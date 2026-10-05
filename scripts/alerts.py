"""Alert evaluation, deduplication, and dispatch.

Alert lifecycle:
  open -> acknowledged -> resolved
  open -> suppressed
  resolved -> open  (if condition recurs)

Email is sent only for new or severity-changed alerts that haven't been notified yet.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import smtplib
import sqlite3
import ssl
from email.message import EmailMessage

from usage_store import (
    list_alerts,
    mark_alerts_notified,
    resolve_stale_alerts,
    upsert_alert,
    resolve_old_audit_alerts,
)
from watchmen_config import project_path
from watchmen_log import get_logger

log = get_logger("alerts")


# ---------------------------------------------------------------------------
# Alert rules — each returns a list of candidate alerts
# ---------------------------------------------------------------------------

def _rule_threshold_crossed(statuses: list[dict]) -> list[dict]:
    """Fire when an entitlement crosses a configured threshold."""
    candidates = []
    for s in statuses:
        if s["severity"] not in ("warning", "critical"):
            continue
        candidates.append({
            "alert_key": f"threshold:{s['key']}:{s['threshold_percent']}",
            "rule": "threshold_crossed",
            "severity": s["severity"],
            "entitlement_key": s["key"],
            "title": f"{s['name']} at {s['percent_used']:.1f}% (threshold {s['threshold_percent']}%)",
            "detail": f"Usage {s['usage_value']:.4f} / {s['limit_value']:.4f} {s['unit']}",
            "payload": s,
        })
    return candidates


def _rule_projected_overage(statuses: list[dict]) -> list[dict]:
    """Fire when projected contract usage exceeds 100%."""
    candidates = []
    for s in statuses:
        kpis = s.get("kpis", {})
        if kpis.get("is_high_watermark"):
            continue
        pct = kpis.get("projected_contract_percent", 0)
        if pct < 100:
            continue
        candidates.append({
            "alert_key": f"projected_overage:{s['key']}",
            "rule": "projected_overage",
            "severity": "critical" if pct >= 150 else "warning",
            "entitlement_key": s["key"],
            "title": f"{s['name']} projected to reach {pct:.0f}% of contract limit",
            "detail": f"Exhaustion date: {kpis.get('projected_exhaustion_date', 'unknown')}",
            "payload": {"key": s["key"], "name": s["name"], "projected_percent": pct, "exhaustion_date": kpis.get("projected_exhaustion_date")},
        })
    return candidates


def _rule_monthly_budget_exceeded(statuses: list[dict]) -> list[dict]:
    """Fire when projected monthly usage exceeds the monthly budget."""
    candidates = []
    for s in statuses:
        kpis = s.get("kpis", {})
        if kpis.get("is_high_watermark"):
            continue
        ratio = kpis.get("month_ratio", 0)
        if ratio < 120:
            continue
        candidates.append({
            "alert_key": f"month_budget:{s['key']}",
            "rule": "monthly_budget_exceeded",
            "severity": "critical" if ratio >= 150 else "warning",
            "entitlement_key": s["key"],
            "title": f"{s['name']} monthly burn at {ratio:.0f}% of budget",
            "detail": f"Month usage: {kpis.get('month_usage', 0):.2f}, budget: {kpis.get('monthly_budget', 0):.2f}",
            "payload": {"key": s["key"], "name": s["name"], "month_ratio": ratio, "month_usage": kpis.get("month_usage"), "monthly_budget": kpis.get("monthly_budget")},
        })
    return candidates


def _rule_hwm_near_limit(statuses: list[dict]) -> list[dict]:
    """Fire when a HIGH_WATERMARK entitlement is at >=80% of its limit."""
    candidates = []
    for s in statuses:
        if s.get("usage_model") != "HIGH_WATERMARK":
            continue
        if s["percent_used"] < 80:
            continue
        candidates.append({
            "alert_key": f"hwm_near_limit:{s['key']}",
            "rule": "hwm_near_limit",
            "severity": "critical" if s["percent_used"] >= 95 else "warning",
            "entitlement_key": s["key"],
            "title": f"{s['name']} at {s['percent_used']:.0f}% of limit ({s['usage_value']:.0f}/{s['limit_value']:.0f})",
            "detail": f"Remaining capacity: {s['remaining']:.0f} {s['unit']}",
            "payload": {"key": s["key"], "name": s["name"], "percent_used": s["percent_used"], "remaining": s["remaining"]},
        })
    return candidates


def _rule_burn_acceleration(statuses: list[dict]) -> list[dict]:
    """Fire when 7-day burn rate is >50% higher than 30-day burn rate."""
    candidates = []
    for s in statuses:
        kpis = s.get("kpis", {})
        accel = kpis.get("burn_acceleration_pct")
        if accel is None or accel < 50:
            continue
        candidates.append({
            "alert_key": f"burn_accel:{s['key']}",
            "rule": "burn_acceleration",
            "severity": "warning",
            "entitlement_key": s["key"],
            "title": f"{s['name']} burn rate accelerating (+{accel:.0f}%)",
            "detail": f"7d burn: {kpis.get('burn_7d', 0):.4f}/day, 30d burn: {kpis.get('burn_30d', 0):.4f}/day",
            "payload": {"key": s["key"], "name": s["name"], "acceleration_pct": accel, "burn_7d": kpis.get("burn_7d"), "burn_30d": kpis.get("burn_30d")},
        })
    return candidates


def _rule_daily_consumption_60pct(statuses: list[dict]) -> list[dict]:
    """Fire when today's usage reaches 60% of the daily budget."""
    candidates = []
    for s in statuses:
        kpis = s.get("kpis", {})
        # Skip HIGH_WATERMARK metrics (they don't have daily budgets in the same sense)
        if s.get("usage_model") == "HIGH_WATERMARK":
            continue
        day_ratio = kpis.get("day_ratio", 0)
        if day_ratio < 60:
            continue
        # Determine severity based on how much of daily budget is consumed
        if day_ratio >= 90:
            severity = "critical"
        elif day_ratio >= 75:
            severity = "warning"
        else:
            severity = "warning"

        today_usage = kpis.get("today_usage", 0)
        daily_budget = kpis.get("daily_budget", 0)
        candidates.append({
            "alert_key": f"daily_60pct:{s['key']}",
            "rule": "daily_consumption_60pct",
            "severity": severity,
            "entitlement_key": s["key"],
            "title": f"{s['name']} daily consumption at {day_ratio:.1f}% ({today_usage:.0f}/{daily_budget:.0f} {s.get('unit', '')})",
            "detail": f"Today's usage {today_usage:.4f} has reached {day_ratio:.1f}% of daily budget {daily_budget:.4f}",
            "payload": {"key": s["key"], "name": s["name"], "today_usage": today_usage, "daily_budget": daily_budget, "day_ratio": day_ratio},
        })
    return candidates


