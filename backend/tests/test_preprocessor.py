"""Universal file preprocessor: every supported format, telemetry-only data, ZIP security, validation."""
import io
import json
import zipfile
from pathlib import Path

import numpy as np
import polars as pl
import pytest

from app.preprocessor import preprocess

CNC_HEADER = "Timestamp,vibration_g,motor_temp_c,spindle_rpm\n"


def cnc_csv(n=120, start_minute=0) -> bytes:
    rng = np.random.default_rng(1)
    rows = [f"2026-10-05T15:{(start_minute + i // 6) % 60:02d}:{(i % 6) * 10:02d}.277Z,{0.4 + rng.normal(0, .02):.4f},"
            f"{48 + rng.normal(0, 1):.2f},{2400 + rng.normal(0, 20):.1f}" for i in range(n)]
    return (CNC_HEADER + "\n".join(rows) + "\n").encode()


def exec_frame(n=300, seed=0) -> pl.DataFrame:
    rng = np.random.default_rng(seed)
    volt = rng.uniform(0.7, 1.1, n)
    fail = rng.uniform(size=n) < np.where(volt < 0.8, 0.7, 0.15)
    return pl.DataFrame({"run_id": [f"r{i}" for i in range(n)], "core_voltage": volt, "mode": rng.choice(["eco", "std", "turbo"], n),
                         "throughput_mbps": rng.normal(500, 40, n), "status": np.where(fail, "fail", "pass")})


def csv_bytes(df: pl.DataFrame, sep=",") -> bytes:
    buf = io.BytesIO()
    df.write_csv(buf, separator=sep)
    return buf.getvalue()


def roles(part) -> dict:
    return {c["name"]: c["role"] for c in part["columns"]}


def zbytes(members: dict) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in members.items():
            z.writestr(name, data)
    return buf.getvalue()


# ---------------------------------------------------------------- 3 + CNC: telemetry-only
@pytest.mark.parametrize("name", ["CNC #1 • Health.csv"])
def test_cnc_health_telemetry(name):
    real = Path.home() / "Downloads" / name
    raw = real.read_bytes() if real.exists() else cnc_csv(356)
    r = preprocess(raw, name)
    assert not r["errors"] and r["file_type"] == "csv"
    p = r["parts"][0]
    assert p["ok"] and p["data_type_label"] == "Industrial telemetry"
    rl = roles(p)
    assert rl["Timestamp"] == "timestamp"
    assert rl["vibration_g"] == rl["motor_temp_c"] == rl["spindle_rpm"] == "telemetry"  # never config params
    assert p["execution"]["available"] is False and p["execution"]["preview"]["mapping"]["outcome"] is None
    assert p["telemetry"]["available"] is True
    assert any("telemetry-only" in i["message"] for i in p["issues"])
    units = {c["name"]: c["unit"] for c in p["columns"]}
    assert units["vibration_g"] == "g" and units["motor_temp_c"] == "°C" and units["spindle_rpm"] == "RPM"


def test_cnc_telemetry_ingest_does_not_touch_execution_dataset(client, h):
    before = client.get("/api/dataset/status", headers=h).json()
    pv = client.post("/api/upload/preview", files={"file": ("CNC #1 • Health.csv", cnc_csv(200))}, headers=h)
    assert pv.status_code == 200, pv.text
    body = pv.json()
    # execution ingest must refuse (no fabricated outcome)
    bad = client.post("/api/upload/ingest", data={"upload_id": body["upload_id"], "part_id": "main", "mode": "execution"}, headers=h)
    assert bad.status_code == 422 and "telemetry-only" in bad.json()["detail"]
    r = client.post("/api/upload/ingest", data={"upload_id": body["upload_id"], "part_id": "main", "mode": "telemetry"}, headers=h)
    assert r.status_code == 200, r.text
    t = r.json()["telemetry"]
    assert t["active"] and t["meta"]["outcome"] is None and t["meta"]["rows"] == 200
    assert {c["original"] for c in t["meta"]["channels"]} == {"vibration_g", "motor_temp_c", "spindle_rpm"}
    assert "failed" not in t["summary"]["series"]["channels"] and "outcome" not in t["summary"]["series"]["channels"]
    assert all(u["message"] == "Required field not available for this analysis." for u in t["summary"]["unavailable"])
    assert t["meta"]["preprocessing_version"] and t["meta"]["original_filename"] == "CNC #1 • Health.csv"
    after = client.get("/api/dataset/status", headers=h).json()
    assert after["source"] == before["source"] and after.get("rows") == before.get("rows")
    assert client.get("/api/telemetry/status", headers=h).json()["active"]
    assert client.post("/api/telemetry/reset", headers=h).json() == {"active": False}


