"""Schedule engine — daemon thread that enforces app lifecycle schedules.

Polls every N seconds (default 30), resolves desired state for each managed app
using priority resolution, and executes start/stop via runtime_services.

Priority (highest wins):
  1. always_on override   → skip entirely (never touch)
  2. One-time action      → override everything else
  3. Per-app custom sched → replaces env schedule for that app
  4. Environment schedule → default for all apps in env
  5. No schedule          → do nothing
"""

from __future__ import annotations

import datetime as dt
import json
import sqlite3
import threading
import time
from typing import Any, Callable

from watchmen_log import get_logger

log = get_logger("schedule_engine")

# Grace period for one-time actions that were missed (e.g. server was down)
ONE_TIME_GRACE_SECONDS = 2 * 3600  # 2 hours


class ScheduleEngine:
    """Daemon thread that enforces start/stop schedules."""

    def __init__(
        self,
        *,
        db_opener: Callable[[], sqlite3.Connection],
        client_factory: Callable[[], Any],
        poll_interval: int = 30,
    ):
        """
        Args:
            db_opener: Callable returning a new sqlite3.Connection (with row_factory).
            client_factory: Callable returning an AnypointPlatformClient.
            poll_interval: Seconds between ticks.
        """
        self._db_opener = db_opener
        self._client_factory = client_factory
        self.poll_interval = poll_interval
        self._close_connections = True
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._running = False

    @property
    def running(self) -> bool:
        return self._running

    def start(self) -> None:
        if self._running:
            log.warning("Engine already running")
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run_loop, daemon=True, name="schedule-engine")
        self._thread.start()
        self._running = True
        log.info("Schedule engine started (poll every %ds)", self.poll_interval)

    def stop(self) -> None:
        if not self._running:
            return
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=10)
        self._running = False
        log.info("Schedule engine stopped")

    def _run_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                self.tick()
            except Exception:
                log.exception("Schedule engine tick failed")
            self._stop_event.wait(self.poll_interval)

    # ------------------------------------------------------------------
    # Core tick
    # ------------------------------------------------------------------

    def tick(self, *, _now: dt.datetime | None = None) -> dict:
        """Run one scheduling cycle. Returns summary of actions taken.

        Decision model (per schedule entry per app):
          1. Was this schedule+app+action already executed today? → skip
          2. Does current time match the schedule? → determine desired action
          3. Is the app already in the desired state? → skip (log once)
          4. Execute start/stop → log result, mark executed

        Args:
            _now: Override current time for testing (naive local datetime).
        """
        from inventory_store import latest_inventory
        from schedule_store import (
            ensure_schedule_schema,
            get_pending_one_time_actions,
            insert_schedule_log,
            list_app_overrides,
            list_env_schedules,
            mark_one_time_executed,
            mark_schedule_executed,
            was_schedule_executed_today,
        )
        from runtime_services import start_app, stop_app

        now = _now or dt.datetime.now()
        today_date = now.strftime("%Y-%m-%d")
        weekday = now.weekday()  # 0=Mon
        current_time = now.strftime("%H:%M")

        conn = self._db_opener()
        ensure_schedule_schema(conn)

        try:
            summary = {"started": 0, "stopped": 0, "skipped": 0, "errors": 0, "one_time": 0}

            # Load data — group env schedules by env_id (multiple per env allowed)
            env_schedules: dict[str, list[dict]] = {}
            for s in list_env_schedules(conn, enabled_only=True):
                env_schedules.setdefault(s["env_id"], []).append(s)
            app_overrides = list_app_overrides(conn, enabled_only=True)
            always_on_set = set()
            custom_overrides: dict[tuple[str, str, str], dict] = {}

            for ov in app_overrides:
                key = (ov["source"], ov["env_id"], ov["domain"])
                if ov["override_type"] == "always_on":
                    always_on_set.add(key)
                elif ov["override_type"] == "custom":
                    custom_overrides[key] = ov

            # -- Process one-time actions first --
            one_time_done = set()  # (env_id, domain, action) already handled
            pending = get_pending_one_time_actions(conn, now.isoformat())

            client = None  # lazy init

            for ot in pending:
                # Check grace period — compare naive local times
                sched_str = ot["scheduled_at"].replace("Z", "").split("+")[0]
                sched_naive = dt.datetime.fromisoformat(sched_str)
                age_seconds = (now - sched_naive).total_seconds()
                if age_seconds > ONE_TIME_GRACE_SECONDS:
                    mark_one_time_executed(conn, ot["id"], status="expired",
                                          detail=f"Missed by {age_seconds/3600:.1f}h")
                    conn.commit()
                    continue

                if client is None:
                    client = self._client_factory()

                ok, detail = self._execute_one_time(client, ot, conn, insert_schedule_log)
                status = "executed" if ok else "failed"
                mark_one_time_executed(conn, ot["id"], status=status, detail=detail)
                conn.commit()
                summary["one_time"] += 1
                one_time_done.add((ot["env_id"], ot["domain"], ot["action"]))

            # -- Process recurring schedules --
            inventory = latest_inventory(conn)
            apps = inventory.get("applications", [])

            # Track status overrides within this tick (after we execute an
            # action, update the effective status so later schedule entries
            # for the same app see the correct state).
            status_overrides: dict[str, str] = {}  # domain → effective status

            for app in apps:
                source = app.get("source", "")
                if source not in ("cloudhub", "ch2"):
                    continue

                domain = app.get("domain", "")
                env_id = app.get("env_id", "")
                org_id = app.get("bg_id", "")
                inv_status = (app.get("status", "") or "").upper()
                # Use overridden status if we already acted on this app in this tick
                status = status_overrides.get(domain, inv_status)

                # Parse deployment_id from extra_json
                extra = app.get("extra_json", "{}")
                if isinstance(extra, str):
                    try:
                        extra = json.loads(extra)
                    except (json.JSONDecodeError, TypeError):
                        extra = {}
                deployment_id = extra.get("deployment_id", "")

                app_key = (source, env_id, domain)

                # Priority 1: always_on → skip
                if app_key in always_on_set:
                    summary["skipped"] += 1
                    continue

                # Priority 2: one-time already handled this app
                if (env_id, domain, "start") in one_time_done or \
                   (env_id, domain, "stop") in one_time_done:
                    continue

                # Priority 3: custom app schedule (single)
                if app_key in custom_overrides:
                    schedules_for_app = [custom_overrides[app_key]]
                    trigger_type = "app_override"
                else:
                    # Priority 4: environment schedules (multiple per env)
                    schedules_for_app = env_schedules.get(env_id, [])
                    trigger_type = "env_schedule"

                if not schedules_for_app:
                    continue  # Priority 5: no schedule

                # Evaluate each schedule entry independently
                for sched in schedules_for_app:
                    trigger_id = sched.get("id")

                    # Check day of week
                    active_days = _parse_days(sched.get("days_of_week", ""))
                    if weekday not in active_days:
                        if _is_running(status) and sched.get("stop_time"):
                            desired = "stop"
                        else:
                            continue
                    else:
                        desired = _desired_action(
                            current_time,
                            sched.get("start_time", ""),
                            sched.get("stop_time", ""),
                        )

                    if desired is None:
                        continue

                    # ── Gate 1: Already executed today for this schedule+app? ──
                    if was_schedule_executed_today(
                        conn, schedule_id=trigger_id, action=desired,
                        domain=domain, today_date=today_date,
                    ):
                        continue  # silently skip — already handled

                    # ── Gate 2: App already in desired state? ──
                    if desired == "stop" and not _is_running(status):
                        # Record execution so we don't re-check every tick
                        mark_schedule_executed(
                            conn, schedule_id=trigger_id, schedule_type=trigger_type,
                            action=desired, env_id=env_id, domain=domain,
                            today_date=today_date,
                        )
                        insert_schedule_log(
                            conn, action=desired, trigger_type=trigger_type,
                            trigger_id=trigger_id, target_type="app",
                            source=source, env_id=env_id, domain=domain,
                            org_id=org_id, ok=True,
                            detail=f"already {status} — no action needed",
                        )
                        conn.commit()
                        summary["skipped"] += 1
                        continue

                    if desired == "start" and _is_running(status):
                        mark_schedule_executed(
                            conn, schedule_id=trigger_id, schedule_type=trigger_type,
                            action=desired, env_id=env_id, domain=domain,
                            today_date=today_date,
                        )
                        insert_schedule_log(
                            conn, action=desired, trigger_type=trigger_type,
                            trigger_id=trigger_id, target_type="app",
                            source=source, env_id=env_id, domain=domain,
                            org_id=org_id, ok=True,
                            detail=f"already {status} — no action needed",
                        )
                        conn.commit()
                        summary["skipped"] += 1
                        continue

                    # ── Gate 3: Execute the action ──
                    if client is None:
                        client = self._client_factory()

                    try:
                        if desired == "start":
                            result = start_app(client, source=source, org_id=org_id,
                                               env_id=env_id, domain=domain,
                                               deployment_id=deployment_id)
                        else:
                            result = stop_app(client, source=source, org_id=org_id,
                                              env_id=env_id, domain=domain,
                                              deployment_id=deployment_id)

                        insert_schedule_log(
                            conn, action=desired, trigger_type=trigger_type,
                            trigger_id=trigger_id, target_type="app",
                            source=source, env_id=env_id, domain=domain,
                            org_id=org_id, ok=result["ok"],
                            detail=result.get("detail", ""),
                            response=result.get("response"),
                        )

                        if result["ok"]:
                            # Mark executed so it won't re-fire this tick or later ticks today
                            mark_schedule_executed(
                                conn, schedule_id=trigger_id, schedule_type=trigger_type,
                                action=desired, env_id=env_id, domain=domain,
                                today_date=today_date,
                            )
                            # Update effective status for subsequent schedule entries
                            status_overrides[domain] = "STOPPED" if desired == "stop" else "STARTED"
                            status = status_overrides[domain]
                            summary["started" if desired == "start" else "stopped"] += 1
                        else:
                            summary["errors"] += 1

                        conn.commit()

                    except Exception as exc:
                        log.error("Schedule action %s on %s failed: %s", desired, domain, exc)
                        insert_schedule_log(
                            conn, action=desired, trigger_type=trigger_type,
                            trigger_id=trigger_id, target_type="app",
                            source=source, env_id=env_id, domain=domain,
                            org_id=org_id, ok=False, detail=str(exc),
                        )
                        conn.commit()
                        summary["errors"] += 1

            log.info("Tick: started=%d stopped=%d skipped=%d errors=%d one_time=%d",
                     summary["started"], summary["stopped"], summary["skipped"],
                     summary["errors"], summary["one_time"])
            return summary

        finally:
            if self._close_connections:
                conn.close()

    # ------------------------------------------------------------------
    # One-time action execution
    # ------------------------------------------------------------------

    def _execute_one_time(self, client, ot: dict, conn, insert_log_fn) -> tuple[bool, str]:
        """Execute a one-time scheduled action. Returns (ok, detail)."""
        from runtime_services import start_app, stop_app, stop_all_apps_in_env

        action = ot["action"]
        target_type = ot["target_type"]
        env_id = ot["env_id"]
        org_id = ot["org_id"]
        source = ot["source"]
        domain = ot["domain"]
        deployment_id = ot["deployment_id"]

        try:
            # Environment-level actions: stop/stop_all on env → stop_all_apps_in_env
            if target_type == "env" and action in ("stop", "stop_all"):
                result = stop_all_apps_in_env(client, org_id=org_id, env_id=env_id)
                detail = f"stopped={result['stopped']} skipped={result['skipped']} failed={result['failed']}"
                ok = result["ok"]
            elif target_type == "env" and action == "start":
                # Start all stopped apps in env — iterate like stop_all but start
                result = self._start_all_apps_in_env(client, org_id=org_id, env_id=env_id)
                detail = f"started={result['started']} skipped={result['skipped']} failed={result['failed']}"
                ok = result["ok"]
            # App-level actions
            elif action == "start":
                result = start_app(client, source=source, org_id=org_id,
                                   env_id=env_id, domain=domain,
                                   deployment_id=deployment_id)
                ok = result["ok"]
                detail = result.get("detail", "")
            elif action in ("stop", "stop_all"):
                result = stop_app(client, source=source, org_id=org_id,
                                  env_id=env_id, domain=domain,
                                  deployment_id=deployment_id)
                ok = result["ok"]
                detail = result.get("detail", "")
            else:
                return False, f"Unknown action: {action}"

            insert_log_fn(
                conn, action=action, trigger_type="one_time",
                trigger_id=ot["id"], target_type=ot["target_type"],
                source=source, env_id=env_id, domain=domain,
                org_id=org_id, ok=ok, detail=detail,
                response=result.get("response"),
            )
            return ok, detail

        except Exception as exc:
            insert_log_fn(
                conn, action=action, trigger_type="one_time",
                trigger_id=ot["id"], target_type=ot["target_type"],
                source=source, env_id=env_id, domain=domain,
                org_id=org_id, ok=False, detail=str(exc),
            )
            return False, str(exc)

    @staticmethod
    def _start_all_apps_in_env(client, *, org_id: str, env_id: str) -> dict:
        """Start all stopped apps (CH1 + CH2) in the given environment."""
        from runtime_services import start_app, _is_stopped

        results: list[dict] = []
        started = skipped = failed = 0

        # CH2
        try:
            ch2_list = client.list_ch2_deployments(org_id, env_id)
        except RuntimeError:
            ch2_list = []

        for dep in ch2_list:
            dep_id = dep.get("id", "")
            name = dep.get("name", dep_id)
            app_status = dep.get("application", {}).get("status", dep.get("status", ""))
            desired = dep.get("application", {}).get("desiredState", "")

            if not _is_stopped(app_status, desired):
                skipped += 1
                continue

            r = start_app(client, source="ch2", org_id=org_id,
                          env_id=env_id, domain=name, deployment_id=dep_id)
            results.append(r)
            if r["ok"]:
                started += 1
            else:
                failed += 1

        # CH1
        try:
            ch1_list = client.list_cloudhub_apps(org_id, env_id)
        except RuntimeError:
            ch1_list = []

        for app in ch1_list:
            name = app.get("domain", "")
            app_status = app.get("status", "")

            if not _is_stopped(app_status):
                skipped += 1
                continue

            r = start_app(client, source="cloudhub", org_id=org_id,
                          env_id=env_id, domain=name, deployment_id="")
            results.append(r)
            if r["ok"]:
                started += 1
            else:
                failed += 1

        return {
            "ok": failed == 0,
            "action": "start_all",
            "env_id": env_id,
            "org_id": org_id,
            "total": started + skipped + failed,
            "started": started,
            "skipped": skipped,
            "failed": failed,
            "results": results,
        }


