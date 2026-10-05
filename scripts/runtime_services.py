"""Runtime management services — start/stop apps across CloudHub 1.0 and 2.0.

This module provides an isolated service layer for app lifecycle operations.
It is designed to be called from:
  - Web routes (user clicking Start/Stop buttons)
  - Future automated idle-detection systems
  - CLI scripts or scheduled jobs

All functions take an AnypointPlatformClient and return structured result dicts.
"""

from __future__ import annotations

import datetime as dt
import json
from typing import Any

from anypoint_platform_api import AnypointPlatformClient
from watchmen_log import get_logger

log = get_logger("runtime")


def start_app(
    client: AnypointPlatformClient,
    *,
    source: str,
    org_id: str,
    env_id: str,
    domain: str,
    deployment_id: str = "",
) -> dict:
    """Start a single application.

    Args:
        client: Authenticated Anypoint API client.
        source: 'cloudhub' or 'ch2'.
        org_id: Business group / org ID that owns the environment.
        env_id: Environment ID.
        domain: Application name (used for CH1 and for logging).
        deployment_id: CH2 deployment ID (required for source='ch2').

    Returns:
        {"ok": bool, "action": "start", "source": ..., "domain": ...,
         "env_id": ..., "detail": str, "response": dict}
    """
    return _do_action(client, "start", source=source, org_id=org_id,
                      env_id=env_id, domain=domain, deployment_id=deployment_id)


def stop_app(
    client: AnypointPlatformClient,
    *,
    source: str,
    org_id: str,
    env_id: str,
    domain: str,
    deployment_id: str = "",
) -> dict:
    """Stop a single application. Same signature as start_app."""
    return _do_action(client, "stop", source=source, org_id=org_id,
                      env_id=env_id, domain=domain, deployment_id=deployment_id)


def stop_all_apps_in_env(
    client: AnypointPlatformClient,
    *,
    org_id: str,
    env_id: str,
) -> dict:
    """Stop every running app (CH1 + CH2) in the given environment.

    Returns:
        {"ok": bool, "action": "stop_all", "env_id": ..., "org_id": ...,
         "total": int, "stopped": int, "skipped": int, "failed": int,
         "results": [per-app result dicts]}
    """
    results: list[dict] = []
    stopped = 0
    skipped = 0
    failed = 0

    # --- CH2 / RTF deployments ---
    try:
        ch2_list = client.list_ch2_deployments(org_id, env_id)
    except RuntimeError as exc:
        log.warning("stop_all: CH2 list failed for env %s: %s", env_id, exc)
        ch2_list = []

    for dep in ch2_list:
        dep_id = dep.get("id", "")
        name = dep.get("name", dep_id)
        app_status = dep.get("application", {}).get("status", dep.get("status", ""))
        desired = dep.get("application", {}).get("desiredState", "")

        if _is_stopped(app_status, desired):
            skipped += 1
            results.append({"ok": True, "action": "stop", "source": "ch2",
                            "domain": name, "detail": f"Already stopped ({app_status})"})
            continue

        r = _do_action(client, "stop", source="ch2", org_id=org_id,
                       env_id=env_id, domain=name, deployment_id=dep_id)
        results.append(r)
        if r["ok"]:
            stopped += 1
        else:
            failed += 1

    # --- CH1 apps ---
    try:
        ch1_list = client.list_cloudhub_apps(org_id, env_id)
    except RuntimeError as exc:
        log.warning("stop_all: CH1 list failed for env %s: %s", env_id, exc)
        ch1_list = []

    for app in ch1_list:
        name = app.get("domain", "")
        app_status = app.get("status", "")

        if _is_stopped(app_status):
            skipped += 1
            results.append({"ok": True, "action": "stop", "source": "cloudhub",
                            "domain": name, "detail": f"Already stopped ({app_status})"})
            continue

        r = _do_action(client, "stop", source="cloudhub", org_id=org_id,
                       env_id=env_id, domain=name, deployment_id="")
        results.append(r)
        if r["ok"]:
            stopped += 1
        else:
            failed += 1

    total = stopped + skipped + failed
    ok = failed == 0
    log.info("stop_all env=%s: total=%d stopped=%d skipped=%d failed=%d",
             env_id, total, stopped, skipped, failed)

    return {
        "ok": ok,
        "action": "stop_all",
        "env_id": env_id,
        "org_id": org_id,
        "total": total,
        "stopped": stopped,
        "skipped": skipped,
        "failed": failed,
        "results": results,
    }


# -- Internal helpers --