# ---------------------------------------------------------------- 1, 2: existing CSVs keep working
def test_benchmark_and_execution_csv_through_new_endpoint(client, h):
    sample = client.get("/api/download-sample-csv", headers=h).content
    legacy = client.post("/api/upload-csv/preview", files={"file": ("sample.csv", sample)}, headers=h).json()
    pv = client.post("/api/upload/preview", files={"file": ("sample.csv", sample)}, headers=h).json()
    p = pv["parts"][0]
    assert p["data_type"] == "execution_log" and p["execution"]["available"]
    # the mapping shown in the existing dialog is identical to the legacy endpoint's
    assert p["execution"]["preview"]["mapping"] == legacy["mapping"]
    assert p["execution"]["preview"]["columns"] == legacy["columns"]
    r = client.post("/api/upload/ingest", data={"upload_id": pv["upload_id"], "mode": "execution",
                                                "mapping": json.dumps(p["execution"]["preview"]["mapping"])}, headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["summary"]["rows"] == 1000 and r.json()["dataset"]["source"] == "uploaded"
    assert client.get("/api/overview", headers=h).status_code == 200
    assert client.post("/api/dataset/reset", headers=h).json()["source"] == "benchmark"


def test_benchmark_generated_csv_detected_as_execution():
    from app.store import store
    raw = csv_bytes(store.df.head(500).drop([c for c in store.df.columns if store.df[c].dtype == pl.List(pl.Utf8)], strict=False))
    p = preprocess(raw, "benchmark.csv")["parts"][0]
    assert p["execution"]["available"] and p["data_type"] == "execution_log"


# ---------------------------------------------------------------- 4, 5: names and order
def test_different_names_and_order():
    df = exec_frame().rename({"status": "Verdict", "run_id": "Test ID", "core_voltage": "Core Voltage"})
    df = df.select(list(reversed(df.columns)))
    p = preprocess(csv_bytes(df), "renamed.csv")["parts"][0]
    m = p["execution"]["preview"]["mapping"]
    assert m["outcome"] == "verdict" and m["fail_values"] == ["fail"]
    assert roles(p)["Verdict"] == "outcome"


# ---------------------------------------------------------------- 6, 7: TXT / LOG
def test_txt_tab_delimited():
    p = preprocess(csv_bytes(exec_frame(), sep="\t"), "runs.txt")
    assert p["file_type"] == "txt" and p["parts"][0]["parser_details"]["delimiter"] == "tab"
    assert p["parts"][0]["execution"]["available"]


def test_structured_log():
    lines = [f"2026-10-05 12:00:{i % 60:02d} {'ERROR' if i % 7 == 0 else 'INFO'} job=run{i} temp_c={40 + i % 5} rpm={2000 + i} msg=ok"
             for i in range(80)]
    lines.insert(5, "    at stack.frame(line 12)")  # continuation line stays with its event
    p = preprocess("\n".join(lines).encode(), "machine.log")
    part = p["parts"][0]
    assert p["file_type"] == "log" and part["parser"] == "log" and part["rows"] == 80
    rl = roles(part)
    assert rl["timestamp"] == "timestamp" and rl["level"] == "level" and rl["temp_c"] == "telemetry"


# ---------------------------------------------------------------- 8, 9: JSON
def test_json_array_and_nested():
    recs = exec_frame(120).to_dicts()
    a = preprocess(json.dumps(recs).encode(), "a.json")["parts"][0]
    assert a["rows"] == 120 and a["execution"]["available"]
    nested = {"source": "rig-7", "results": [{"id": r["run_id"], "run": {"status": r["status"], "metrics": {"throughput_mbps": r["throughput_mbps"]}},
                                              "config": {"core_voltage": r["core_voltage"], "mode": r["mode"]}} for r in recs]}
    b = preprocess(json.dumps(nested).encode(), "b.json")["parts"][0]
    assert b["parser_details"]["root"] == "object.results" and b["parser_details"]["document_metadata"] == {"source": "rig-7"}
    assert {"run.status", "run.metrics.throughput_mbps", "config.core_voltage"} <= set(roles(b))
    assert b["execution"]["available"]


# ---------------------------------------------------------------- 10, 11: Excel
def test_xlsx_and_xls():
    buf = io.BytesIO()
    pl.read_csv(cnc_csv(60)).write_excel(buf, worksheet="Health")
    r = preprocess(buf.getvalue(), "cnc.xlsx")
    assert r["file_type"] == "xlsx" and r["sheets"][0]["name"] == "Health"
    assert roles(r["parts"][0])["spindle_rpm"] == "telemetry" and r["parts"][0]["telemetry"]["available"]
    xlwt = pytest.importorskip("xlwt")
    wb = xlwt.Workbook()
    ws = wb.add_sheet("Runs")
    df = exec_frame(80)
    for j, c in enumerate(df.columns):
        ws.write(0, j, c)
        for i, v in enumerate(df[c].to_list()):
            ws.write(i + 1, j, v)
    b2 = io.BytesIO()
    wb.save(b2)
    r2 = preprocess(b2.getvalue(), "runs.xls")
    assert r2["file_type"] == "xls" and r2["parts"][0]["rows"] == 80 and r2["parts"][0]["execution"]["available"]
    # typo extension accepted only when the bytes are really a workbook
    assert preprocess(buf.getvalue(), "cnc.xlsv")["file_type"] == "xlsx"
    assert preprocess(b"a,b\n1,2\n", "x.xlsv")["errors"][0].startswith("Unsupported file type")


# ---------------------------------------------------------------- 12, 13, 14: ZIP
def test_zip_multiple_compatible():
    r = preprocess(zbytes({"cnc1.csv": cnc_csv(50), "cnc2.csv": cnc_csv(50, 30)}), "plant.zip")
    assert r["file_type"] == "zip" and r["archive"]["combinable"] and r["default_part"] == "combined"
    comb = next(p for p in r["parts"] if p["part_id"] == "combined")
    assert comb["rows"] == 100 and "source_file" in roles(comb)


def test_zip_mixed_and_incompatible():
    r = preprocess(zbytes({"a.csv": cnc_csv(40), "runs.json": json.dumps(exec_frame(60).to_dicts()), "readme.pdf": b"%PDF-1.4",
                           "tool.exe": b"MZ"}), "mixed.zip")
    st = {f["name"]: f["status"] for f in r["archive"]["files"]}
    assert st["a.csv"] == st["runs.json"] == "supported"
    assert st["readme.pdf"] == st["tool.exe"] == "ignored"  # listed, never silently dropped
    assert r["archive"]["combinable"] is False and "different columns" in r["archive"]["combine_reason"]
    assert {p["part_id"] for p in r["parts"]} == {"p0", "p1"}


def test_zip_path_traversal_rejected(tmp_path):
    r = preprocess(zbytes({"../../evil.csv": cnc_csv(30), "/abs/x.csv": cnc_csv(30), "C:/win.csv": cnc_csv(30), "ok.csv": cnc_csv(30)}), "evil.zip")
    st = {f["name"]: (f["status"], f["reason"]) for f in r["archive"]["files"]}
    for bad in ("../../evil.csv", "/abs/x.csv", "C:/win.csv"):
        assert st[bad][0] == "rejected" and "zip-slip" in st[bad][1]
    assert st["ok.csv"][0] == "supported" and len(r["parts"]) == 1
    assert not (tmp_path.parent / "evil.csv").exists()


def test_zip_bomb_ratio_rejected():
    r = preprocess(zbytes({"big.csv": b"a,b\n" + b"1,2\n" * 2_000_000, "ok.csv": cnc_csv(30)}), "bomb.zip")
    st = {f["name"]: f for f in r["archive"]["files"]}
    assert st["big.csv"]["status"] == "rejected" and "compression ratio" in st["big.csv"]["reason"]


# ---------------------------------------------------------------- 15, 16: empty / malformed / unsupported
def test_empty_malformed_unsupported(client, h):
    assert preprocess(b"", "e.csv")["errors"] == ["The file is empty."]
    assert preprocess(b"{not json", "bad.json")["errors"]
    assert "no tabular" in preprocess(b"hello world\njust prose here\n", "notes.txt")["errors"][0].lower()
    assert preprocess(b"PK\x03\x04garbage", "c.zip")["errors"]
    r = client.post("/api/upload/preview", files={"file": ("x.pdf", b"%PDF")}, headers=h)
    assert r.status_code == 415 and r.json()["detail"] == "Unsupported file type (.pdf). Supported: CSV, TXT, LOG, JSON, XLS, XLSX, ZIP."
    r = client.post("/api/upload/preview", files={"file": ("e.csv", b"")}, headers=h)
    assert r.status_code == 400 and "Traceback" not in r.text
    # legacy endpoint is untouched: still CSV-only
    assert client.post("/api/upload-csv/preview", files={"file": ("x.json", b"[]")}, headers=h).status_code == 400


# ---------------------------------------------------------------- 17-23: validation cases
def test_missing_timestamp_and_outcome():
    df = pl.DataFrame({"vibration_g": np.linspace(0.1, 0.5, 60), "motor_temp_c": np.linspace(40, 50, 60)})
    p = preprocess(csv_bytes(df), "no_ts.csv")["parts"][0]
    msgs = " ".join(i["message"] for i in p["issues"])
    assert "No timestamp column detected" in msgs and "PASS/FAIL outcome is not present" in msgs
    assert p["data_type"] == "measurement_table" and p["telemetry"]["available"] and not p["execution"]["available"]


def test_missing_optional_fields_still_execution():
    df = exec_frame().select(["core_voltage", "mode", "status"])
    p = preprocess(csv_bytes(df), "minimal.csv")["parts"][0]
    m = p["execution"]["preview"]["mapping"]
    assert p["execution"]["available"] and m["run_id"] is None and m["timestamp"] is None


def test_invalid_numeric_and_missing_values():
    vals = [str(v) for v in np.linspace(1, 2, 100)]
    vals[3], vals[7] = "n/a?", "oops"
    temps = [str(40 + i % 3) for i in range(100)]
    temps[10] = ""
    raw = ("ts,pressure_kpa,motor_temp_c\n" + "\n".join(f"2026-01-01 00:{i // 60:02d}:{i % 60:02d},{v},{t}" for i, (v, t) in enumerate(zip(vals, temps)))).encode()
    p = preprocess(raw, "bad_nums.csv")["parts"][0]
    msgs = [i["message"] for i in p["issues"]]
    assert "Column pressure_kpa contains 2 non-numeric values." in msgs
    assert any(m.startswith("Column motor_temp_c has 1 missing value") for m in msgs)
    assert roles(p)["pressure_kpa"] == "telemetry"


def test_invalid_timestamps_reported():
    ts = [f"2026-01-01 00:00:{i:02d}" if i % 5 else "not a date" for i in range(60)]
    raw = ("timestamp,rpm\n" + "\n".join(f"{t},{1000 + i}" for i, t in enumerate(ts))).encode()
    p = preprocess(raw, "ts.csv")["parts"][0]
    assert any("20% of rows have invalid timestamps" in i["message"] for i in p["issues"])


def test_duplicate_columns_renamed_not_dropped():
    raw = ("time,temp_c,temp_c,rpm\n" + "\n".join(f"2026-01-01 00:00:{i:02d},{40 + i % 3},{41 + i % 2},{900 + i}" for i in range(40))).encode()
    p = preprocess(raw, "dup.csv")["parts"][0]
    assert {"temp_c", "temp_c_2"} <= set(roles(p))
    assert any("Duplicate column 'temp_c'" in i["message"] for i in p["issues"])


def test_very_small_dataset_warned_not_rejected():
    p = preprocess(cnc_csv(5), "tiny.csv")["parts"][0]
    assert p["ok"] and any("Very small dataset (5 rows)" in i["message"] for i in p["issues"])


def test_multiple_timestamp_candidates():
    raw = ("start_time,end_time,rpm\n" + "\n".join(f"2026-01-01 00:00:{i:02d},2026-01-01 00:01:{i:02d},{900 + i}" for i in range(40))).encode()
    p = preprocess(raw, "two_ts.csv")["parts"][0]
    assert any("Multiple columns could represent timestamp" in i["message"] for i in p["issues"])


def test_fahrenheit_converted_and_unknown_units_flagged():
    from app.preprocessor.normalizer import normalize_telemetry
    df = pl.DataFrame({"ts": ["2026-01-01 00:00:00", "2026-01-01 00:00:10"], "oil_temp_f": [212.0, 32.0], "spindle_load": [0.5, 0.6]})
    frame, meta = normalize_telemetry(df, "ts", {"oil_temp_f": "telemetry", "spindle_load": "telemetry"}, {})
    assert frame["oil_temp_c"].to_list() == [100.0, 0.0]
    assert {c["original"]: c["unit"] for c in meta["channels"]}["spindle_load"] == "unit unknown"