ALL_RULES = [
    _rule_threshold_crossed,
    _rule_projected_overage,
    _rule_monthly_budget_exceeded,
    _rule_hwm_near_limit,
    _rule_burn_acceleration,
    _rule_daily_consumption_60pct,
]


# ---------------------------------------------------------------------------
# Core evaluate + dispatch
# ---------------------------------------------------------------------------

def evaluate_alerts(
    conn: sqlite3.Connection,
    statuses: list[dict],
    run_id: str,
) -> dict:
    """Run all alert rules, upsert results, auto-resolve stale alerts."""
    active_keys: set[str] = set()
    created = updated = unchanged = reopened = 0

    # MONITOR-tier meters have no contractual limit — track for awareness only,
    # never fire alerts on them.
    alertable = [
        s for s in statuses
        if s.get("usage_model") != "MONITOR" and float(s.get("limit_value", 0) or 0) > 0
    ]

    for rule_fn in ALL_RULES:
        candidates = rule_fn(alertable)
        for c in candidates:
            active_keys.add(c["alert_key"])
            result = upsert_alert(
                conn,
                alert_key=c["alert_key"],
                rule=c["rule"],
                severity=c["severity"],
                entitlement_key=c.get("entitlement_key"),
                title=c["title"],
                detail=c.get("detail"),
                run_id=run_id,
                payload=c.get("payload", {}),
            )
            action = result["action"]
            if action == "created":
                created += 1
            elif action == "updated":
                updated += 1
            elif action == "reopened":
                reopened += 1
            else:
                unchanged += 1

    resolved = resolve_stale_alerts(conn, active_keys)

    return {
        "created": created,
        "updated": updated,
        "reopened": reopened,
        "unchanged": unchanged,
        "resolved": resolved,
        "active_rules": len(active_keys),
    }


# ---------------------------------------------------------------------------
# Email dispatch — only for un-notified alerts
# ---------------------------------------------------------------------------

