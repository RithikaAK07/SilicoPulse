"""Central dataset-type registry: classification -> processing pipeline -> analyses.

Every uploaded part is classified from its detected fields (never the filename), and the
registry decides which pipeline may ingest it. Adding a dataset type = adding one entry here.

  execution             -> existing execution pipeline (canonicalize + store + models)
  industrial_telemetry  -> telemetry pipeline (telemetry store + telemetry analytics)
  time_series_telemetry -> telemetry pipeline
"""
from __future__ import annotations

DATASET_TYPES: dict[str, dict] = {
    "execution": {
        "label": "Execution log (pass/fail outcomes)",
        "pipeline": "execution",
        "requires": ["outcome (PASS/FAIL)", "at least one configuration parameter"],
        "analyses": ["PASS/FAIL analysis", "configuration discovery", "randomization & seed determinism", "root cause & logs",
                     "failure prediction / risk model", "configuration recommendation", "AI Copilot"],
    },
    "industrial_telemetry": {
        "label": "Industrial telemetry",
        "pipeline": "telemetry",
        "requires": ["timestamp", "at least one numeric measurement channel"],
        "analyses": ["channel statistics", "trends", "anomaly windows", "channel health", "channel correlations"],
    },
    "time_series_telemetry": {
        "label": "Time-series telemetry",
        "pipeline": "telemetry",
        "requires": ["timestamp", "at least one numeric measurement channel"],
        "analyses": ["channel statistics", "trends", "anomaly windows", "channel health", "channel correlations"],
    },
}

# preprocessor classification (mapper.classify) -> dataset type; anything else has no ingestion pipeline
_FROM_DATA_TYPE = {
    "execution_log": "execution",
    "industrial_telemetry": "industrial_telemetry",
    "time_series_telemetry": "time_series_telemetry",
}


def dataset_type_of(data_type: str) -> str | None:
    return _FROM_DATA_TYPE.get(data_type)


def pipeline_of(dataset_type: str | None) -> str | None:
    return DATASET_TYPES.get(dataset_type or "", {}).get("pipeline")


def describe(dataset_type: str | None) -> dict | None:
    if dataset_type not in DATASET_TYPES:
        return None
    return {"key": dataset_type, **DATASET_TYPES[dataset_type]}
