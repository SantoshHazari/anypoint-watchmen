"""In-process job manager and lightweight scheduler."""

from __future__ import annotations

import datetime as dt
import json
import threading
import time
import traceback
import uuid
from typing import Callable

from usage_store import connect, create_job_run, finish_job_run, get_job_run, list_job_runs
from watchmen_config import load_settings, project_path
from watchmen_log import get_logger
from inventory_services import collect_inventory
from audit_services import collect_audit_events
from alerts import send_alert_email
from watchmen_services import calculate_status, collect_usage

log = get_logger("jobs")


JobFunc = Callable[[], dict]


# -- Job metadata: label and description shown in UI --

JOB_META = {
    "collect_all": {
        "label": "Collect All",
        "description": "Full pipeline: usage metrics, inventory, audit log, then recalculate status and alerts.",
    },
    "recalculate_status": {
        "label": "Recalculate Status",
        "description": "Recompute burn rates, projections, and alerts from existing usage data. No API calls.",
    },
    "collect_inventory": {
        "label": "Collect Inventory",
        "description": "Refresh platform inventory: business groups, environments, apps (CH1/CH2/hybrid), and API instances.",
    },
    "collect_audit": {
        "label": "Collect Audit Log",
        "description": "Pull recent audit events from Anypoint and classify risky changes.",
    },
}


class JobManager:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._running: dict[str, threading.Thread] = {}

    def definitions(self) -> dict[str, JobFunc]:
        return {
            "collect_all": _collect_all,
            "recalculate_status": lambda: _status_summary(calculate_status(write_alerts=True, markdown_output="docs/latest-usage-status.md")),
            "collect_inventory": collect_inventory,
            "collect_audit": _collect_audit,
        }

    def metadata(self) -> dict[str, dict]:
        """Return job metadata (label, description) keyed by job name."""
        return JOB_META

    EXCLUSIVE_JOBS = {
        "collect_all",
        "recalculate_status",
        "collect_inventory",
        "collect_audit",
    }

    def start(self, job_name: str, requested_by: str = "web") -> str:
        jobs = self.definitions()
        if job_name not in jobs:
            raise RuntimeError(f"Unknown job {job_name}")
        with self._lock:
            self._running = {rid: t for rid, t in self._running.items() if t.is_alive()}
            if job_name in self.EXCLUSIVE_JOBS:
                for rid in self._running:
                    if any(rid.startswith(f"job-{exclusive}-") for exclusive in self.EXCLUSIVE_JOBS):
                        raise RuntimeError(f"Exclusive job already running ({rid})")
        run_id = f"job-{job_name}-{dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex[:6]}"
        conn = connect(project_path(load_settings()["storage"]["sqlite_path"]))
        try:
            create_job_run(conn, run_id=run_id, job_name=job_name, requested_by=requested_by)
            conn.commit()
        finally:
            conn.close()
        log.info("Starting job %s (run_id=%s, requested_by=%s)", job_name, run_id, requested_by)
        thread = threading.Thread(target=self._run, args=(run_id, job_name, jobs[job_name]), daemon=True)
        with self._lock:
            self._running[run_id] = thread
        thread.start()
        return run_id

    def running(self) -> list[str]:
        with self._lock:
            self._running = {rid: thread for rid, thread in self._running.items() if thread.is_alive()}
            return list(self._running)

    def recent_runs(self, limit: int = 25) -> list[dict]:
        conn = connect(project_path(load_settings()["storage"]["sqlite_path"]))
        try:
            return list_job_runs(conn, limit)
        finally:
            conn.close()

    def _run(self, run_id: str, job_name: str, func: JobFunc) -> None:
        status = "success"
        summary: dict = {}
        error = None
        try:
            summary = func()
        except Exception as exc:  # noqa: BLE001
            status = "failed"
            error = f"{exc}\n{traceback.format_exc()}"
            summary = {"error": str(exc)}
            log.error("Job %s failed: %s", run_id, exc)
        conn = connect(project_path(load_settings()["storage"]["sqlite_path"]))
        try:
            finish_job_run(conn, run_id=run_id, status=status, summary=summary, error=error)
            conn.commit()
        finally:
            conn.close()
        with self._lock:
            self._running.pop(run_id, None)
        log.info("Job %s finished with status=%s", run_id, status)


def _notify(settings: dict | None = None) -> int:
    """Send email for any un-notified open alerts. Safe to call from any job."""
    try:
        cfg = settings or load_settings()
        conn = connect(project_path(cfg["storage"]["sqlite_path"]))
        try:
            count = send_alert_email(cfg, conn)
            conn.commit()
            return count
        finally:
            conn.close()
    except Exception as exc:  # noqa: BLE001
        log.warning("Email notification step failed: %s", exc)
        return 0


