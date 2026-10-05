from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

# Load .env file if present (no external dependency)
_env_file = ROOT / ".env"
if _env_file.exists():
    for line in _env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip())

from flask import Flask, abort, flash, jsonify, redirect, render_template, request, url_for

from inventory_store import (
    ensure_inventory_schema,
    inventory_counts,
    latest_inventory,
    list_changes,
)
from audit_store import audit_counts, backfill_objects_fields, ensure_audit_schema, list_audit_bgs, list_audit_events, list_audit_users
from usage_store import (
    alert_counts,
    connect,
    daily_usage_timeseries,
    get_alert,
    get_job_run,
    latest_entitlement_status,
    list_alerts,
    list_job_runs,
    update_alert_state,
    usage_totals_by_meter,
)
from runtime_services import refresh_app_statuses, start_app, stop_app, stop_all_apps_in_env
from schedule_store import (
    ensure_schedule_schema,
    create_env_schedule,
    delete_env_schedule,
    list_env_schedules,
    upsert_app_override,
    delete_app_override,
    list_app_overrides,
    create_one_time_action,
    cancel_one_time_action,
    list_one_time_actions,
    insert_schedule_log,
    list_schedule_logs,
    schedule_counts as get_schedule_counts,
)
from schedule_engine import ScheduleEngine
from watchmen_config import load_audit_rules, load_entitlements, load_settings, save_audit_rules, save_entitlements, save_settings, project_path
from watchmen_jobs import JobManager, SchedulerService
from watchmen_log import get_logger, setup_logging
from watchmen_services import calculate_status

setup_logging()
log = get_logger("web")

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "watchmen-dev-key-change-in-prod")
job_manager = JobManager()
scheduler = SchedulerService(job_manager)


import datetime as _dt


def _format_audit_time(raw: str) -> str:
    """Convert epoch-millis or ISO string to 'May 17, 20:15' format."""
    if not raw:
        return ""
    try:
        if raw.isdigit() and len(raw) >= 10:
            d = _dt.datetime.fromtimestamp(int(raw) / 1000, tz=_dt.timezone.utc)
        else:
            d = _dt.datetime.fromisoformat(raw.replace("Z", "+00:00"))
        return d.strftime("%b %d, %H:%M")
    except Exception:
        return raw[:16]


def _audit_time_relative(raw: str) -> str:
    """Return relative time string like '2h ago', '1d ago'."""
    if not raw:
        return ""
    try:
        if raw.isdigit() and len(raw) >= 10:
            d = _dt.datetime.fromtimestamp(int(raw) / 1000, tz=_dt.timezone.utc)
        else:
            d = _dt.datetime.fromisoformat(raw.replace("Z", "+00:00"))
        diff = _dt.datetime.now(_dt.timezone.utc) - d
        mins = int(diff.total_seconds() / 60)
        if mins < 1:
            return "just now"
        if mins < 60:
            return f"{mins}m ago"
        hours = mins // 60
        if hours < 24:
            return f"{hours}h ago"
        days = hours // 24
        return f"{days}d ago"
    except Exception:
        return ""


app.jinja_env.filters["audit_time"] = _format_audit_time
app.jinja_env.filters["audit_relative"] = _audit_time_relative
app.jinja_env.filters["commas"] = lambda v: f"{v:,.0f}" if isinstance(v, (int, float)) else str(v)


def db_conn():
    settings = load_settings()
    return connect(project_path(settings["storage"]["sqlite_path"]))



def run_health_checks() -> list[dict]:
    checks = []

    # SQLite reachable
    try:
        conn = db_conn()
        conn.execute("SELECT 1")
        conn.close()
        checks.append({"name": "SQLite", "ok": True, "detail": "reachable"})
    except Exception as exc:
        checks.append({"name": "SQLite", "ok": False, "detail": str(exc)})

    # Config valid
    try:
        load_settings()
        load_entitlements()
        checks.append({"name": "Config", "ok": True, "detail": "settings and entitlements loaded"})
    except Exception as exc:
        checks.append({"name": "Config", "ok": False, "detail": str(exc)})

    # Anypoint auth
    from anypoint_auth import auth_status
    auth_info = auth_status()
    checks.append({
        "name": "Anypoint Auth",
        "ok": auth_info["ok"],
        "detail": auth_info["detail"],
    })

    # SMTP config
    smtp_user = os.environ.get("SMTP_SENDER_EMAIL", "")
    smtp_pass = os.environ.get("SMTP_SENDER_PASSWORD", "")
    smtp_ok = bool(smtp_user and smtp_pass)
    checks.append({
        "name": "SMTP",
        "ok": smtp_ok,
        "detail": "credentials present" if smtp_ok else "SMTP_SENDER_EMAIL or SMTP_SENDER_PASSWORD missing",
    })

    # Latest usage_collect job
    try:
        conn = db_conn()
        rows = list_job_runs(conn, limit=1)
        conn.close()
        if rows:
            last = rows[0]
            checks.append({
                "name": "Last Job",
                "ok": last["status"] == "success",
                "detail": f"{last['job_name']} {last['status']} at {last['started_at_utc']}",
            })
        else:
            checks.append({"name": "Last Job", "ok": True, "detail": "no jobs run yet"})
    except Exception as exc:
        checks.append({"name": "Last Job", "ok": False, "detail": str(exc)})

    return checks