def load_dotenv(path: str = ".env") -> None:
    env_path = project_path(path)
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def send_alert_email(settings: dict, conn: sqlite3.Connection) -> int:
    """Send email for un-notified open alerts. Returns count of alerts notified."""
    email_cfg = settings["alerts"]["email"]
    if not email_cfg.get("enabled"):
        return 0

    pending = [a for a in list_alerts(conn, state="open") if not a.get("notified")]
    if not pending:
        return 0

    load_dotenv()
    username = os.environ.get(email_cfg["username_env"])
    password = os.environ.get(email_cfg["password_env"])
    smtp_host = os.environ.get(email_cfg["smtp_host_env"], "smtp.gmail.com")
    smtp_port = int(os.environ.get(email_cfg["smtp_port_env"], "465"))
    sender = os.environ.get(email_cfg.get("from_env", email_cfg["username_env"]), username)
    if not username or not password:
        log.warning("Email alerts enabled but SMTP credentials missing")
        return 0

    critical = [a for a in pending if a["severity"] == "critical"]
    warnings = [a for a in pending if a["severity"] == "warning"]

    subject = f"Watchmen: {len(critical)} critical, {len(warnings)} warning alert(s)"
    lines = ["Anypoint Platform Watchmen — New Alerts", ""]

    for a in pending:
        icon = "!!" if a["severity"] == "critical" else "!"
        lines.append(f"  [{icon}] {a['severity'].upper()} — {a['title']}")
        if a.get("detail"):
            lines.append(f"      {a['detail']}")
        lines.append("")

    lines.append("---")
    base_url = settings.get("app_base_url", "http://localhost:5050").rstrip("/")
    lines.append(f"Review and manage alerts at {base_url}/alerts")

    # Recipients come from settings.json alerts.email.to — editable via Settings UI
    recipients = list(email_cfg.get("to", []))
    if not recipients:
        log.warning("Email alerts enabled but no recipients configured (set to_env or 'to')")
        return 0

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = ", ".join(recipients)
    msg.set_content("\n".join(lines))

    try:
        if email_cfg.get("smtp_ssl", True):
            context = ssl.create_default_context()
            with smtplib.SMTP_SSL(smtp_host, smtp_port, context=context, timeout=30) as smtp:
                smtp.login(username, password)
                smtp.send_message(msg)
        else:
            with smtplib.SMTP(smtp_host, smtp_port, timeout=30) as smtp:
                smtp.starttls(context=ssl.create_default_context())
                smtp.login(username, password)
                smtp.send_message(msg)

        mark_alerts_notified(conn, [a["id"] for a in pending])
        log.info("Sent alert email for %d alerts", len(pending))
        return len(pending)
    except Exception as exc:
        log.error("Failed to send alert email: %s", exc)
        return 0


def write_file_alerts(settings: dict, conn: sqlite3.Connection) -> int:
    """Append un-notified alerts to the JSONL alert file and mark them notified."""
    pending = [a for a in list_alerts(conn, state="open") if not a.get("notified")]
    if not pending:
        return 0
    path = project_path(settings["alerts"]["file"]["path"])
    path.parent.mkdir(parents=True, exist_ok=True)
    now = dt.datetime.now(dt.timezone.utc).isoformat()
    with path.open("a", encoding="utf-8") as handle:
        for a in pending:
            handle.write(json.dumps({
                "alert_id": a["id"],
                "alert_key": a["alert_key"],
                "rule": a["rule"],
                "severity": a["severity"],
                "title": a["title"],
                "detail": a.get("detail"),
                "created_at_utc": a["created_at_utc"],
                "written_at_utc": now,
            }, sort_keys=True) + "\n")
    mark_alerts_notified(conn, [a["id"] for a in pending])
    return len(pending)


def dispatch_alerts(
    settings: dict,
    statuses: list[dict],
    run_id: str,
    conn: sqlite3.Connection,
) -> dict:
    """Evaluate alert rules, persist to DB, dispatch via configured channels."""
    eval_result = evaluate_alerts(conn, statuses, run_id)

    channels = settings["alerts"].get("enabled_channels", [])
    sent = {"file": 0, "email": 0}
    if "file" in channels:
        sent["file"] = write_file_alerts(settings, conn)
    if "email" in channels:
        sent["email"] = send_alert_email(settings, conn)

    return {**eval_result, "dispatched": sent}


def send_test_email(settings: dict) -> int:
    """Send a test email to verify SMTP configuration."""
    load_dotenv()
    email_cfg = settings["alerts"]["email"]
    username = os.environ.get(email_cfg["username_env"])
    password = os.environ.get(email_cfg["password_env"])
    smtp_host = os.environ.get(email_cfg["smtp_host_env"], "smtp.gmail.com")
    smtp_port = int(os.environ.get(email_cfg["smtp_port_env"], "465"))
    sender = os.environ.get(email_cfg.get("from_env", email_cfg["username_env"]), username)
    if not username or not password:
        raise RuntimeError("SMTP credentials missing from environment.")

    msg = EmailMessage()
    msg["Subject"] = "Watchmen SMTP test"
    msg["From"] = sender
    msg["To"] = ", ".join(email_cfg["to"])
    msg.set_content("This is a test email from Anypoint Platform Watchmen.\nSMTP configuration is working correctly.")

    if email_cfg.get("smtp_ssl", True):
        context = ssl.create_default_context()
        with smtplib.SMTP_SSL(smtp_host, smtp_port, context=context, timeout=30) as smtp:
            smtp.login(username, password)
            smtp.send_message(msg)
    else:
        with smtplib.SMTP(smtp_host, smtp_port, timeout=30) as smtp:
            smtp.starttls(context=ssl.create_default_context())
            smtp.login(username, password)
            smtp.send_message(msg)
    return 1
