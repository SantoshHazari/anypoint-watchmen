#!/usr/bin/env python3
"""Generate realistic synthetic Usage API raw snapshots for local KPI testing."""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

from usage_meters import build_query, load_meters
from usage_time import utc_ms
from watchmen_config import project_path


FIXTURE_DIR = project_path("tests/fixtures/usage")
HOST = "https://anypoint.mulesoft.com"
ORG = "Example-Org"
ENV = "demo-nonprod"
ENV_TYPE = "Sandbox"
APP = "demo-order-api"
DEPLOYMENT_MODEL = "CloudHub 2.0"


SCENARIOS = {
    "zero": {
        "mule_flows": 0,
        "mule_messages": 0,
        "data_throughput_gb": 0,
        "api_manager_prod": 0,
        "api_manager_preprod": 0,
        "governed_apis": 0,
        "flex_gateway_api_calls": 0,
        "object_store_effective_api_requests": 0,
        "anypoint_mq_api_requests": 0,
    },
    "small": {
        "mule_flows": 5,
        "mule_messages": 125000,
        "data_throughput_gb": 80,
        "api_manager_prod": 1,
        "api_manager_preprod": 2,
        "governed_apis": 1,
        "flex_gateway_api_calls": 150000,
        "object_store_effective_api_requests": 50000,
        "anypoint_mq_api_requests": 25000,
    },
    "spike": {
        "mule_flows": 25,
        "mule_messages": 4500000,
        "data_throughput_gb": 9000,
        "api_manager_prod": 4,
        "api_manager_preprod": 5,
        "governed_apis": 4,
        "flex_gateway_api_calls": 5000000,
        "object_store_effective_api_requests": 3000000,
        "anypoint_mq_api_requests": 2000000,
    },
    "near_limit": {
        "mule_flows": 170,
        "mule_messages": 18500000,
        "data_throughput_gb": 37000,
        "api_manager_prod": 9,
        "api_manager_preprod": 9,
        "governed_apis": 9,
        "flex_gateway_api_calls": 18500000,
        "object_store_effective_api_requests": 85000000,
        "anypoint_mq_api_requests": 440000000,
    },
}


def _row(meter_key: str, measurement: str, value: float, timestamp_ms: int) -> dict:
    common = {"timestamp": timestamp_ms, "org_name": ORG}
    if meter_key in {"mule_flows", "mule_messages", "data_throughput"}:
        return {
            **common,
            "env_name": ENV,
            "env_type": ENV_TYPE,
            "app_name": APP,
            "deployment_model": DEPLOYMENT_MODEL,
            measurement: value,
            **({"max_concurrent_time": timestamp_ms} if meter_key == "mule_flows" else {}),
        }
    if meter_key.startswith("api_manager"):
        return {
            **common,
            "env_type": "Production" if meter_key == "api_manager_prod" else "Sandbox",
            "runtime": "Mule Gateway",
            measurement: value,
            "max_concurrent_time": timestamp_ms,
        }
    if meter_key == "governed_apis":
        return {**common, measurement: value, "max_concurrent_time": timestamp_ms}
    if meter_key == "flex_gateway_api_calls":
        return {
            **common,
            "env_name": ENV,
            "deployment_mode": "managed",
            "asset_name": "demo-flex-gateway",
            measurement: value,
        }
    if meter_key == "object_store_effective_api_requests":
        return {
            **common,
            "env_name": ENV,
            "region_id": "us-east-1",
            "store_id": "demo-object-store",
            measurement: value,
        }
    if meter_key.startswith("anypoint_mq"):
        return {
            **common,
            "env_name": ENV,
            "region_id": "us-east-1",
            "object_type": "QUEUE",
            "object_name": "demo-queue",
            measurement: value,
        }
    if meter_key == "idp_processed_pages":
        return {**common, "execution_type": "demo", measurement: value}
    if meter_key == "composer_tasks":
        return {"timestamp": timestamp_ms, "asset_id": "demo-composer-flow", measurement: value}
    if meter_key == "rpa_bot_minutes":
        return {"timestamp": timestamp_ms, "process_id": "demo-rpa-process", measurement: value}
    return {**common, measurement: value}


def _scenario_value(scenario: dict, meter_key: str) -> float:
    if meter_key == "data_throughput":
        return scenario["data_throughput_gb"] * (1024**3)
    return float(scenario.get(meter_key, 0))


def build_fixture(name: str, scenario: dict) -> dict:
    start = dt.datetime(2026, 5, 4, 0, 0, 0, tzinfo=dt.timezone.utc)
    end = dt.datetime(2026, 5, 11, 23, 59, 59, tzinfo=dt.timezone.utc)
    timestamp_ms = utc_ms(dt.datetime(2026, 5, 11, 0, 0, 0, tzinfo=dt.timezone.utc))
    meters = load_meters(project_path("config/anypoint_usage_meters.json"))
    results = []
    for meter in meters:
        value = _scenario_value(scenario, meter["key"])
        data = [] if value == 0 else [_row(meter["key"], meter["measurement"], value, timestamp_ms)]
        results.append(
            {
                "key": meter["key"],
                "meter": meter["meter"],
                "measurement": meter["measurement"],
                "query": build_query(meter, utc_ms(start), utc_ms(end), "P1D", True),
                "ok": True,
                "response": {
                    "metadata": {"responseAsOf": "2026-05-15T00:00:00Z"},
                    "data": data,
                },
            }
        )
    return {
        "run_id": f"fixture-{name}",
        "host": HOST,
        "collected_at_utc": "2026-05-15T00:00:00+00:00",
        "window_utc": {"start": start.isoformat(), "end": end.isoformat()},
        "timeseries": "P1D",
        "dimensions": True,
        "fixture": True,
        "scenario": name,
        "results": results,
    }


def main() -> int:
    FIXTURE_DIR.mkdir(parents=True, exist_ok=True)
    for name, scenario in SCENARIOS.items():
        path = FIXTURE_DIR / f"{name}.json"
        path.write_text(json.dumps(build_fixture(name, scenario), indent=2) + "\n", encoding="utf-8")
        print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