def _build_activity_feed(conn, limit: int = 8) -> list[dict]:
    """Merge risky audit events + inventory changes into a unified chronological feed.

    Caps each source to half the limit so one source can't dominate the feed.
    """
    import datetime as dt
    cutoff = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=24)).isoformat()
    per_source = limit  # fetch more per source, then trim the merged list

    # Risky audit events from last 24h
    audit_rows = conn.execute(
        """SELECT event_time_utc, action, object_type, object_name, user_name, env_name, risk_tier, subaction
        FROM audit_event
        WHERE risky = 1 AND COALESCE(event_time_utc, inserted_at_utc) >= ?
        ORDER BY COALESCE(event_time_utc, inserted_at_utc) DESC
        LIMIT ?""",
        (cutoff, per_source),
    ).fetchall()
    feed = []
    for r in audit_rows:
        tier = r["risk_tier"] if r["risk_tier"] else "warning"
        sub = r["subaction"] if r["subaction"] else ""
        label = sub or r["action"]
        feed.append({
            "time": r["event_time_utc"],
            "source": "audit",
            "icon": "!!" if tier == "critical" else "!",
            "summary": f"{r['user_name'] or 'system'}: {label} {r['object_type']} {r['object_name']}".strip(),
            "env": r["env_name"] or "",
            "severity": "critical" if tier == "critical" else "warn",
        })

    # Inventory changes from last 24h
    inv_rows = conn.execute(
        """SELECT c.change_type, c.entity_type, c.entity_key, c.detected_at_utc
        FROM inventory_change c
        WHERE c.detected_at_utc >= ?
        ORDER BY c.detected_at_utc DESC
        LIMIT ?""",
        (cutoff, per_source),
    ).fetchall()
    for r in inv_rows:
        key_short = r["entity_key"].rsplit(":", 1)[-1] if ":" in r["entity_key"] else r["entity_key"]
        feed.append({
            "time": r["detected_at_utc"],
            "source": "inventory",
            "icon": "+" if r["change_type"] == "added" else "-" if r["change_type"] == "removed" else "~",
            "summary": f"{r['entity_type']} {key_short} {r['change_type']}",
            "env": "",
            "severity": "ok" if r["change_type"] == "added" else "critical" if r["change_type"] == "removed" else "warn",
        })

    feed.sort(key=lambda x: x["time"], reverse=True)
    return feed[:limit]


@app.get("/")
def dashboard():
    payload = calculate_status(markdown_output="docs/latest-usage-status.md")
    statuses = payload["statuses"]
    conn = db_conn()
    try:
        ensure_inventory_schema(conn)
        ensure_audit_schema(conn)
        a_counts = alert_counts(conn)
        inv_counts = inventory_counts(conn)
        au_counts = audit_counts(conn)
        activity = _build_activity_feed(conn)

        # --- Daily consumption bar-chart data ---
        settings = load_settings()
        entitlements = load_entitlements()
        contract = settings.get("contract", {})
        start_date = contract.get("start_date", "2026-05-04")
        end_date = contract.get("end_date", "2027-05-06")
        d_start = _dt.date.fromisoformat(start_date)
        d_end = _dt.date.fromisoformat(end_date)
        contract_days = max((d_end - d_start).days, 1)

        # Look up limits and compute daily budgets
        limits_by_key = {lim["key"]: lim for lim in entitlements.get("limits", [])}
        meter_keys = ["mule_messages", "data_throughput"]
        daily_budgets = {}
        for mk in meter_keys:
            lim = limits_by_key.get(mk, {})
            limit_val = lim.get("limit", 0)
            # data_throughput limit is in GB but API returns bytes
            daily_budgets[mk] = limit_val / contract_days if limit_val else 0

        # Fetch daily data with rolling window
        now_utc = _dt.datetime.now(_dt.timezone.utc)
        # Get days_limit from config, default to 15
        dashboard_cfg = settings.get("dashboard", {})
        days_limit = dashboard_cfg.get("days_limit", 15)
        since = (now_utc - _dt.timedelta(days=30)).strftime("%Y-%m-%d")
        until = now_utc.strftime("%Y-%m-%d")
        timeseries = daily_usage_timeseries(conn, meter_keys, since, until, days_limit=days_limit)

        # Convert data_throughput from bytes to GB
        bytes_to_gb = 1 / (1024 ** 3)
        for pt in timeseries.get("data_throughput", []):
            pt["value"] = pt["value"] * bytes_to_gb
    finally:
        conn.close()

    critical = sum(1 for item in statuses if item["severity"] == "critical")
    warning = sum(1 for item in statuses if item["severity"] == "warning")
    open_alerts = a_counts.get("open", 0)
    health = run_health_checks()
    return render_template(
        "dashboard.html",
        statuses=statuses,
        critical=critical,
        warning=warning,
        scheduler=scheduler.status(),
        running=job_manager.running(),
        jobs=job_manager.recent_runs(8),
        health=health,
        open_alerts=open_alerts,
        inv_counts=inv_counts,
        audit_counts=au_counts,
        activity=activity,
        timeseries=timeseries,
        daily_budgets=daily_budgets,
    )


