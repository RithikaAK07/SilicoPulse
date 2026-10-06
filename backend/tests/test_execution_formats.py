"""Execution-data format independence: differently formatted execution files must reach the EXISTING
pipeline (upload_handler.canonicalize) as the same canonical execution data; ambiguity is never guessed."""
import io
import json

import numpy as np
import polars as pl
import pytest

from app import upload_handler as uh
from app.preprocessor import execution_frame, load_part, preprocess

N = 200
rng = np.random.default_rng(42)
TEMP = rng.normal(60, 8, N).round(1)
RPM = rng.choice([1200, 1800, 2400, 3000], N)
FAIL = rng.uniform(size=N) < np.where(TEMP > 65, 0.6, 0.15)
TIMES = [f"2026-03-{1 + i // 24:02d} {i % 24:02d}:15:00" for i in range(N)]


def csv(df: pl.DataFrame, sep=",") -> bytes:
    buf = io.BytesIO()
    df.write_csv(buf, separator=sep)
    return buf.getvalue()


def part(raw: bytes, name: str) -> dict:
    r = preprocess(raw, name)
    assert not r["errors"], r["errors"]
    return r["parts"][0]


def canon(raw: bytes, name: str, user_map: dict | None = None):
    """The exact path /api/upload/ingest uses: preprocess -> adapter -> existing canonicalize."""
    table, member = load_part(raw, name, "main")
    df, det, report = execution_frame(table, member)
    m = {**det["mapping"], **(user_map or {})}
    frame, meta = uh.canonicalize(df, m, name)
    return frame, meta, det, report


def ex1():
    return pl.DataFrame({"time": TIMES, "temperature": TEMP, "rpm": RPM, "result": np.where(FAIL, "FAIL", "PASS")})


def ex2():
    return pl.DataFrame({"event_time": TIMES, "motor_temperature": TEMP, "spindle_speed": RPM, "status": np.where(FAIL, "Failed", "Passed")})


def ex3():
    return pl.DataFrame({"status": np.where(FAIL, "fail", "pass"), "rpm_value": RPM, "date_time": TIMES, "temp": TEMP})


# ------------------------------------------------------------------ examples 1-3: same canonical result
def test_examples_map_to_the_same_canonical_execution_data():
    outs = [canon(csv(f()), f"{f.__name__}.csv") for f in (ex1, ex2, ex3)]
    truth = FAIL.astype(int)
    for frame, meta, det, _ in outs:
        assert frame["failed"].to_list() == truth.tolist()
        assert "timestamp" not in meta["synthetic_columns"]  # real timestamps reached the pipeline
        assert frame["timestamp"].dt.strftime("%Y-%m-%d %H:%M:%S").to_list() == TIMES
        assert len(meta["config_params"]) == 1 and len(meta["random_vars"]) == 1
        assert "throughput_mbps" in meta["synthetic_columns"]  # rpm / spindle speed is NOT a performance metric
    # every variant: rotational speed -> configuration, temperature -> randomized variable, keeping the user's names
    cfg = [m["config_params"][0] for _, m, _, _ in outs]
    rnd = [m["random_vars"][0] for _, m, _, _ in outs]
    assert cfg == ["rpm", "spindle_speed", "rpm_value"] and rnd == ["temperature", "motor_temperature", "temp"]
    a, b, c = (o[0] for o in outs)
    assert a["rpm"].to_list() == b["spindle_speed"].to_list() == c["rpm_value"].to_list()
    assert a["temperature"].to_list() == b["motor_temperature"].to_list() == c["temp"].to_list()


def test_correct_format_file_is_unchanged_by_the_adapter(client, h):
    """A file already in SilicoPulse format reaches canonicalize exactly as through the legacy endpoint."""
    raw = client.get("/api/download-sample-csv", headers=h).content
    legacy_df, _ = uh.sanitize(uh.read_csv(raw))
    legacy_det = uh.detect(legacy_df)
    legacy_frame, legacy_meta = uh.canonicalize(legacy_df, legacy_det["mapping"], "s.csv")
    frame, meta, det, report = canon(raw, "s.csv")
    assert det["mapping"] == legacy_det["mapping"] and report["confirmations"] == []
    assert frame.equals(legacy_frame)
    for k in ("config_params", "random_vars", "telemetry", "synthetic_columns", "leakage_dropped"):
        assert meta[k] == legacy_meta[k], k