_STOPPED_STATUSES = {"STOPPED", "UNDEPLOYED", "NOT_RUNNING", "DELETED"}


def _is_stopped(status: str, desired_state: str = "") -> bool:
    """Check if an app is already in a stopped/non-running state."""
    s = (status or "").upper()
    d = (desired_state or "").upper()
    return s in _STOPPED_STATUSES or d == "STOPPED"


def _do_action(
    client: AnypointPlatformClient,
    action: str,  # "start" or "stop"
    *,
    source: str,
    org_id: str,
    env_id: str,
    domain: str,
    deployment_id: str,
) -> dict:
    """Execute a start or stop action on a single app."""
    base = {
        "action": action,
        "source": source,
        "domain": domain,
        "env_id": env_id,
        "org_id": org_id,
        "timestamp_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
    }
    try:
        if source == "ch2":
            if not deployment_id:
                raise ValueError(f"deployment_id required for CH2 app '{domain}'")
            if action == "start":
                resp = client.start_ch2_deployment(org_id, env_id, deployment_id)
            else:
                resp = client.stop_ch2_deployment(org_id, env_id, deployment_id)

        elif source == "cloudhub":
            if action == "start":
                resp = client.start_cloudhub_app(org_id, env_id, domain)
            else:
                resp = client.stop_cloudhub_app(org_id, env_id, domain)

        else:
            raise ValueError(f"Unsupported source '{source}' for {action}")

        log.info("%s %s app '%s' in env %s (org %s) — success",
                 action.upper(), source, domain, env_id, org_id)
        return {**base, "ok": True, "detail": f"{action} request accepted",
                "response": _safe_response(resp)}

    except Exception as exc:
        log.error("%s %s app '%s' in env %s failed: %s",
                  action.upper(), source, domain, env_id, exc)
        return {**base, "ok": False, "detail": str(exc), "response": {}}


def refresh_app_statuses(
    client: AnypointPlatformClient,
    apps: list[dict],
    conn: Any,
) -> dict:
    """Quick live-status refresh for apps in the latest inventory snapshot.

    Reads each app's current status from the Anypoint API and updates
    the inventory_application row in-place (no new snapshot).

    Args:
        client: Authenticated API client.
        apps: List of app dicts from latest_inventory()["applications"].
              Each must have: id, source, env_id, domain, bg_id, extra_json.
        conn: Open sqlite3 connection (caller must commit).

    Returns:
        {"refreshed": int, "unchanged": int, "errors": int, "details": [...]}
    """
    refreshed = 0
    unchanged = 0
    errors = 0
    details: list[dict] = []

    for app in apps:
        source = app.get("source", "")
        if source not in ("cloudhub", "ch2"):
            continue  # skip hybrid / other

        row_id = app.get("id")
        domain = app.get("domain", "")
        org_id = app.get("bg_id", "")
        env_id = app.get("env_id", "")
        old_status = app.get("status", "")

        # Parse deployment_id from extra_json for CH2
        extra = app.get("extra_json", "{}")
        if isinstance(extra, str):
            try:
                extra = json.loads(extra)
            except (json.JSONDecodeError, TypeError):
                extra = {}
        deployment_id = extra.get("deployment_id", "")

        try:
            if source == "ch2":
                if not deployment_id:
                    continue
                info = client.get_ch2_deployment(org_id, env_id, deployment_id)
                new_status = info.get("application", {}).get("status", info.get("status", ""))
            else:  # cloudhub
                info = client.get_cloudhub_app(org_id, env_id, domain)
                new_status = info.get("status", "")

            if new_status and new_status != old_status:
                conn.execute(
                    "UPDATE inventory_application SET status = ? WHERE id = ?",
                    (new_status, row_id),
                )
                refreshed += 1
                details.append({"domain": domain, "old": old_status, "new": new_status})
                log.info("Status refresh: %s %s → %s", domain, old_status, new_status)
            else:
                unchanged += 1

        except Exception as exc:
            errors += 1
            log.warning("Status refresh failed for %s: %s", domain, exc)

    log.info("Status refresh: %d refreshed, %d unchanged, %d errors",
             refreshed, unchanged, errors)
    return {"refreshed": refreshed, "unchanged": unchanged, "errors": errors, "details": details}


def _safe_response(resp: Any) -> dict:
    """Ensure the response is a JSON-serializable dict."""
    if isinstance(resp, dict):
        return resp
    if isinstance(resp, list):
        return {"items": resp}
    return {"raw": str(resp)}