@app.get("/health")
def health():
    checks = run_health_checks()
    all_ok = all(c["ok"] for c in checks)
    return jsonify({"healthy": all_ok, "checks": checks}), 200 if all_ok else 503


@app.get("/jobs")
def jobs():
    return render_template(
        "jobs.html",
        jobs=job_manager.recent_runs(50),
        running=job_manager.running(),
        scheduler=scheduler.status(),
        definitions=job_manager.definitions().keys(),
        job_meta=job_manager.metadata(),
    )


@app.get("/jobs/<run_id>/status")
def job_status(run_id: str):
    conn = db_conn()
    try:
        job = get_job_run(conn, run_id)
    finally:
        conn.close()
    if job is None:
        abort(404)
    return jsonify({"run_id": run_id, "status": job["status"]})


@app.get("/jobs/<run_id>")
def job_detail(run_id: str):
    conn = db_conn()
    try:
        job = get_job_run(conn, run_id)
    finally:
        conn.close()
    if job is None:
        abort(404)
    return render_template("job_detail.html", job=job)


@app.post("/jobs/run/<job_name>")
def run_job(job_name: str):
    try:
        run_id = job_manager.start(job_name, requested_by="web")
    except RuntimeError as exc:
        log.warning("Job start blocked: %s", exc)
        if request.headers.get("Accept") == "application/json":
            return jsonify({"error": str(exc)}), 409
        return redirect(url_for("jobs"))
    if request.headers.get("Accept") == "application/json":
        return jsonify({"run_id": run_id})
    return redirect(url_for("jobs"))


@app.post("/scheduler/start")
def scheduler_start():
    scheduler.start()
    return redirect(request.referrer or url_for("dashboard"))


@app.post("/scheduler/stop")
def scheduler_stop():
    scheduler.stop()
    return redirect(request.referrer or url_for("dashboard"))


@app.post("/settings/scheduler")
def settings_update_scheduler():
    try:
        value = max(int(request.form.get("interval_value", 12)), 5)
    except (ValueError, TypeError):
        abort(400, "Interval value must be an integer.")
    unit = request.form.get("interval_unit", "hours")
    if unit not in ("minutes", "hours", "days"):
        abort(400, "Unit must be minutes, hours, or days.")
    settings = load_settings()
    settings.setdefault("scheduler", {})["interval_value"] = value
    settings["scheduler"]["interval_unit"] = unit
    save_settings(settings)
    # Force scheduler to reload on next cycle (already happens via _load_interval)
    return redirect(url_for("settings_page"))


def _ensure_audit_rules() -> dict:
    """Load audit rules from file, or create from defaults if missing."""
    rules = load_audit_rules()
    if rules is None:
        from audit_normalizer import _DEFAULT_ACTIONS, _DEFAULT_COMBOS
        rules = {
            "actions": {tier: sorted(acts) for tier, acts in _DEFAULT_ACTIONS.items()},
            "combos": {
                tier: [{"action": a, "target": t} for a, t in sorted(combos)]
                for tier, combos in _DEFAULT_COMBOS.items()
            },
        }
        save_audit_rules(rules)
    return rules


@app.post("/settings/audit-rules/add")
def audit_rules_add():
    tier = request.form.get("tier", "").strip()
    rule_type = request.form.get("rule_type", "").strip()
    action_val = request.form.get("action", "").strip().lower()
    target_val = request.form.get("target", "").strip().lower()

    if tier not in ("critical", "warning", "info"):
        abort(400, "Invalid tier.")
    if not action_val:
        abort(400, "Action is required.")

    rules = _ensure_audit_rules()

    if rule_type == "combo":
        if not target_val:
            abort(400, "Target is required for combo rules.")
        combos = rules.setdefault("combos", {}).setdefault(tier, [])
        entry = {"action": action_val, "target": target_val}
        if entry not in combos:
            combos.append(entry)
    else:
        actions = rules.setdefault("actions", {}).setdefault(tier, [])
        if action_val not in actions:
            actions.append(action_val)

    save_audit_rules(rules)
    from audit_normalizer import invalidate_rules_cache
    invalidate_rules_cache()
    return redirect(url_for("settings_page") + "#audit-rules")