# ------------------------------------------------------------------ example 4: PASS/FAIL representations
@pytest.mark.parametrize("col,pass_v,fail_v,expected", [
    ("result", "PASS", "FAIL", None), ("result", "pass", "fail", None), ("status", "Passed", "Failed", None),
    ("result", "SUCCESS", "ERROR", None), ("failed", 0, 1, ["1"]), ("passed", "true", "false", ["false"]),
    ("is_fail", "False", "True", ["true"]), ("success", "yes", "no", ["no"]),
])
def test_reliable_outcome_representations(col, fail_v, pass_v, expected):
    df = pl.DataFrame({"time": TIMES, "temperature": TEMP, "rpm": RPM, col: np.where(FAIL, fail_v, pass_v)})
    p = part(csv(df), "o.csv")
    m = p["execution"]["preview"]["mapping"]
    assert m["outcome"] == col and p["execution"]["confirmations"] == []
    if expected:
        assert m["fail_values"] == expected
    frame, *_ = canon(csv(df), "o.csv")
    assert frame["failed"].to_list() == FAIL.astype(int).tolist()


@pytest.mark.parametrize("fail_v,pass_v", [(1, 0), ("true", "false"), ("yes", "no"), ("Y", "N")])
def test_ambiguous_outcome_requires_confirmation(client, h, fail_v, pass_v):
    df = pl.DataFrame({"time": TIMES, "temperature": TEMP, "rpm": RPM, "result": np.where(FAIL, fail_v, pass_v)})
    p = part(csv(df), "amb.csv")
    m = p["execution"]["preview"]["mapping"]
    assert m["outcome"] == "result" and m["fail_values"] == []  # nothing guessed
    conf = p["execution"]["confirmations"]
    assert conf and conf[0]["field"] == "outcome" and conf[0]["required"]
    assert any("Confirmation needed" in i["message"] for i in p["issues"])
    assert p["execution"]["available"] and p["data_type"] == "execution_log"  # still an execution file, not telemetry


def test_ambiguous_outcome_ingest_blocked_until_confirmed(client, h):
    df = pl.DataFrame({"time": TIMES, "temperature": TEMP, "rpm": RPM, "result": np.where(FAIL, 1, 0)})
    pv = client.post("/api/upload/preview", files={"file": ("amb.csv", csv(df))}, headers=h).json()
    m = pv["parts"][0]["execution"]["preview"]["mapping"]
    # a client that sends a guessed polarity without confirming is refused
    r = client.post("/api/upload/ingest", data={"upload_id": pv["upload_id"], "mode": "execution",
                                                "mapping": json.dumps({**m, "fail_values": ["1"]})}, headers=h)
    assert r.status_code == 422 and "Please confirm" in r.json()["detail"]
    r = client.post("/api/upload/ingest", data={"upload_id": pv["upload_id"], "mode": "execution",
                                                "mapping": json.dumps({**m, "fail_values": ["1"], "confirmed": ["outcome"]})}, headers=h)
    assert r.status_code == 200, r.text
    from app.store import store
    assert store.df["failed"].to_list() == FAIL.astype(int).tolist()
    assert store.meta["preprocessing"]["confirmed"] == ["outcome"]
    assert client.post("/api/dataset/reset", headers=h).json()["source"] == "benchmark"


def test_extra_unknown_outcome_values_require_confirmation():
    vals = np.where(FAIL, "fail", "pass").astype(object)
    vals[::17] = "skipped"
    df = pl.DataFrame({"time": TIMES, "rpm": RPM, "temperature": TEMP, "status": vals.tolist()})
    p = part(csv(df), "skip.csv")
    assert p["execution"]["preview"]["mapping"]["fail_values"] == ["fail"]
    assert p["execution"]["confirmations"][0]["field"] == "outcome" and "skipped" in p["execution"]["confirmations"][0]["message"]


