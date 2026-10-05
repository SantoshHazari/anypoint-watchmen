"""Demo ownership governance checks."""

from __future__ import annotations

import datetime as dt

from demo_store import active_demo_resource_keys, demo_counts, ensure_demo_schema, list_demos
from inventory_store import ensure_inventory_schema, latest_inventory
from usage_store import connect, upsert_alert
from watchmen_config import load_settings, project_path
from watchmen_log import get_logger


log = get_logger("demos")

DEMO_ALERT_RULES = (
    "demo_expired",
    "demo_expires_soon",
    "closed_demo_resource_still_live",
    "unowned_inventory_resource",
)


def run_demo_governance(settings_path: str = "config/settings.json") -> dict:
    settings = load_settings(settings_path)
    conn = connect(project_path(settings["storage"]["sqlite_path"]))
    active_alert_keys: set[str] = set()
    created_or_updated = 0
    try:
        ensure_demo_schema(conn)
        ensure_inventory_schema(conn)
        demos = list_demos(conn)
        inventory = latest_inventory(conn)
        now_run = f"demo-governance-{dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"

        for demo in demos:
            for alert in _demo_alerts(demo, inventory, now_run):
                active_alert_keys.add(alert["alert_key"])
                upsert_alert(conn, **alert)
                created_or_updated += 1

        for alert in _unowned_inventory_alerts(conn, inventory, now_run):
            active_alert_keys.add(alert["alert_key"])
            upsert_alert(conn, **alert)
            created_or_updated += 1

        # Only resolve stale demo-governance alerts, not usage/audit alerts.
        _resolve_stale_demo_alerts(conn, active_alert_keys)
        conn.commit()
        counts = demo_counts(conn)
    finally:
        conn.close()
    summary = {
        "run_id": now_run,
        "alerts_evaluated": len(active_alert_keys),
        "alerts_upserted": created_or_updated,
        "demo_counts": counts,
    }
    log.info("Demo governance %s: %d active alerts", now_run, len(active_alert_keys))
    return summary


def _demo_alerts(demo: dict, inventory: dict, run_id: str) -> list[dict]:
    alerts = []
    today = dt.date.today()
    expiry = _parse_date(demo.get("expiry_date", ""))
    if expiry and expiry < today and demo["status"] != "closed":
        alerts.append(_alert(
            key=f"demo:expired:{demo['id']}",
            rule="demo_expired",
            severity="warning",
            title=f"Demo expired: {demo['name']}",
            detail=f"Owner {demo['owner']} | expired {demo['expiry_date']}",
            run_id=run_id,
            payload=demo,
        ))
    if expiry and today <= expiry <= today + dt.timedelta(days=7) and demo["status"] in ("planned", "active"):
        alerts.append(_alert(
            key=f"demo:expires-soon:{demo['id']}",
            rule="demo_expires_soon",
            severity="info",
            title=f"Demo expires soon: {demo['name']}",
            detail=f"Owner {demo['owner']} | expiry {demo['expiry_date']}",
            run_id=run_id,
            payload=demo,
        ))
    if demo["status"] == "closed" and _resource_exists(demo, inventory):
        alerts.append(_alert(
            key=f"demo:closed-resource-live:{demo['id']}",
            rule="closed_demo_resource_still_live",
            severity="warning",
            title=f"Closed demo resource still exists: {demo['name']}",
            detail=f"{demo['resource_type']} {demo['resource_name']}",
            run_id=run_id,
            payload=demo,
        ))
    return alerts


def _unowned_inventory_alerts(conn, inventory: dict, run_id: str) -> list[dict]:
    owned = active_demo_resource_keys(conn)
    alerts = []
    for app in inventory.get("applications", []):
        key = ("application", app["domain"], app["env_id"])
        if key not in owned:
            alerts.append(_alert(
                key=f"demo:unowned:application:{app['env_id']}:{app['domain']}",
                rule="unowned_inventory_resource",
                severity="info",
                title=f"Application without demo owner: {app['domain']}",
                detail=f"Environment {app['env_id']} | status {app.get('status', '')}",
                run_id=run_id,
                payload=app,
            ))
    for api in inventory.get("api_instances", []):
        resource_name = api.get("asset_id") or api.get("instance_label") or api.get("api_id")
        key = ("api_instance", resource_name, api["env_id"])
        if key not in owned:
            alerts.append(_alert(
                key=f"demo:unowned:api_instance:{api['env_id']}:{resource_name}",
                rule="unowned_inventory_resource",
                severity="info",
                title=f"API instance without demo owner: {resource_name}",
                detail=f"Environment {api['env_id']} | label {api.get('instance_label', '')}",
                run_id=run_id,
                payload=api,
            ))
    return alerts


def _resource_exists(demo: dict, inventory: dict) -> bool:
    if not demo.get("resource_name"):
        return False
    if demo["resource_type"] == "application":
        return any(app["domain"] == demo["resource_name"] and app["env_id"] == demo["environment_id"] for app in inventory.get("applications", []))
    if demo["resource_type"] == "api_instance":
        return any(
            (api.get("asset_id") == demo["resource_name"] or api.get("instance_label") == demo["resource_name"] or str(api.get("api_id")) == demo["resource_name"])
            and api["env_id"] == demo["environment_id"]
            for api in inventory.get("api_instances", [])
        )
    return False


def _resolve_stale_demo_alerts(conn, active_keys: set[str]) -> None:
    placeholders = ",".join("?" for _ in DEMO_ALERT_RULES)
    rows = conn.execute(
        f"""
        SELECT id, alert_key
        FROM alert_event
        WHERE rule IN ({placeholders})
          AND state IN ('open', 'acknowledged')
        """,
        DEMO_ALERT_RULES,
    ).fetchall()
    now = dt.datetime.now(dt.timezone.utc).isoformat()
    for row in rows:
        if row["alert_key"] not in active_keys:
            conn.execute(
                """
                UPDATE alert_event
                SET state = 'resolved', resolved_at_utc = ?, updated_at_utc = ?
                WHERE id = ?
                """,
                (now, now, row["id"]),
            )


def _alert(*, key: str, rule: str, severity: str, title: str, detail: str, run_id: str, payload: dict) -> dict:
    return {
        "alert_key": key,
        "rule": rule,
        "severity": severity,
        "entitlement_key": None,
        "title": title,
        "detail": detail,
        "run_id": run_id,
        "payload": payload,
    }


def _parse_date(value: str) -> dt.date | None:
    if not value:
        return None
    try:
        return dt.date.fromisoformat(value)
    except ValueError:
        return None