@app.post("/settings/audit-rules/remove")
def audit_rules_remove():
    tier = request.form.get("tier", "").strip()
    rule_type = request.form.get("rule_type", "").strip()
    action_val = request.form.get("action", "").strip().lower()
    target_val = request.form.get("target", "").strip().lower()

    if tier not in ("critical", "warning", "info"):
        abort(400, "Invalid tier.")

    rules = _ensure_audit_rules()

    if rule_type == "combo":
        combos = rules.get("combos", {}).get(tier, [])
        rules["combos"][tier] = [c for c in combos if not (c["action"] == action_val and c["target"] == target_val)]
    else:
        actions = rules.get("actions", {}).get(tier, [])
        rules["actions"][tier] = [a for a in actions if a != action_val]

    save_audit_rules(rules)
    from audit_normalizer import invalidate_rules_cache
    invalidate_rules_cache()
    return redirect(url_for("settings_page") + "#audit-rules")


@app.post("/settings/exclude-actions/add")
def exclude_actions_add():
    action_val = request.form.get("action", "").strip().lower()
    if not action_val:
        abort(400, "Action is required.")
    settings = load_settings()
    exclude = settings.setdefault("audit", {}).setdefault("exclude_actions", [])
    if action_val not in exclude:
        exclude.append(action_val)
    save_settings(settings)
    from audit_services import _build_action_filter  # noqa: F401 — trigger cache bust on next collect
    return redirect(url_for("settings_page") + "#audit-rules")


@app.post("/settings/exclude-actions/remove")
def exclude_actions_remove():
    action_val = request.form.get("action", "").strip().lower()
    settings = load_settings()
    exclude = settings.get("audit", {}).get("exclude_actions", [])
    settings["audit"]["exclude_actions"] = [a for a in exclude if a != action_val]
    save_settings(settings)
    return redirect(url_for("settings_page") + "#audit-rules")


@app.get("/alerts")
def alerts_page():
    state_filter = request.args.get("state")
    conn = db_conn()
    try:
        alerts = list_alerts(conn, state=state_filter if state_filter else None)
        counts = alert_counts(conn)
    finally:
        conn.close()
    return render_template("alerts.html", alerts=alerts, counts=counts, current_filter=state_filter)


@app.get("/alerts/<int:alert_id>")
def alert_detail(alert_id: int):
    conn = db_conn()
    try:
        alert = get_alert(conn, alert_id)
    finally:
        conn.close()
    if alert is None:
        abort(404)
    return render_template("alert_detail.html", alert=alert)


@app.post("/alerts/<int:alert_id>/state")
def alert_change_state(alert_id: int):
    new_state = request.form.get("state", "")
    if new_state not in ("acknowledged", "resolved", "suppressed", "open"):
        abort(400)
    conn = db_conn()
    try:
        ok = update_alert_state(conn, alert_id, new_state)
        conn.commit()
    finally:
        conn.close()
    if not ok:
        abort(404)
    return redirect(request.referrer or url_for("alerts_page"))


@app.get("/settings")
def settings_page():
    entitlements = load_entitlements()
    settings = load_settings()
    audit_rules = load_audit_rules()
    if audit_rules is None:
        # Build from hardcoded defaults for display
        from audit_normalizer import _DEFAULT_ACTIONS, _DEFAULT_COMBOS
        audit_rules = {
            "actions": {tier: sorted(acts) for tier, acts in _DEFAULT_ACTIONS.items()},
            "combos": {
                tier: [{"action": a, "target": t} for a, t in sorted(combos)]
                for tier, combos in _DEFAULT_COMBOS.items()
            },
        }
    return render_template("settings.html", entitlements=entitlements, settings=settings, scheduler_status=scheduler.status(), audit_rules=audit_rules)


@app.post("/settings/policy")
def settings_update_policy():
    thresholds_str = request.form.get("default_thresholds_percent", "")
    try:
        thresholds = [int(x.strip()) for x in thresholds_str.split(",") if x.strip()]
    except ValueError:
        abort(400, "Invalid thresholds format. Must be comma-separated integers.")
    entitlements = load_entitlements()
    entitlements.setdefault("policy", {})["default_thresholds_percent"] = thresholds
    save_entitlements(entitlements)
    return redirect(url_for("settings_page"))


@app.post("/settings/limit/<limit_key>")
def settings_update_limit(limit_key: str):
    new_limit_str = request.form.get("limit", "")
    new_status = request.form.get("status", "")
    try:
        val = float(new_limit_str.strip())
        new_limit = int(val) if val == int(val) else val
    except (ValueError, OverflowError):
        abort(400, "Invalid limit format. Must be a number.")

    entitlements = load_entitlements()
    for limit in entitlements.get("limits", []):
        if limit["key"] == limit_key:
            limit["limit"] = new_limit
            if new_status:
                limit["status"] = new_status
            break
    save_entitlements(entitlements)
    return redirect(url_for("settings_page"))