# ------------------------------------------------------------------ separators, decimals, dates, units, extra columns
def test_semicolon_decimal_comma_and_european_dates():
    eu_times = [f"{1 + i // 24:02d}.03.2026 {i % 24:02d}:15" for i in range(N)]
    df = pl.DataFrame({"Zeit": eu_times, "Temperatur": [f"{t:.1f}".replace(".", ",") for t in TEMP], "RPM": RPM,
                       "Ergebnis": np.where(FAIL, "FAIL", "PASS")})
    frame, meta, det, report = canon(csv(df, sep=";"), "eu.csv")
    m = det["mapping"]
    assert m["outcome"] == "ergebnis" and m["timestamp"] == "zeit"
    assert frame["temperatur"].to_list() == pytest.approx(TEMP.tolist())  # 61,3 -> 61.3 (not 613)
    assert "timestamp" not in meta["synthetic_columns"]
    assert frame["timestamp"].dt.strftime("%Y-%m-%d %H:%M").to_list() == [t.replace(":00", "", 1)[:16] for t in TIMES]
    assert frame["failed"].to_list() == FAIL.astype(int).tolist()
    assert any("decimal comma" in t["transform"] for t in report["transforms"])


def test_units_in_values_index_and_irrelevant_columns():
    tp = rng.normal(500, 30, N).round(1)
    df = pl.DataFrame({"Unnamed: 0": np.arange(N), "Run": [f"r{i}" for i in range(N)], "Timestamp": TIMES,
                       "Throughput": [f"{v} MB/s" for v in tp], "Block Size": rng.choice(["4K", "64K", "128K"], N),
                       "Operator Notes": [f"note {i}: looked fine" for i in range(N)], "Verdict": np.where(FAIL, "Failed", "Passed")})
    p = part(csv(df), "units.csv")
    assert {c["name"]: c["unit"] for c in p["columns"]}["Throughput"] == "MB/s"
    frame, meta, det, report = canon(csv(df), "units.csv")
    m = det["mapping"]
    assert m["performance"] == "throughput" and frame["throughput_mbps"].to_list() == pytest.approx(tp.tolist())
    assert m["roles"]["unnamed_0"] == "ignore" and m["roles"]["operator_notes"] == "ignore"
    assert meta["config_params"] == ["block_size"]  # "4K/64K/128K" stays a configuration choice, not a number
    assert any("row index" in t["transform"] for t in report["transforms"])


def test_day_month_ambiguous_dates_require_confirmation():
    amb = [f"{(i % 12) + 1:02d}/{(i // 12) % 12 + 1:02d}/2026 10:00:00" for i in range(N)]
    df = pl.DataFrame({"date": amb, "rpm": RPM, "temperature": TEMP, "status": np.where(FAIL, "fail", "pass")})
    p = part(csv(df), "dates.csv")
    conf = p["execution"]["confirmations"]
    assert any(c["field"] == "timestamp" for c in conf)


def test_json_and_excel_execution_with_native_types():
    recs = [{"when": t, "settings": {"rpm": int(r)}, "temp_c": float(tc), "passed": not f} for t, r, tc, f in zip(TIMES, RPM, TEMP, FAIL)]
    frame, meta, det, report = canon(json.dumps({"runs": recs}).encode(), "runs.json")
    assert det["mapping"]["outcome"] == "passed" and det["mapping"]["fail_values"] == ["false"] and not report["confirmations"]
    assert frame["failed"].to_list() == FAIL.astype(int).tolist()
    buf = io.BytesIO()
    pl.DataFrame({"Status": np.where(FAIL, "FAIL", "PASS"), "Spindle Speed": RPM, "Motor Temperature": TEMP,
                  "Event Time": pl.Series(TIMES).str.to_datetime()}).write_excel(buf)
    frame, meta, det, report = canon(buf.getvalue(), "runs.xlsx")
    assert frame["failed"].to_list() == FAIL.astype(int).tolist() and "timestamp" not in meta["synthetic_columns"]
    assert meta["config_params"] == ["spindle_speed"] and meta["random_vars"] == ["motor_temperature"]


