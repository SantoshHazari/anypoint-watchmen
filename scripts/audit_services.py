"""Audit Log collection and risk detection."""

from __future__ import annotations

import datetime as dt
import uuid

from anypoint_auth import load_auth
from anypoint_platform_api import AnypointPlatformClient
from audit_normalizer import classify_risk, extract_audit_events, is_risky_event, normalize_audit_event
from audit_store import ensure_audit_schema, finish_audit_poll_run, get_last_audit_poll_end, insert_audit_events, insert_audit_poll_run
from usage_store import connect, upsert_alert, resolve_old_audit_alerts
from watchmen_config import load_settings, project_path
from watchmen_log import get_logger


log = get_logger("audit")


def _org_from_me(me: dict) -> tuple[str, str]:
    user_info = me.get("user", me)
    org_id = user_info.get("organizationId", "")
    org_name = ""
    for org in user_info.get("memberOfOrganizations", []):
        if org.get("id") == org_id:
            org_name = org.get("name", "")
            break
    return org_id, org_name


def _iso_z(value: dt.datetime) -> str:
    return value.astimezone(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def _build_action_filter(settings: dict) -> list[str] | None:
    """Return an API-ready actions whitelist derived from settings exclude_actions.

    The Anypoint audit API uses PascalCase action names (e.g. 'Login', 'Edit').
    We derive the filter by discovering all known action names from one sample page
    and removing the ones in the exclude_actions list (stored lowercase in settings).

    Returns None if no filter should be applied (pass-through).
    """
    exclude = {a.lower() for a in settings.get("audit", {}).get("exclude_actions", [])}
    if not exclude:
        return None

    # Known Anypoint action names — expanded from real API observations.
    # Using title-case as returned by the API.
    known_actions = [
        "Create", "Delete", "Edit", "Deploy", "Grant", "Revoke",
        "Disable", "Enable", "Approve", "Approved", "Reject",
        "Start", "Stop", "Import", "Export", "Publish", "Archive",
        "Promote", "Permissions change", "Add", "Remove", "Upload",
        "Download", "Execute", "Rotate",
    ]
    watch = [a for a in known_actions if a.lower() not in exclude]
    log.debug("Audit action filter: excluding %s → watching %d actions", sorted(exclude), len(watch))
    return watch or None


def _paginate_audit(
    client,
    bg_id: str,
    bg_name: str,
    start,
    end,
    page_size: int,
    max_pages: int,
    action_filter: list[str] | None = None,
) -> list[dict]:
    """Fetch audit events for a single BG with optional action pre-filter."""
    raw_events = []
    for page in range(max_pages):
        offset = page * page_size
        response = client.query_audit_log(
            bg_id,
            start_date=_iso_z(start),
            end_date=_iso_z(end),
            actions=action_filter,
            limit=page_size,
            offset=offset,
        )
        page_events = extract_audit_events(response)
        raw_events.extend(page_events)
        log.info("  [%s] page %d: %d events (total %d)", bg_name, page + 1, len(page_events), len(raw_events))
        if len(page_events) < page_size:
            break
    return raw_events


def collect_audit_events(
    *,
    host: str | None = None,
    token_env: str = "ANYPOINT_TOKEN",
    settings_path: str = "config/settings.json",
    page_size: int = 100,
    max_pages: int = 50,
) -> dict:
    auth = load_auth(host, token_env)
    settings = load_settings(settings_path)
    client = AnypointPlatformClient(auth)
    me = client.get_me()
    org_id, org_name = _org_from_me(me)
    if not org_id:
        raise RuntimeError("Could not determine organizationId from /accounts/api/me")

    # Determine collection window — incremental polling
    end = dt.datetime.now(dt.timezone.utc)
    max_lookback_hours = settings.get("audit", {}).get("max_lookback_hours", 24)
    max_lookback = end - dt.timedelta(hours=max_lookback_hours)

    conn = connect(project_path(settings["storage"]["sqlite_path"]))
    try:
        ensure_audit_schema(conn)
        last_end = get_last_audit_poll_end(conn)
    finally:
        conn.close()

    if last_end:
        try:
            start = dt.datetime.fromisoformat(last_end.replace("Z", "+00:00"))
            # Safety cap: never go further back than max_lookback_hours
            if start < max_lookback:
                log.info("Last poll end (%s) exceeds max_lookback_hours=%d — capping start", last_end, max_lookback_hours)
                start = max_lookback
            incremental = True
        except ValueError:
            log.warning("Could not parse last poll end '%s' — falling back to max_lookback", last_end)
            start = max_lookback
            incremental = False
    else:
        start = max_lookback
        incremental = False

    collected_at = dt.datetime.now(dt.timezone.utc)
    run_id = f"audit-{collected_at.strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex[:8]}"
    window_hours = (end - start).total_seconds() / 3600

    # Discover all business groups in the org hierarchy
    org_tree = client.walk_organization_tree(org_id)
    bg_list = [(bg.get("id", org_id), bg.get("name", org_name)) for bg in org_tree]
    if not bg_list:
        bg_list = [(org_id, org_name)]
    action_filter = _build_action_filter(settings)
    log.info(
        "Collecting audit events across %d BGs — window %.1fh (%s → %s) [incremental=%s, action_filter=%s]",
        len(bg_list), window_hours, start.isoformat()[:19], end.isoformat()[:19],
        incremental, f"{len(action_filter)} actions" if action_filter else "none",
    )

    conn = connect(project_path(settings["storage"]["sqlite_path"]))
    try:
        ensure_audit_schema(conn)
        poll_id = insert_audit_poll_run(
            conn,
            run_id=run_id,
            collected_at_utc=collected_at.isoformat(),
            org_id=org_id,
            org_name=org_name,
            window_start_utc=start.isoformat(),
            window_end_utc=end.isoformat(),
        )

        # Collect audit events from every business group
        all_events = []
        for bg_id, bg_name in bg_list:
            log.info("Querying audit log for BG: %s (%s)", bg_name, bg_id)
            raw_events = _paginate_audit(client, bg_id, bg_name, start, end, page_size, max_pages, action_filter)
            for raw in raw_events:
                event = normalize_audit_event(raw)
                event["bg_id"] = bg_id
                event["bg_name"] = bg_name
                event["risk_tier"] = classify_risk(event)
                event["risky"] = event["risk_tier"] in ("critical", "warning")
                all_events.append(event)

        inserted, risky_seen = insert_audit_events(conn, poll_id, all_events)
        alert_count = _upsert_risky_alerts(conn, run_id, [e for e in all_events if e.get("risky")])

        # Auto-resolve audit alerts older than the configured window
        max_age_days = settings.get("audit", {}).get("auto_resolve_days", 7)
        auto_resolved = resolve_old_audit_alerts(conn, max_age_days=max_age_days)
        if auto_resolved:
            log.info("Auto-resolved %d stale audit alerts (older than %d days)", auto_resolved, max_age_days)

        finish_audit_poll_run(conn, poll_id, seen=len(all_events), inserted=inserted, risky=risky_seen)
        conn.commit()
    except Exception as exc:
        conn.rollback()
        raise
    finally:
        conn.close()

    summary = {
        "run_id": run_id,
        "org_id": org_id,
        "org_name": org_name,
        "business_groups": len(bg_list),
        "window_utc": {"start": start.isoformat(), "end": end.isoformat()},
        "window_hours": round(window_hours, 2),
        "incremental": incremental,
        "events_seen": len(all_events),
        "events_inserted": inserted,
        "risky_events": risky_seen,
        "alerts_upserted": alert_count,
        "alerts_auto_resolved": auto_resolved,
    }
    log.info("Audit %s: %d BGs, %d events, %d inserted, %d risky", run_id, len(bg_list), len(all_events), inserted, risky_seen)
    return summary


def _upsert_risky_alerts(conn, run_id: str, events: list[dict]) -> int:
    count = 0
    for event in events:
        tier = event.get("risk_tier", "warning")
        action = event.get("action") or "action"
        obj = event.get("object_type") or "object"
        subaction = event.get("subaction", "")
        label = f"{subaction or action} {obj}" if subaction else f"{action} {obj}"
        title = f"{'CRITICAL: ' if tier == 'critical' else ''}{label.strip()}"
        detail = " | ".join(
            part for part in [
                event.get("product"),
                event.get("env_name"),
                event.get("object_name") or event.get("object_id"),
                event.get("user_name"),
            ] if part
        )
        upsert_alert(
            conn,
            alert_key=f"audit:{event['event_key']}",
            rule="audit_risky_change",
            severity="critical" if tier == "critical" else "warning",
            entitlement_key=None,
            title=title,
            detail=detail,
            run_id=run_id,
            payload=event,
        )
        count += 1
    return count
