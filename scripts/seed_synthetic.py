#!/usr/bin/env python3
"""Seed the database with realistic synthetic usage data.

Models a mid-sized integration team with multiple apps across environments.
Volumes are calibrated against the entitlement limits in config/entitlements.json.

Scenarios:
  hot    — some entitlements burning above limits, needs attention
  steady — cruising along fine, well within budget

Usage:
    python scripts/seed_synthetic.py                    # default: hot
    python scripts/seed_synthetic.py --scenario steady   # comfortable pace
    python scripts/seed_synthetic.py --clear             # remove all synthetic data
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import random
import uuid

from usage_store import connect, insert_snapshot, insert_usage_records
from watchmen_config import load_settings, project_path


SYNTHETIC_PREFIX = "synthetic-seed-"
ORG = "Example-Org"


# ---------------------------------------------------------------------------
# Scenario definitions
# ---------------------------------------------------------------------------
# Each scenario defines consumer topologies with daily volumes.
# The entitlement limits (from config/entitlements.json) for reference:
#   mule_flows:           200 flows          (HIGH_WATERMARK)
#   mule_messages:        20,000,000 msgs    (DRAWDOWN, 1yr)
#   data_throughput:      40,000 GB          (DRAWDOWN, 1yr)
#   api_manager_prod:     10 instances       (HIGH_WATERMARK)
#   api_manager_preprod:  10 instances       (HIGH_WATERMARK)
#   governed_apis:        10 governed apis   (HIGH_WATERMARK)
#   flex_gateway_api_calls: 20,000,000 calls (DRAWDOWN, 1yr)
#   object_store:         100,000,000 reqs   (DRAWDOWN, 1yr)
#   anypoint_mq:          500,000,000 reqs   (DRAWDOWN, 1yr)
# ---------------------------------------------------------------------------

SCENARIOS = {
    "hot": {
        "description": "Above-limit burn on Flex Gateway and Mule Messages",
        "growth_per_day": 0.015,
        "runtime_apps": [
            {"app": "order-process-api",  "env": "prod",    "env_type": "Production", "model": "CloudHub 2.0",     "flows": 25, "msgs_day": 42000,  "bytes_day": 1.8e9},
            {"app": "customer-sys-api",   "env": "prod",    "env_type": "Production", "model": "CloudHub 2.0",     "flows": 18, "msgs_day": 28000,  "bytes_day": 0.9e9},
            {"app": "inventory-exp-api",  "env": "prod",    "env_type": "Production", "model": "CloudHub 2.0",     "flows": 12, "msgs_day": 15000,  "bytes_day": 0.5e9},
            {"app": "payment-gateway",    "env": "prod",    "env_type": "Production", "model": "Runtime Fabric",   "flows": 8,  "msgs_day": 9500,   "bytes_day": 1.2e9},
            {"app": "order-process-api",  "env": "staging", "env_type": "Sandbox",    "model": "CloudHub 2.0",     "flows": 25, "msgs_day": 5000,   "bytes_day": 0.2e9},
            {"app": "customer-sys-api",   "env": "staging", "env_type": "Sandbox",    "model": "CloudHub 2.0",     "flows": 18, "msgs_day": 3000,   "bytes_day": 0.1e9},
            {"app": "shipping-notif-api", "env": "dev",     "env_type": "Sandbox",    "model": "CloudHub 2.0",     "flows": 6,  "msgs_day": 1200,   "bytes_day": 0.05e9},
        ],
        "flex_gateways": [
            {"asset": "retail-flex-gw",   "env": "prod",    "mode": "managed", "calls_day": 85000},
            {"asset": "partner-flex-gw",  "env": "prod",    "mode": "managed", "calls_day": 45000},
            {"asset": "internal-flex-gw", "env": "staging", "mode": "managed", "calls_day": 12000},
        ],
        "mq_queues": [
            {"queue": "order-events",       "env": "prod",    "region": "us-east-1", "reqs_day": 65000},
            {"queue": "notification-queue", "env": "prod",    "region": "us-east-1", "reqs_day": 38000},
            {"queue": "inventory-sync",     "env": "prod",    "region": "us-east-1", "reqs_day": 22000},
            {"queue": "test-queue",         "env": "staging", "region": "us-east-1", "reqs_day": 5000},
        ],
        "obj_stores": [
            {"store": "order-cache",   "env": "prod",    "region": "us-east-1", "reqs_day": 28000},
            {"store": "session-store", "env": "prod",    "region": "us-east-1", "reqs_day": 15000},
            {"store": "test-cache",    "env": "staging", "region": "us-east-1", "reqs_day": 3000},
        ],
    },

    "steady": {
        "description": "Comfortable pace, well within all budgets",
        "growth_per_day": 0.005,
        "runtime_apps": [
            # ~30K msgs/day total across all apps → ~11M/yr vs 20M limit ≈ 55%
            {"app": "order-process-api",  "env": "prod",    "env_type": "Production", "model": "CloudHub 2.0",     "flows": 15, "msgs_day": 12000,  "bytes_day": 0.6e9},
            {"app": "customer-sys-api",   "env": "prod",    "env_type": "Production", "model": "CloudHub 2.0",     "flows": 10, "msgs_day": 8000,   "bytes_day": 0.3e9},
            {"app": "inventory-exp-api",  "env": "prod",    "env_type": "Production", "model": "CloudHub 2.0",     "flows": 8,  "msgs_day": 5000,   "bytes_day": 0.2e9},
            {"app": "payment-gateway",    "env": "prod",    "env_type": "Production", "model": "Runtime Fabric",   "flows": 5,  "msgs_day": 3000,   "bytes_day": 0.4e9},
            {"app": "order-process-api",  "env": "staging", "env_type": "Sandbox",    "model": "CloudHub 2.0",     "flows": 15, "msgs_day": 2000,   "bytes_day": 0.05e9},
            {"app": "customer-sys-api",   "env": "staging", "env_type": "Sandbox",    "model": "CloudHub 2.0",     "flows": 10, "msgs_day": 1000,   "bytes_day": 0.03e9},
        ],
        "flex_gateways": [
            # ~35K calls/day → ~13M/yr vs 20M limit ≈ 65%
            {"asset": "retail-flex-gw",   "env": "prod",    "mode": "managed", "calls_day": 25000},
            {"asset": "partner-flex-gw",  "env": "prod",    "mode": "managed", "calls_day": 10000},
        ],
        "mq_queues": [
            # ~55K reqs/day → ~20M/yr vs 500M limit ≈ 4%
            {"queue": "order-events",       "env": "prod",    "region": "us-east-1", "reqs_day": 30000},
            {"queue": "notification-queue", "env": "prod",    "region": "us-east-1", "reqs_day": 18000},
            {"queue": "inventory-sync",     "env": "prod",    "region": "us-east-1", "reqs_day": 7000},
        ],
        "obj_stores": [
            # ~30K reqs/day → ~11M/yr vs 100M limit ≈ 11%
            {"store": "order-cache",   "env": "prod",    "region": "us-east-1", "reqs_day": 18000},
            {"store": "session-store", "env": "prod",    "region": "us-east-1", "reqs_day": 12000},
        ],
    },
}


# ---------------------------------------------------------------------------
# Record builder
# ---------------------------------------------------------------------------

def _ts_ms(d: dt.date) -> int:
    return int(dt.datetime.combine(d, dt.time(), dt.timezone.utc).timestamp() * 1000)


def _jitter(base: float, pct: float = 0.15) -> float:
    return base * (1 + random.uniform(-pct, pct))


def _build_daily_records(snapshot_id: int, start_date: dt.date, num_days: int, scenario: dict) -> list[dict]:
    records: list[dict] = []
    growth_rate = scenario["growth_per_day"]
    runtime_apps = scenario["runtime_apps"]
    flex_gateways = scenario["flex_gateways"]
    mq_queues = scenario["mq_queues"]
    obj_stores = scenario["obj_stores"]

    for day_offset in range(num_days):
        day = start_date + dt.timedelta(days=day_offset)
        ts = _ts_ms(day)
        period_start = dt.datetime.combine(day, dt.time(), dt.timezone.utc).isoformat()
        period_end = dt.datetime.combine(day + dt.timedelta(days=1), dt.time(), dt.timezone.utc).isoformat()

        weekday_factor = 0.4 if day.weekday() >= 5 else 1.0
        growth = 1.0 + (day_offset * growth_rate)

        base_rec = {
            "snapshot_id": snapshot_id,
            "timeseries": "P1D",
            "period_start_utc": period_start,
            "period_end_utc": period_end,
            "org_name": ORG,
        }

        # --- Runtime meters ---
        for app in runtime_apps:
            app_base = {
                **base_rec,
                "env_name": app["env"],
                "env_type": app["env_type"],
                "app_name": app["app"],
                "deployment_model": app["model"],
            }

            records.append({
                **app_base,
                "meter_key": "mule_flows",
                "meter_name": "runtime_flow_count",
                "measurement": "mule_flow_count",
                "meter_type": "MAX_CONCURRENT",
                "value": float(app["flows"]),
                "raw_dimensions": {
                    "timestamp": ts, "org_name": ORG,
                    "env_name": app["env"], "env_type": app["env_type"],
                    "app_name": app["app"], "deployment_model": app["model"],
                    "max_concurrent_time": ts,
                },
                "raw_record": {},
            })

            records.append({
                **app_base,
                "meter_key": "mule_messages",
                "meter_name": "runtime_mule_message_count",
                "measurement": "mule_message_count",
                "meter_type": "SUM",
                "value": round(_jitter(app["msgs_day"] * growth * weekday_factor)),
                "raw_dimensions": {
                    "timestamp": ts, "org_name": ORG,
                    "env_name": app["env"], "app_name": app["app"],
                },
                "raw_record": {},
            })

            records.append({
                **app_base,
                "meter_key": "data_throughput",
                "meter_name": "runtime_network_bytes_count",
                "measurement": "network_bytes_count",
                "meter_type": "SUM",
                "value": round(_jitter(app["bytes_day"] * growth * weekday_factor)),
                "raw_dimensions": {
                    "timestamp": ts, "org_name": ORG,
                    "env_name": app["env"], "app_name": app["app"],
                },
                "raw_record": {},
            })

        # --- API Manager (MAX_CONCURRENT) ---
        prod_apps = [a for a in runtime_apps if a["env_type"] == "Production"]
        sandbox_apps = [a for a in runtime_apps if a["env_type"] == "Sandbox"]

        records.append({
            **base_rec,
            "env_name": None, "env_type": "Production", "app_name": "api-manager",
            "deployment_model": "Mule Gateway",
            "meter_key": "api_manager_prod",
            "meter_name": "api_manager_api_instance_count_prod",
            "measurement": "managed_api_count",
            "meter_type": "MAX_CONCURRENT",
            "value": float(len(prod_apps)),
            "raw_dimensions": {"timestamp": ts, "org_name": ORG, "env_type": "Production", "runtime": "Mule Gateway", "max_concurrent_time": ts},
            "raw_record": {},
        })
        records.append({
            **base_rec,
            "env_name": None, "env_type": "Sandbox", "app_name": "api-manager",
            "deployment_model": "Mule Gateway",
            "meter_key": "api_manager_preprod",
            "meter_name": "api_manager_api_instance_count_preprod",
            "measurement": "managed_api_count",
            "meter_type": "MAX_CONCURRENT",
            "value": float(len(sandbox_apps)),
            "raw_dimensions": {"timestamp": ts, "org_name": ORG, "env_type": "Sandbox", "runtime": "Mule Gateway", "max_concurrent_time": ts},
            "raw_record": {},
        })

        # --- Governed APIs (MAX_CONCURRENT) ---
        records.append({
            **base_rec,
            "env_name": None, "env_type": None, "app_name": "governance",
            "deployment_model": None,
            "meter_key": "governed_apis",
            "meter_name": "governed_api_count",
            "measurement": "governed_api_count",
            "meter_type": "MAX_CONCURRENT",
            "value": float(len(prod_apps)),
            "raw_dimensions": {"timestamp": ts, "org_name": ORG, "max_concurrent_time": ts},
            "raw_record": {},
        })

        # --- Flex Gateway API Calls (SUM) ---
        for gw in flex_gateways:
            records.append({
                **base_rec,
                "env_name": gw["env"], "env_type": None,
                "app_name": None,
                "deployment_model": gw["mode"],
                "meter_key": "flex_gateway_api_calls",
                "meter_name": "flex_api_call_count",
                "measurement": "api_call_count",
                "meter_type": "SUM",
                "value": round(_jitter(gw["calls_day"] * growth * weekday_factor)),
                "raw_dimensions": {
                    "timestamp": ts, "org_name": ORG,
                    "env_name": gw["env"], "deployment_mode": gw["mode"],
                    "asset_name": gw["asset"],
                },
                "raw_record": {},
            })

        # --- Object Store (SUM) ---
        for os_ in obj_stores:
            records.append({
                **base_rec,
                "env_name": os_["env"], "env_type": None,
                "app_name": None,
                "deployment_model": None,
                "meter_key": "object_store_effective_api_requests",
                "meter_name": "object_store_effective_api_requests_count",
                "measurement": "effective_api_requests",
                "meter_type": "SUM",
                "value": round(_jitter(os_["reqs_day"] * growth * weekday_factor)),
                "raw_dimensions": {
                    "timestamp": ts, "org_name": ORG,
                    "env_name": os_["env"], "region_id": os_["region"],
                    "store_id": os_["store"],
                },
                "raw_record": {},
            })

        # --- Anypoint MQ (SUM) ---
        for mq in mq_queues:
            records.append({
                **base_rec,
                "env_name": mq["env"], "env_type": None,
                "app_name": mq["queue"],
                "deployment_model": None,
                "meter_key": "anypoint_mq_api_requests",
                "meter_name": "anypoint_mq_api_requests_count",
                "measurement": "api_requests",
                "meter_type": "SUM",
                "value": round(_jitter(mq["reqs_day"] * growth * weekday_factor)),
                "raw_dimensions": {
                    "timestamp": ts, "org_name": ORG,
                    "env_name": mq["env"], "region_id": mq["region"],
                    "object_type": "QUEUE", "object_name": mq["queue"],
                },
                "raw_record": {},
            })

    return records


def seed(settings_path: str = "config/settings.json", scenario_name: str = "hot") -> dict:
    if scenario_name not in SCENARIOS:
        raise ValueError(f"Unknown scenario '{scenario_name}'. Available: {', '.join(SCENARIOS)}")

    scenario = SCENARIOS[scenario_name]
    settings = load_settings(settings_path)
    contract_start = dt.date.fromisoformat(settings["contract"]["start_date"])
    today = dt.date.today()
    num_days = max((today - contract_start).days + 1, 1)

    run_id = f"{SYNTHETIC_PREFIX}{scenario_name}-{dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex[:6]}"
    window_start = dt.datetime.combine(contract_start, dt.time(), dt.timezone.utc)
    window_end = dt.datetime.combine(today + dt.timedelta(days=1), dt.time(), dt.timezone.utc)

    random.seed(42)

    conn = connect(project_path(settings["storage"]["sqlite_path"]))
    try:
        snapshot_id = insert_snapshot(
            conn,
            run_id=run_id,
            collected_at_utc=dt.datetime.now(dt.timezone.utc).isoformat(),
            host="https://anypoint.mulesoft.com",
            window_start_utc=window_start.isoformat(),
            window_end_utc=window_end.isoformat(),
            timeseries="P1D",
            dimensions=True,
            raw_path=None,
        )
        records = _build_daily_records(snapshot_id, contract_start, num_days, scenario)
        inserted = insert_usage_records(conn, records)
        conn.commit()
    finally:
        conn.close()

    return {"run_id": run_id, "scenario": scenario_name, "description": scenario["description"], "days": num_days, "records_inserted": inserted}


def clear(settings_path: str = "config/settings.json") -> dict:
    settings = load_settings(settings_path)
    conn = connect(project_path(settings["storage"]["sqlite_path"]))
    try:
        rows = conn.execute(
            "SELECT id FROM usage_snapshot WHERE run_id LIKE ?", (f"{SYNTHETIC_PREFIX}%",)
        ).fetchall()
        snapshot_ids = [r["id"] for r in rows]
        deleted_records = 0
        for sid in snapshot_ids:
            cur = conn.execute("DELETE FROM usage_record WHERE snapshot_id = ?", (sid,))
            deleted_records += cur.rowcount
        cur = conn.execute("DELETE FROM usage_snapshot WHERE run_id LIKE ?", (f"{SYNTHETIC_PREFIX}%",))
        deleted_snapshots = cur.rowcount
        conn.commit()
    finally:
        conn.close()
    return {"deleted_snapshots": deleted_snapshots, "deleted_records": deleted_records}


def main() -> int:
    parser = argparse.ArgumentParser(description="Seed or clear synthetic usage data.")
    parser.add_argument("--clear", action="store_true", help="Remove all synthetic data.")
    parser.add_argument("--scenario", default="hot", choices=list(SCENARIOS.keys()),
                        help="Which scenario to seed (default: hot)")
    parser.add_argument("--settings", default="config/settings.json")
    args = parser.parse_args()

    if args.clear:
        result = clear(args.settings)
        print("Cleared synthetic data:", json.dumps(result, indent=2))
    else:
        result = seed(args.settings, args.scenario)
        print("Seeded synthetic data:", json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