def test_genuinely_missing_outcome_is_not_invented():
    df = pl.DataFrame({"time": TIMES, "temperature": TEMP, "rpm": RPM})
    p = part(csv(df), "no_outcome.csv")
    assert not p["execution"]["available"] and p["execution"]["preview"]["mapping"]["outcome"] is None
    assert p["telemetry"]["available"]


# ------------------------------------------------------------------ more layouts from the specification
def test_uppercase_generic_speed_example():
    """RESULT | TEMP_C | SPEED | EVENT_TIME: a SPEED column holding a few fixed settings is a configuration
    parameter (not the performance metric), so the file reaches the pipeline like examples 1-3."""
    iso = [t.replace(" ", "T") + "Z" for t in TIMES]
    df = pl.DataFrame({"RESULT": np.where(FAIL, "FAIL", "PASS"), "TEMP_C": TEMP, "SPEED": RPM, "EVENT_TIME": iso})
    frame, meta, det, report = canon(csv(df), "ex4.csv")
    assert det["mapping"]["outcome"] == "result" and det["mapping"]["timestamp"] == "event_time"
    assert det["mapping"]["performance"] is None and meta["config_params"] == ["speed"] and meta["random_vars"] == ["temp_c"]
    assert frame["failed"].to_list() == FAIL.astype(int).tolist() and "timestamp" not in meta["synthetic_columns"]
    assert any("configuration setting" in t["transform"] for t in report["transforms"])


def test_continuous_speed_stays_performance_metric():
    sp = rng.normal(500, 40, N)
    df = pl.DataFrame({"time": TIMES, "mode": rng.choice(["a", "b", "c"], N), "speed": sp, "status": np.where(FAIL, "fail", "pass")})
    frame, meta, det, _ = canon(csv(df), "speed.csv")
    assert det["mapping"]["performance"] == "speed" and frame["throughput_mbps"].to_list() == pytest.approx(sp.tolist())
    assert "throughput_mbps" not in meta["synthetic_columns"]


def test_success_failure_words():
    df = pl.DataFrame({"time": TIMES, "temperature": TEMP, "rpm": RPM, "outcome": np.where(FAIL, "FAILURE", "SUCCESS")})
    frame, _, det, report = canon(csv(df), "sf.csv")
    assert det["mapping"]["fail_values"] == ["failure"] and not report["confirmations"]
    assert frame["failed"].to_list() == FAIL.astype(int).tolist()


def test_log_file_execution_keeps_log_text():
    lines = [f"{t} {'ERROR' if f else 'INFO'} run=r{i} temp_c={tc} rpm={r} result={'FAIL' if f else 'PASS'} msg=\"{'CRC mismatch' if f else 'ok'}\""
             for i, (t, tc, r, f) in enumerate(zip(TIMES, TEMP, RPM, FAIL))]
    frame, meta, det, _ = canon("\n".join(lines).encode(), "runs.log")
    m = det["mapping"]
    assert m["outcome"] == "result" and m["timestamp"] == "timestamp" and m["log"] == "message"
    assert frame["failed"].to_list() == FAIL.astype(int).tolist()
    assert meta["config_params"] == ["rpm"] and "temp_c" in meta["random_vars"]
    assert "CRC mismatch" in frame.filter(pl.col("failed") == 1)["log_trace"][0]  # raw log text preserved for Root Cause & Logs