@app.post("/settings/system")
def settings_update_system():
    settings = load_settings()
    
    start_date = request.form.get("start_date", "").strip()
    end_date = request.form.get("end_date", "").strip()
    
    if not start_date or not end_date:
        abort(400, "Start date and end date are required (YYYY-MM-DD).")

    from datetime import date as _date
    try:
        _date.fromisoformat(start_date)
        _date.fromisoformat(end_date)
    except ValueError:
        abort(400, "Invalid date format. Must be YYYY-MM-DD.")

    settings.setdefault("contract", {})["start_date"] = start_date
    settings["contract"]["end_date"] = end_date

    emails = [e.strip() for e in request.form.get("email_to", "").split(",") if e.strip()]
    settings.setdefault("alerts", {}).setdefault("email", {})["to"] = emails

    save_settings(settings)
    return redirect(url_for("settings_page"))


@app.get("/inventory")
def inventory_page():
    conn = db_conn()
    try:
        ensure_inventory_schema(conn)
        inv = latest_inventory(conn)
        counts = inventory_counts(conn)
        changes = list_changes(conn, limit=25)
    finally:
        conn.close()
    env_filter = request.args.get("env")
    if env_filter:
        inv["applications"] = [a for a in inv["applications"] if a["env_id"] == env_filter]
        inv["api_instances"] = [a for a in inv["api_instances"] if a["env_id"] == env_filter]

    # Parse extra_json to extract deployment_id for CH2 apps (needed for start/stop)
    for app_row in inv.get("applications", []):
        extra = app_row.get("extra_json", "{}")
        if isinstance(extra, str):
            try:
                extra = json.loads(extra)
            except (json.JSONDecodeError, TypeError):
                extra = {}
        app_row["_extra"] = extra

    return render_template(
        "inventory.html",
        inventory=inv,
        counts=counts,
        changes=changes,
        current_env=env_filter,
    )


@app.get("/inventory/changes")
def inventory_changes_page():
    conn = db_conn()
    try:
        ensure_inventory_schema(conn)
        ensure_audit_schema(conn)
        changes = list_changes(conn, limit=100)
        _enrich_changes_with_user(conn, changes)
    finally:
        conn.close()
    return render_template("inventory_changes.html", changes=changes)


def _enrich_changes_with_user(conn, changes: list[dict]) -> None:
    """Deterministic match: find the audit user who caused each inventory change.

    Matches by object_id (from audit objects[] array) against the inventory
    entity's API ID or app domain, scoped to the same environment.
    Only populates 'changed_by' when a definitive match exists.
    """
    # Run backfill once to ensure existing audit rows have object_id/env_id
    backfill_objects_fields(conn)

    for ch in changes:
        ch["changed_by"] = ""
        env_id = ch.get("env_id", "")
        if not env_id:
            continue

        # Extract identifiers from the inventory change to match against audit
        payload = ch.get("new") or ch.get("old") or {}
        match_ids = set()

        # api_instance: api_id is the numeric ID that appears in audit objectId
        api_id = payload.get("api_id")
        if api_id:
            match_ids.add(str(api_id))

        # application: domain is the app name
        domain = payload.get("domain", "")
        if domain:
            match_ids.add(domain)

        # Also try from entity_key (e.g. "env_id:20917364" or "ch2:env_id:app-name")
        parts = ch.get("entity_key", "").split(":")
        if len(parts) == 2:
            match_ids.add(parts[1])  # api_id from "env_id:api_id"
        elif len(parts) >= 3:
            match_ids.add(parts[-1])  # app domain from "ch2:env_id:domain"

        if not match_ids:
            continue

        # Query: find audit event with matching env_id + object_id
        placeholders = ",".join("?" for _ in match_ids)
        row = conn.execute(
            f"""SELECT user_name FROM audit_event
            WHERE env_id = ?
            AND object_id IN ({placeholders})
            AND user_name != ''
            ORDER BY CAST(COALESCE(NULLIF(event_time_utc, ''), '0') AS INTEGER) DESC
            LIMIT 1""",
            (env_id, *match_ids),
        ).fetchone()
        if row:
            ch["changed_by"] = row["user_name"]


# -- Runtime Management (start/stop apps) --

def _get_api_client():
    """Create an authenticated AnypointPlatformClient for runtime operations."""
    from anypoint_auth import load_auth
    from anypoint_platform_api import AnypointPlatformClient
    auth = load_auth()
    return AnypointPlatformClient(auth)