# ------------------------------------------------------------------
# Pure helpers (easily testable)
# ------------------------------------------------------------------

def _skip_already_logged(conn, trigger_id, domain: str, action: str, today_str: str) -> bool:
    """Check if a 'no action needed' skip was already logged for this
    trigger+domain+action today. Prevents log spam (one entry per combo per day)."""
    row = conn.execute(
        """SELECT COUNT(*) AS c FROM schedule_log
        WHERE trigger_id = ? AND domain = ? AND action = ?
        AND timestamp_utc >= ? AND detail LIKE '%no action needed%'""",
        (trigger_id, domain, action, today_str),
    ).fetchone()
    return row["c"] > 0


_RUNNING_STATUSES = {"STARTED", "RUNNING", "DEPLOYING", "UPDATING"}


def _is_running(status: str) -> bool:
    return (status or "").upper() in _RUNNING_STATUSES


def _parse_days(days_str: str) -> set[int]:
    """Parse '0,1,2,3,4' into {0,1,2,3,4}."""
    if not days_str:
        return set()
    try:
        return {int(d.strip()) for d in days_str.split(",")}
    except ValueError:
        return set()


def _desired_action(current_time: str, start_time: str, stop_time: str) -> str | None:
    """Determine what action to take based on current time and the schedule window.

    Returns 'start', 'stop', or None (do nothing).

    Modes:
      - Both set (operating window):
          before start → stop, between start/stop → start, after stop → stop
      - Stop only (start_time empty):
          at/after stop_time → stop, before → None
      - Start only (stop_time empty):
          at/after start_time → start, before → None
      - Neither set → None
    """
    if not start_time and not stop_time:
        return None

    # Stop only — just stop at the given time, no auto-start
    if not start_time and stop_time:
        return "stop" if current_time >= stop_time else None

    # Start only — just start at the given time, no auto-stop
    if start_time and not stop_time:
        return "start" if current_time >= start_time else None

    # Both — full operating window
    if current_time < start_time:
        return "stop"
    elif current_time >= start_time and current_time < stop_time:
        return "start"
    else:
        return "stop"