def test_workbook_default_is_the_data_sheet():
    import xlsxwriter
    buf = io.BytesIO()
    wb = xlsxwriter.Workbook(buf)
    wb.add_worksheet("Summary").write_row(0, 0, ["Report", "Owner"])
    wb.get_worksheet_by_name("Summary").write_row(1, 0, ["Q1", "lab-3"])
    ws = wb.add_worksheet("Runs")
    ws.write_row(0, 0, ["Status", "Spindle Speed", "Motor Temperature", "Event Time"])
    for i in range(N):
        ws.write_row(i + 1, 0, ["FAIL" if FAIL[i] else "PASS", int(RPM[i]), float(TEMP[i]), TIMES[i]])
    wb.add_worksheet("Empty")
    wb.close()
    r = preprocess(buf.getvalue(), "book.xlsx")
    default = next(p for p in r["parts"] if p["part_id"] == r["default_part"])
    assert default["label"].endswith("Runs") and default["execution"]["available"]
    assert {s["name"]: s["status"] for s in r["sheets"]}["Empty"].startswith("empty")


def test_telemetry_dataset_persists_across_restart(client, h):
    cnc = ("Timestamp,vibration_g,motor_temp_c,spindle_rpm\n" + "\n".join(
        f"2026-10-05T15:{i // 6:02d}:{(i % 6) * 10:02d}.277Z,{0.4 + (i % 7) / 100},{48 + (i % 5) / 2},{2400 + i % 9}" for i in range(120))).encode()
    pv = client.post("/api/upload/preview", files={"file": ("CNC #1 • Health.csv", cnc)}, headers=h).json()
    r = client.post("/api/upload/ingest", data={"upload_id": pv["upload_id"], "mode": "telemetry"}, headers=h)
    assert r.status_code == 200 and r.json()["telemetry"]["active"]
    from app.telemetry import TelemetryStore
    reloaded = TelemetryStore().status()  # what a restarted server / refreshed page sees
    assert reloaded["active"] and reloaded["meta"]["rows"] == 120 and reloaded["meta"]["original_filename"] == "CNC #1 • Health.csv"
    assert [c["original"] for c in reloaded["meta"]["channels"]] == ["vibration_g", "motor_temp_c", "spindle_rpm"]
    assert reloaded["meta"]["outcome"] is None
    client.post("/api/telemetry/reset", headers=h)


def test_zip_combined_execution_files_do_not_learn_the_file_name():
    import zipfile
    half = N // 2
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for k in (0, 1):
            sl = slice(k * half, (k + 1) * half)
            z.writestr(f"runs_{k}.csv", csv(pl.DataFrame({"time": TIMES[sl], "temperature": TEMP[sl], "rpm": RPM[sl],
                                                          "result": np.where(FAIL[sl], "FAIL", "PASS")})))
    raw = buf.getvalue()
    r = preprocess(raw, "runs.zip")
    assert r["default_part"] == "combined"
    table, member = load_part(raw, "runs.zip", "combined")
    df, det, report = execution_frame(table, member)
    assert det["mapping"]["roles"]["source_file"] == "ignore"
    frame, meta = uh.canonicalize(df, det["mapping"], "runs.zip")
    assert "source_file" not in meta["config_params"] and meta["config_params"] == ["rpm"]
    assert frame["failed"].to_list() == FAIL.astype(int).tolist()


@pytest.mark.parametrize("suffix", ["Z", "+00:00", "+02:00"])
def test_timezone_timestamps_reach_the_pipeline_as_naive_utc(suffix):
    ts = [t.replace(" ", "T") + suffix for t in TIMES]
    df = pl.DataFrame({"time": ts, "temperature": TEMP, "rpm": RPM, "result": np.where(FAIL, "FAIL", "PASS")})
    frame, meta, _, _ = canon(csv(df), "tz.csv")
    assert frame.schema["timestamp"] == pl.Datetime("ms") and "timestamp" not in meta["synthetic_columns"]
    from datetime import datetime, timedelta
    shift = {"Z": 0, "+00:00": 0, "+02:00": -2}[suffix]  # same instants, expressed in UTC
    expect = [datetime.strptime(t, "%Y-%m-%d %H:%M:%S") + timedelta(hours=shift) for t in TIMES]
    assert frame["timestamp"].to_list() == expect