def _check_app_status(client, source: str, org_id: str, env_id: str, domain: str, deployment_id: str) -> str:
    """Quick live status check after a start/stop action."""
    try:
        if source == "ch2" and deployment_id:
            info = client.get_ch2_deployment(org_id, env_id, deployment_id)
            return info.get("application", {}).get("status", info.get("status", "unknown"))
        elif source == "cloudhub":
            info = client.get_cloudhub_app(org_id, env_id, domain)
            return info.get("status", "unknown")
    except Exception:
        pass
    return "unknown"


@app.post("/runtime/app/start")
def runtime_app_start():
    source = request.form.get("source", "")
    org_id = request.form.get("org_id", "")
    env_id = request.form.get("env_id", "")
    domain = request.form.get("domain", "")
    deployment_id = request.form.get("deployment_id", "")

    if not all([source, org_id, env_id, domain]):
        abort(400, "Missing required fields: source, org_id, env_id, domain")

    try:
        client = _get_api_client()
        result = start_app(client, source=source, org_id=org_id, env_id=env_id,
                           domain=domain, deployment_id=deployment_id)
        if result["ok"]:
            live = _check_app_status(client, source, org_id, env_id, domain, deployment_id)
            flash(f"Start requested for {domain} — current status: {live}", "success")
        else:
            flash(f"Failed to start {domain}: {result['detail']}", "error")
    except Exception as exc:
        log.error("runtime_app_start failed: %s", exc)
        result = {"ok": False, "detail": str(exc)}
        flash(f"Failed to start {domain}: {exc}", "error")

    if request.headers.get("Accept") == "application/json":
        return jsonify(result), 200 if result["ok"] else 500
    return redirect(request.referrer or url_for("inventory_page"))


@app.post("/runtime/app/stop")
def runtime_app_stop():
    source = request.form.get("source", "")
    org_id = request.form.get("org_id", "")
    env_id = request.form.get("env_id", "")
    domain = request.form.get("domain", "")
    deployment_id = request.form.get("deployment_id", "")

    if not all([source, org_id, env_id, domain]):
        abort(400, "Missing required fields: source, org_id, env_id, domain")

    try:
        client = _get_api_client()
        result = stop_app(client, source=source, org_id=org_id, env_id=env_id,
                          domain=domain, deployment_id=deployment_id)
        if result["ok"]:
            live = _check_app_status(client, source, org_id, env_id, domain, deployment_id)
            flash(f"Stop requested for {domain} — current status: {live}", "success")
        else:
            flash(f"Failed to stop {domain}: {result['detail']}", "error")
    except Exception as exc:
        log.error("runtime_app_stop failed: %s", exc)
        result = {"ok": False, "detail": str(exc)}
        flash(f"Failed to stop {domain}: {exc}", "error")

    if request.headers.get("Accept") == "application/json":
        return jsonify(result), 200 if result["ok"] else 500
    return redirect(request.referrer or url_for("inventory_page"))


@app.post("/runtime/env/stop-all")
def runtime_env_stop_all():
    org_id = request.form.get("org_id", "")
    env_id = request.form.get("env_id", "")

    if not all([org_id, env_id]):
        abort(400, "Missing required fields: org_id, env_id")

    try:
        client = _get_api_client()
        result = stop_all_apps_in_env(client, org_id=org_id, env_id=env_id)
        if result["ok"]:
            flash(f"Stop All completed: {result['stopped']} stopped, {result['skipped']} already stopped", "success")
        else:
            flash(f"Stop All partial: {result['stopped']} stopped, {result['failed']} failed", "warning")
    except Exception as exc:
        log.error("runtime_env_stop_all failed: %s", exc)
        result = {"ok": False, "detail": str(exc)}
        flash(f"Stop All failed: {exc}", "error")

    if request.headers.get("Accept") == "application/json":
        return jsonify(result), 200 if result["ok"] else 500
    return redirect(request.referrer or url_for("inventory_page"))


@app.post("/runtime/refresh-statuses")
def runtime_refresh_statuses():
    conn = db_conn()
    try:
        ensure_inventory_schema(conn)
        inv = latest_inventory(conn)
        apps = inv.get("applications", [])
        if not apps:
            flash("No apps in inventory to refresh.", "warning")
            return redirect(request.referrer or url_for("inventory_page"))

        client = _get_api_client()
        result = refresh_app_statuses(client, apps, conn)
        conn.commit()

        if result["errors"] == 0:
            flash(f"Statuses refreshed: {result['refreshed']} updated, {result['unchanged']} unchanged", "success")
        else:
            flash(f"Statuses refreshed: {result['refreshed']} updated, {result['errors']} errors", "warning")
    except Exception as exc:
        log.error("refresh_statuses failed: %s", exc)
        flash(f"Refresh failed: {exc}", "error")
    finally:
        conn.close()

    if request.headers.get("Accept") == "application/json":
        return jsonify(result)
    return redirect(request.referrer or url_for("inventory_page"))