def _collect_audit() -> dict:
    """Collect audit events and notify for any new risky-event alerts."""
    result = collect_audit_events()
    settings = load_settings()
    email_notified = _notify(settings)
    return {**result, "email_notified": email_notified}


def _collect_all() -> dict:
    settings = load_settings()
    usage = collect_usage()
    inventory = collect_inventory()
    audit = collect_audit_events()
    status = calculate_status(write_alerts=True, markdown_output="docs/latest-usage-status.md")
    email_notified = _notify(settings)

    return {
        "usage": {"run_id": usage["run_id"], "records": usage["records_inserted"]},
        "inventory": {"run_id": inventory["run_id"], "apps": inventory["applications"], "apis": inventory["api_instances"]},
        "audit": {"run_id": audit["run_id"], "events": audit["events_inserted"], "risky": audit["risky_events"]},
        "status": {"run_id": status["run_id"], "alerts": status["alerts"]},
        "email_notified": email_notified,
    }


def _status_summary(payload: dict) -> dict:
    return {
        "run_id": payload["run_id"],
        "alerts": payload["alerts"],
        "critical": sum(1 for item in payload["statuses"] if item["severity"] == "critical"),
        "warning": sum(1 for item in payload["statuses"] if item["severity"] == "warning"),
    }


class SchedulerService:
    SETTINGS_KEY = "scheduler"
    _UNIT_MULTIPLIERS = {"minutes": 60, "hours": 3600, "days": 86400}

    def __init__(self, jobs: JobManager) -> None:
        self.jobs = jobs
        self._load_interval()
        self.enabled = False
        self.next_run_at: dt.datetime | None = None
        self.last_run_id: str | None = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._restore()

    def _load_interval(self) -> None:
        """Load scheduler interval from settings.json, default 12h."""
        try:
            settings = load_settings()
            cfg = settings.get("scheduler", {})
            value = max(int(cfg.get("interval_value", 12)), 1)
            unit = cfg.get("interval_unit", "hours")
            self.interval_seconds = value * self._UNIT_MULTIPLIERS.get(unit, 3600)
        except Exception:
            self.interval_seconds = 12 * 3600

    def _state_path(self) -> "Path":
        from pathlib import Path
        return project_path("data/scheduler_state.json")

    def _persist(self) -> None:
        state = {
            "enabled": self.enabled,
            "interval_seconds": self.interval_seconds,
            "next_run_at": self.next_run_at.isoformat() if self.next_run_at else None,
            "last_run_id": self.last_run_id,
        }
        path = self._state_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")

    def _restore(self) -> None:
        path = self._state_path()
        if not path.exists():
            return
        try:
            state = json.loads(path.read_text(encoding="utf-8"))
            self.interval_seconds = state.get("interval_seconds", self.interval_seconds)
            self.last_run_id = state.get("last_run_id")
            if state.get("enabled"):
                log.info("Restoring scheduler (interval=%ds)", self.interval_seconds)
                self.start()
        except Exception as exc:  # noqa: BLE001
            log.warning("Could not restore scheduler state: %s", exc)

    def start(self) -> None:
        with self._lock:
            if self.enabled:
                return
            self.enabled = True
            self._stop.clear()
            self.next_run_at = dt.datetime.now(dt.timezone.utc)
            self._thread = threading.Thread(target=self._loop, daemon=True)
            self._thread.start()
            self._persist()
        log.info("Scheduler started (interval=%ds)", self.interval_seconds)

    def stop(self) -> None:
        with self._lock:
            self.enabled = False
            self.next_run_at = None
            self._stop.set()
            self._persist()
        log.info("Scheduler stopped")

    def status(self) -> dict:
        return {
            "enabled": self.enabled,
            "interval_seconds": self.interval_seconds,
            "next_run_at": self.next_run_at.isoformat() if self.next_run_at else None,
            "last_run_id": self.last_run_id,
        }

    def _loop(self) -> None:
        while not self._stop.is_set():
            self._load_interval()
            now = dt.datetime.now(dt.timezone.utc)
            if self.next_run_at and now >= self.next_run_at:
                try:
                    self.last_run_id = self.jobs.start("collect_all", requested_by="scheduler")
                except RuntimeError as exc:
                    log.warning("Scheduler could not start job: %s", exc)
                self.next_run_at = now + dt.timedelta(seconds=self.interval_seconds)
                self._persist()
            time.sleep(2)
