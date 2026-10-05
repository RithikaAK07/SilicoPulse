"""Synthetic execution-log & telemetry dataset generator.

Produces N execution runs with:
  * 100+ configuration parameters (12 named storage/firmware knobs + generic flags)
  * 50+ randomized variables (seed, traffic, timing jitter, temperature, voltage, noise)
  * environment / hardware / workload context and timestamps over 90 days
  * resource telemetry (throughput, IOPS, p99 latency, CPU, memory, retries)
  * pass/fail outcome, error signature and a multi-line log trace

Failures are produced by a hidden ground-truth model that mixes deterministic
config interactions (always fail) with stochastic drivers (seed, thermal drift,
jitter), so every analytics view has a real signal to discover.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta

import numpy as np
import polars as pl

from .config import META_PATH, RUNS_PATH

# name, kind, choices, description
KEY_PARAMS: list[dict] = [
    {"name": "queue_depth", "kind": "int", "choices": [1, 2, 4, 8, 16, 32, 64, 128, 256], "desc": "NVMe submission queue depth"},
    {"name": "block_size_kb", "kind": "int", "choices": [4, 8, 16, 32, 64, 128, 256, 512], "desc": "I/O block size (KB)"},
    {"name": "write_cache", "kind": "bool", "choices": [0, 1], "desc": "Volatile write cache enabled"},
    {"name": "gc_policy", "kind": "cat", "choices": ["lazy", "balanced", "aggressive"], "desc": "Garbage collection policy"},
    {"name": "thread_count", "kind": "int", "choices": [1, 2, 4, 8, 16, 32, 64], "desc": "Host I/O worker threads"},
    {"name": "io_scheduler", "kind": "cat", "choices": ["none", "mq-deadline", "bfq", "kyber"], "desc": "Linux I/O scheduler"},
    {"name": "compression", "kind": "cat", "choices": ["off", "lz4", "zstd"], "desc": "Inline compression codec"},
    {"name": "prefetch_depth", "kind": "int", "choices": list(range(0, 17, 2)), "desc": "Read-ahead prefetch depth"},
    {"name": "power_mode", "kind": "cat", "choices": ["performance", "balanced", "powersave"], "desc": "Controller power state policy"},
    {"name": "ecc_level", "kind": "cat", "choices": ["standard", "enhanced", "ldpc_max"], "desc": "Error-correction strength"},
    {"name": "retry_limit", "kind": "int", "choices": list(range(0, 9)), "desc": "Command retry limit"},
    {"name": "over_provisioning_pct", "kind": "int", "choices": [7, 12, 20, 28], "desc": "Over-provisioned capacity (%)"},
]
SANDBOX_PARAMS = [p["name"] for p in KEY_PARAMS[:10]]

ENVIRONMENTS = ["lab", "staging", "production"]
HARDWARE = ["Gen5-NVMe", "Gen4-NVMe", "UFS-4.0", "eMMC-5.1"]
WORKLOADS = ["seq_write", "rand_read", "mixed_70_30", "db_oltp"]
NAMED_RANDOM = ["seed", "traffic_intensity", "timing_jitter_us", "temperature_c", "voltage_droop_mv"]
TELEMETRY = ["throughput_mbps", "iops", "latency_p99_ms", "cpu_util", "mem_util", "retry_count", "instability_index"]

SIGNATURES = {
    "CMD_QUEUE_TIMEOUT": "Command queue saturated without write-cache absorption",
    "GC_STALL_WATCHDOG": "Aggressive GC on small blocks starves host I/O",
    "DMA_BUFFER_OVERRUN": "zstd compression under high thread fan-out overruns DMA buffers",
    "THERMAL_THROTTLE_ABORT": "Junction temperature crossed throttle threshold",
    "TIMING_RACE_CRC_MISMATCH": "Timing jitter exposes a race on the CRC path",
    "FW_ASSERT_0x3F": "Seed-dependent firmware assertion under load",
    "ECC_UNCORRECTABLE": "Uncorrectable ECC event (low over-provisioning / weak ECC)",
}


def _sigmoid(x):
    return 1.0 / (1.0 + np.exp(-x))


def generate_dataset(n_runs: int = 10_000, n_config: int = 100, n_random: int = 51, seed: int = 42, persist: bool = True) -> pl.DataFrame:
    n_config = max(n_config, len(KEY_PARAMS) + 1)
    n_random = max(n_random, len(NAMED_RANDOM) + 1)
    rng = np.random.default_rng(seed)

    # ---- configuration profiles: a fixed pool so each profile repeats across seeds
    n_profiles = max(60, n_runs // 25)
    profiles: dict[str, np.ndarray] = {}
    for p in KEY_PARAMS:
        profiles[p["name"]] = rng.choice(np.array(p["choices"], dtype=object), size=n_profiles)
    n_generic = n_config - len(KEY_PARAMS)
    generic_names = [f"cfg_flag_{i:03d}" for i in range(len(KEY_PARAMS) + 1, len(KEY_PARAMS) + 1 + n_generic)]
    for i, g in enumerate(generic_names):
        profiles[g] = rng.integers(0, 2 if i % 3 else 5, size=n_profiles)
    weights = 1.0 / np.arange(1, n_profiles + 1) ** 0.35
    weights /= weights.sum()
    prof_idx = rng.choice(n_profiles, size=n_runs, p=weights)

    df: dict[str, np.ndarray] = {"run_id": np.array([f"RUN-{i:06d}" for i in range(n_runs)])}
    df["config_id"] = np.array([f"CFG-{i:04d}" for i in prof_idx])
    for name, arr in profiles.items():
        df[name] = arr[prof_idx]

    # ---- context
    start = datetime(2026, 7, 1)
    day = np.sort(rng.uniform(0, 90, size=n_runs))
    df["timestamp"] = np.array([start + timedelta(days=float(d)) for d in day], dtype="datetime64[ms]")
    df["environment"] = rng.choice(ENVIRONMENTS, size=n_runs, p=[0.45, 0.35, 0.20])
    df["hardware"] = rng.choice(HARDWARE, size=n_runs, p=[0.3, 0.35, 0.2, 0.15])
    df["workload"] = rng.choice(WORKLOADS, size=n_runs)

    # ---- randomized variables (with slow drift over time for temperature & traffic)
    seed_pool = rng.choice(np.arange(1000, 99999), size=64, replace=False)
    unlucky = set(rng.choice(seed_pool, size=6, replace=False).tolist())
    df["seed"] = rng.choice(seed_pool, size=n_runs)
    df["traffic_intensity"] = np.clip(rng.beta(2, 2.5, n_runs) + 0.0025 * day * rng.uniform(0.5, 1.5, n_runs), 0, 1.25)
    df["timing_jitter_us"] = rng.lognormal(2.2, 0.55, n_runs)
    df["temperature_c"] = rng.normal(44, 7, n_runs) + 0.16 * day + 6 * np.sin(day / 9)
    df["voltage_droop_mv"] = np.abs(rng.normal(18, 8, n_runs))
    noise_names = [f"rand_{i:02d}" for i in range(n_random - len(NAMED_RANDOM))]
    for nm in noise_names:
        df[nm] = rng.uniform(0, 1, n_runs).round(4)

    qd = df["queue_depth"].astype(float)
    bs = df["block_size_kb"].astype(float)
    wc = df["write_cache"].astype(int)
    thr = df["thread_count"].astype(float)
    op = df["over_provisioning_pct"].astype(float)
    gc = df["gc_policy"]
    comp = df["compression"]
    ecc = df["ecc_level"]
    pm = df["power_mode"]
    temp = df["temperature_c"]
    jit = df["timing_jitter_us"]
    traffic = df["traffic_intensity"]
    is_unlucky = np.isin(df["seed"], list(unlucky)).astype(float)

    # ---- hidden ground-truth failure model (terms map to error signatures)
    terms = {
        "CMD_QUEUE_TIMEOUT": 4.6 * ((qd >= 128) & (wc == 0)),
        "GC_STALL_WATCHDOG": 3.4 * ((gc == "aggressive") & (bs <= 8)),
        "DMA_BUFFER_OVERRUN": 2.6 * ((comp == "zstd") & (thr >= 32)),
        "THERMAL_THROTTLE_ABORT": 2.2 * np.clip((temp - 60) / 8, 0, 3),
        "TIMING_RACE_CRC_MISMATCH": 1.6 * (jit > 22) + 0.04 * np.clip(jit - 10, 0, 40) * (df["io_scheduler"] == "none"),
        "FW_ASSERT_0x3F": 2.8 * is_unlucky * traffic,
        "ECC_UNCORRECTABLE": 1.1 * (op == 7) + 0.9 * (ecc == "standard") * (df["hardware"] == "eMMC-5.1"),
    }
    z = -3.4 + sum(terms.values())
    z = z - 0.9 * (ecc == "ldpc_max") + 0.6 * ((pm == "powersave") & (df["workload"] == "db_oltp"))
    z = z + 0.35 * (df[generic_names[min(29, n_generic - 1)]] == 1) + 0.7 * (traffic - 0.5) + 0.004 * df["voltage_droop_mv"]
    p_fail = _sigmoid(z)
    failed = rng.uniform(0, 1, n_runs) < p_fail
    df["outcome"] = np.where(failed, "fail", "pass")
    df["failed"] = failed.astype(int)

    term_mat = np.vstack([np.asarray(v, dtype=float) for v in terms.values()]).T + rng.uniform(0, 0.25, (n_runs, len(terms)))
    sig_names = np.array(list(terms.keys()))
    df["error_signature"] = np.where(failed, sig_names[term_mat.argmax(1)], "NONE")

    # ---- telemetry
    hw_base = {"Gen5-NVMe": 7200, "Gen4-NVMe": 5100, "UFS-4.0": 3600, "eMMC-5.1": 320}
    base = np.array([hw_base[h] for h in df["hardware"]], dtype=float)
    f_qd = np.clip(np.log2(qd + 1) / 6.5, 0.15, 1.15)
    f_bs = np.clip(np.log2(bs) / 8.0, 0.25, 1.1)
    f_comp = np.where(comp == "off", 1.0, np.where(comp == "lz4", 1.08, 0.93))
    f_pm = np.where(pm == "performance", 1.0, np.where(pm == "balanced", 0.9, 0.72))
    f_wc = np.where(wc == 1, 1.07, 0.94)
    f_gc = np.where(gc == "lazy", 1.03, np.where(gc == "balanced", 1.0, 0.92))
    f_thr = np.clip(0.6 + 0.12 * np.log2(thr + 1), 0.6, 1.25)
    f_wl = np.array([{"seq_write": 1.0, "rand_read": 0.78, "mixed_70_30": 0.84, "db_oltp": 0.66}[w] for w in df["workload"]])
    thermal = np.clip(1 - 0.02 * np.clip(temp - 55, 0, None), 0.5, 1)
    tput = base * f_qd * f_bs * f_comp * f_pm * f_wc * f_gc * f_thr * f_wl * thermal * rng.normal(1, 0.06, n_runs)
    tput = np.where(failed, tput * rng.uniform(0.15, 0.7, n_runs), tput)
    df["throughput_mbps"] = np.clip(tput, 5, None).round(1)
    df["iops"] = (df["throughput_mbps"] * 1024 / bs).round(0)
    df["latency_p99_ms"] = (0.08 * qd ** 0.5 + 0.01 * bs + 0.03 * jit + np.where(failed, rng.uniform(5, 60, n_runs), 0) + rng.gamma(2, 0.3, n_runs)).round(3)
    df["cpu_util"] = np.clip(20 + 1.1 * thr + 15 * (comp != "off") + rng.normal(0, 6, n_runs), 2, 100).round(1)
    df["mem_util"] = np.clip(30 + 0.08 * qd + 0.03 * bs + rng.normal(0, 7, n_runs), 5, 100).round(1)
    df["retry_count"] = np.minimum(rng.poisson(0.3 + 3 * p_fail), df["retry_limit"].astype(int) + 1)
    instab = 0.35 * np.clip(jit / 40, 0, 1) + 0.25 * df["retry_count"] / 9 + 0.25 * np.clip(df["latency_p99_ms"] / 40, 0, 1) + 0.15 * np.clip((temp - 40) / 35, 0, 1)
    df["instability_index"] = np.clip(instab, 0, 1).round(4)

    df["log_trace"] = _build_logs(df, rng)

    # cast object columns
    out = {}
    for k, v in df.items():
        if v.dtype == object:
            sample = v[0]
            v = v.astype(int) if isinstance(sample, (int, np.integer)) else v.astype(str)
        out[k] = v
    frame = pl.DataFrame(out)

    meta = {
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "n_runs": n_runs,
        "config_params": [p["name"] for p in KEY_PARAMS] + generic_names,
        "key_params": KEY_PARAMS,
        "sandbox_params": SANDBOX_PARAMS,
        "random_vars": NAMED_RANDOM + noise_names,
        "telemetry": TELEMETRY,
        "context": ["environment", "hardware", "workload"],
        "n_profiles": n_profiles,
        "generator_seed": seed,
        "hidden_unlucky_seeds": sorted(int(s) for s in unlucky),
        "log_lines": int(sum(t.count("\n") + 1 for t in df["log_trace"])),
    }
    if persist:
        frame.write_parquet(RUNS_PATH)
        META_PATH.write_text(json.dumps(meta, indent=2))
    return frame


_ANOMALY_LINES = {
    "CMD_QUEUE_TIMEOUT": ["WARN  nvme0: SQ{q} occupancy 100% for 850ms", "ERROR nvme0: I/O cmd {c} timeout, aborting", "ERROR host: controller reset issued (reason=CMD_TIMEOUT)"],
    "GC_STALL_WATCHDOG": ["WARN  ftl: GC reclaim backlog {g} blocks", "ERROR ftl: host write stalled 1200ms during GC", "FATAL wdt: GC watchdog expired"],
    "DMA_BUFFER_OVERRUN": ["WARN  dma: ring {q} high watermark exceeded", "ERROR dma: buffer overrun on zstd stream ctx={c}", "ERROR host: data integrity check failed"],
    "THERMAL_THROTTLE_ABORT": ["WARN  thermal: Tj={t}C exceeds TMT1", "WARN  thermal: throttle level 3 engaged", "ERROR thermal: Tj={t}C critical, aborting workload"],
    "TIMING_RACE_CRC_MISMATCH": ["WARN  phy: timing skew {j}us on lane 2", "ERROR crc: mismatch on LBA 0x{c}", "ERROR host: retry exhausted after CRC errors"],
    "FW_ASSERT_0x3F": ["WARN  fw: unexpected state transition 0x3F->0x12", "FATAL fw: ASSERT 0x3F at sched.c:812", "ERROR host: device unresponsive, power-cycling"],
    "ECC_UNCORRECTABLE": ["WARN  ecc: corrected bit errors spike ({g}/page)", "ERROR ecc: uncorrectable read at LBA 0x{c}", "ERROR host: media error reported"],
}


def _build_logs(df: dict, rng: np.random.Generator) -> np.ndarray:
    n = len(df["run_id"])
    logs = np.empty(n, dtype=object)
    qs, cs, gs = rng.integers(0, 16, n), rng.integers(0x1000, 0xFFFFFF, n), rng.integers(200, 9000, n)
    for i in range(n):
        lines = [
            f"INFO  boot: fw=4.{int(df['seed'][i]) % 7}.{int(df['seed'][i]) % 13} hw={df['hardware'][i]} env={df['environment'][i]}",
            f"INFO  cfg: qd={df['queue_depth'][i]} bs={df['block_size_kb'][i]}K wc={df['write_cache'][i]} gc={df['gc_policy'][i]} comp={df['compression'][i]}",
            f"INFO  run: workload={df['workload'][i]} seed={df['seed'][i]} traffic={df['traffic_intensity'][i]:.2f}",
            f"INFO  telemetry: temp={df['temperature_c'][i]:.1f}C jitter={df['timing_jitter_us'][i]:.1f}us",
        ]
        sig = df["error_signature"][i]
        if sig != "NONE":
            fmt = dict(q=qs[i], c=f"{cs[i]:X}", g=gs[i], t=f"{df['temperature_c'][i]:.0f}", j=f"{df['timing_jitter_us'][i]:.1f}")
            lines += [ln.format(**fmt) for ln in _ANOMALY_LINES[sig]]
            lines.append(f"RESULT FAIL signature={sig} throughput={df['throughput_mbps'][i]}MB/s")
        else:
            lines.append(f"INFO  perf: throughput={df['throughput_mbps'][i]}MB/s p99={df['latency_p99_ms'][i]}ms")
            lines.append("RESULT PASS")
        logs[i] = "\n".join(lines)
    return logs