@app.get("/audit")
def audit_page():
    tier = request.args.get("tier", "critical")
    risky_only = request.args.get("risky") == "1"
    user_filter = request.args.get("user", "")
    bg_filter = request.args.get("bg", "")
    tier_query = "" if tier == "all" else tier
    conn = db_conn()
    try:
        ensure_audit_schema(conn)
        events = list_audit_events(conn, risk_tier=tier_query, risky_only=risky_only, user_name=user_filter, bg_name=bg_filter, limit=200)
        counts = audit_counts(conn)
        users = list_audit_users(conn)
        bgs = list_audit_bgs(conn)
    finally:
        conn.close()
    current_filter = tier if tier else ("risky" if risky_only else "")
    return render_template("audit.html", events=events, counts=counts, current_filter=current_filter, users=users, current_user=user_filter, bgs=bgs, current_bg=bg_filter)



@app.get("/api/status")
def api_status():
    conn = db_conn()
    try:
        statuses = latest_entitlement_status(conn)
    finally:
        conn.close()
    return jsonify({"statuses": statuses, "scheduler": scheduler.status(), "running": job_manager.running()})


# ---------------------------------------------------------------------------
# Schedule engine singleton
# ---------------------------------------------------------------------------

_schedule_engine: ScheduleEngine | None = None


def _get_schedule_engine() -> ScheduleEngine:
    global _schedule_engine
    if _schedule_engine is None:
        settings = load_settings()
        sched_cfg = settings.get("schedule", {})
        poll_interval = sched_cfg.get("poll_interval", 30)
        _schedule_engine = ScheduleEngine(
            db_opener=db_conn,
            client_factory=_get_api_client,
            poll_interval=poll_interval,
        )
        if sched_cfg.get("auto_start", False):
            _schedule_engine.start()
    return _schedule_engine


# ---------------------------------------------------------------------------
# Schedule routes
# ---------------------------------------------------------------------------

@app.get("/schedules")
def schedules_page():
    import time as _time
    conn = db_conn()
    try:
        ensure_schedule_schema(conn)
        ensure_inventory_schema(conn)
        env_scheds = list_env_schedules(conn)
        app_customs = list_app_overrides(conn, override_type="custom")
        app_always_on = list_app_overrides(conn, override_type="always_on")
        one_times = list_one_time_actions(conn, limit=30)
        logs = list_schedule_logs(conn, limit=50)
        counts = get_schedule_counts(conn)
        inv = latest_inventory(conn)
    finally:
        conn.close()

    # Parse extra_json for deployment_id (same as inventory route)
    for app_row in inv.get("applications", []):
        extra = app_row.get("extra_json", "{}")
        if isinstance(extra, str):
            try:
                extra = json.loads(extra)
            except (json.JSONDecodeError, TypeError):
                extra = {}
        app_row["_extra"] = extra

    engine = _get_schedule_engine()
    # Build short timezone label (Windows returns verbose localized names)
    tz_name = _time.strftime("%Z") or ""
    if len(tz_name) > 5:
        # Derive from UTC offset instead
        import datetime as _dt
        _local = _dt.datetime.now(_dt.timezone.utc).astimezone()
        _off = _local.utcoffset().total_seconds() / 3600
        _offset_map = {
            -8: "PST", -7: "PDT", -6: "MDT", -5: "CDT",
            -4: "EDT", 0: "UTC", 1: "CET", 2: "CEST",
        }
        tz_name = _offset_map.get(int(_off), f"UTC{int(_off):+d}")
    tz_name = tz_name or "UTC"

    import datetime as _dt2
    _server_now = _dt2.datetime.now()
    server_time = _server_now.strftime("%H:%M")
    server_iso = _server_now.isoformat()

    return render_template("schedules.html",
                           env_schedules=env_scheds,
                           app_customs=app_customs,
                           app_always_on=app_always_on,
                           one_times=one_times,
                           logs=logs,
                           counts=counts,
                           environments=inv.get("environments", []),
                           applications=inv.get("applications", []),
                           engine_running=engine.running,
                           engine_interval=engine.poll_interval,
                           tz_name=tz_name,
                           server_time=server_time,
                           server_iso=server_iso)


@app.post("/schedules/env")
def schedule_env_upsert():
    conn = db_conn()
    try:
        ensure_schedule_schema(conn)
        env_id = request.form["env_id"]
        env_name = request.form.get("env_name", "")
        org_id = request.form.get("org_id", "")
        start_time = request.form.get("start_time", "")
        stop_time = request.form.get("stop_time", "")
        days = request.form.getlist("days")
        days_str = ",".join(days) if days else "0,1,2,3,4"
        enabled = "enabled" in request.form or not request.form.get("_disable")

        create_env_schedule(conn, env_id=env_id, env_name=env_name,
                            org_id=org_id, start_time=start_time,
                            stop_time=stop_time, days_of_week=days_str,
                            enabled=enabled)
        conn.commit()
        flash(f"Schedule entry added for {env_name or env_id}", "success")
    except Exception as exc:
        flash(f"Error saving schedule: {exc}", "error")
    finally:
        conn.close()
    return redirect(url_for("schedules_page"))


@app.post("/schedules/env/<int:schedule_id>/delete")
def schedule_env_delete(schedule_id):
    conn = db_conn()
    try:
        ensure_schedule_schema(conn)
        deleted = delete_env_schedule(conn, schedule_id)
        conn.commit()
        if deleted:
            flash("Environment schedule removed", "success")
        else:
            flash("Schedule not found", "warning")
    finally:
        conn.close()
    return redirect(url_for("schedules_page"))


@app.post("/schedules/app")
def schedule_app_upsert():
    conn = db_conn()
    try:
        ensure_schedule_schema(conn)
        source = request.form["source"]
        env_id = request.form["env_id"]
        domain = request.form["domain"]
        org_id = request.form.get("org_id", "")
        deployment_id = request.form.get("deployment_id", "")
        override_type = request.form["override_type"]

        kwargs = dict(source=source, env_id=env_id, domain=domain,
                      org_id=org_id, deployment_id=deployment_id,
                      override_type=override_type)

        if override_type == "custom":
            kwargs["start_time"] = request.form["start_time"]
            kwargs["stop_time"] = request.form["stop_time"]
            days = request.form.getlist("days")
            kwargs["days_of_week"] = ",".join(days) if days else "0,1,2,3,4"

        upsert_app_override(conn, **kwargs)
        conn.commit()
        label = "Always-on" if override_type == "always_on" else "Custom schedule"
        flash(f"{label} saved for {domain}", "success")
    except Exception as exc:
        flash(f"Error saving app override: {exc}", "error")
    finally:
        conn.close()
    return redirect(url_for("schedules_page"))


@app.post("/schedules/app/<int:override_id>/delete")
def schedule_app_delete(override_id):
    conn = db_conn()
    try:
        ensure_schedule_schema(conn)
        deleted = delete_app_override(conn, override_id)
        conn.commit()
        flash("Override removed" if deleted else "Override not found",
              "success" if deleted else "warning")
    finally:
        conn.close()
    return redirect(url_for("schedules_page"))


@app.post("/schedules/one-time")
def schedule_one_time_create():
    conn = db_conn()
    try:
        ensure_schedule_schema(conn)
        target_type = request.form["target_type"]
        action = request.form["action"]
        env_id = request.form["env_id"]
        scheduled_at = request.form["scheduled_at"]
        source = request.form.get("source", "")
        domain = request.form.get("domain", "")
        org_id = request.form.get("org_id", "")
        deployment_id = request.form.get("deployment_id", "")

        # Normalize: env-level stop → stop_all
        if target_type == "env" and action == "stop":
            action = "stop_all"

        create_one_time_action(conn, target_type=target_type, action=action,
                               env_id=env_id, scheduled_at=scheduled_at,
                               source=source, domain=domain, org_id=org_id,
                               deployment_id=deployment_id)
        conn.commit()
        flash(f"One-time {action} scheduled for {scheduled_at}", "success")
    except Exception as exc:
        flash(f"Error scheduling action: {exc}", "error")
    finally:
        conn.close()
    return redirect(url_for("schedules_page"))


@app.post("/schedules/one-time/<int:action_id>/cancel")
def schedule_one_time_cancel(action_id):
    conn = db_conn()
    try:
        ensure_schedule_schema(conn)
        cancelled = cancel_one_time_action(conn, action_id)
        conn.commit()
        flash("Action cancelled" if cancelled else "Could not cancel (not pending)",
              "success" if cancelled else "warning")
    finally:
        conn.close()
    return redirect(url_for("schedules_page"))


@app.get("/schedules/log")
def schedule_log_page():
    conn = db_conn()
    try:
        ensure_schedule_schema(conn)
        logs = list_schedule_logs(conn, limit=200)
        from inventory_store import latest_inventory
        inv = latest_inventory(conn)
    finally:
        conn.close()
    return render_template("schedule_log.html", logs=logs,
                           environments=inv.get("environments", []))


@app.post("/schedules/engine/start")
def schedule_engine_start():
    engine = _get_schedule_engine()
    engine.start()
    flash("Schedule engine started", "success")
    return redirect(url_for("schedules_page"))


@app.post("/schedules/engine/stop")
def schedule_engine_stop():
    engine = _get_schedule_engine()
    engine.stop()
    flash("Schedule engine stopped", "warning")
    return redirect(url_for("schedules_page"))


if __name__ == "__main__":
    host = os.environ.get("WATCHMEN_HOST", "0.0.0.0")
    port = int(os.environ.get("WATCHMEN_PORT", "5050"))
    log.info("Starting Watchmen web app on http://%s:%d", host, port)
    app.run(host=host, port=port, debug=False, use_reloader=False)
