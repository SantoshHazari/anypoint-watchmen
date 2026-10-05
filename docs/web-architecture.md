# Watchmen Web Architecture

## Direction

Watchmen is no longer modeled as a set of operational CLI scripts.

The intended runtime is:

```text
Flask Web App
  -> internal scheduler (persistent state)
  -> internal job runner (exclusive locking)
  -> usage/status services
  -> SQLite state
  -> admin UI
```

CLI scripts remain useful for debugging and support, but the administrator-facing surface is the web UI.

## Running

Quickstart (double-click or run from terminal):

```cmd
start.bat
```

Or manually:

```powershell
python web\app.py
```

By default the server binds to `0.0.0.0:5050` (all network interfaces). Override with environment variables:

```cmd
set WATCHMEN_HOST=127.0.0.1
set WATCHMEN_PORT=8080
python web\app.py
```

Local URL:

```text
http://localhost:5050
```

LAN access: use your machine's IP address (e.g. `http://192.168.1.x:5050`).

## Pages

- `/` — Dashboard
  - summary grid: critical, warning, running jobs, scheduler state
  - system status panel: SQLite, Config, Anypoint Token, SMTP, Last Job
  - toolbar: Run Collect+Status, Recalculate Status, Start/Stop Scheduler
  - burn perspective gauges: Contract, This Month, Today
  - timeframe detail table: per-entitlement month/day usage, budget, ratio
  - entitlement usage % chart (horizontal bar)
  - projected contract % chart (horizontal bar)
  - entitlement runway table: burn rates, acceleration, exhaustion dates
  - top consumers table
  - recent jobs table
  - meter records table
  - burn rate comparison chart (daily vs 7d vs 30d)

- `/jobs` — Job history and manual execution

- `/jobs/<run_id>` — Job detail: status, duration, summary JSON, error traceback

- `/health` — JSON health check (200/503)

- `/alerts` — Alert list with state filter tabs (All/Open/Acknowledged/Resolved/Suppressed)

- `/alerts/<id>` — Alert detail: metadata, state-sensitive actions, payload JSON

- `/settings` — Configuration UI: policy, entitlement limits, contract & alert settings, scheduler interval, audit risk rules (add/remove actions and combos)

- `/inventory` — Platform inventory: environments, applications, API instances with environment filter tabs

- `/inventory/changes` — Inventory change log: added/removed/changed entities across snapshots

- `/audit` — Audit Log events with readable timestamps, search filter, sortable columns, and tier filter tabs (All/Critical/Warning/All Risky)

- `/api/status` — JSON status for future UI/API use

## Job Model

Current jobs:

- `usage_collect`
  - calls Anypoint Usage API
  - saves raw JSON
  - persists normalized usage records
  - exclusive lock: cannot run concurrently with any exclusive collection/governance job

- `usage_status`
  - reads SQLite
  - compares usage against externalized entitlements
  - calculates KPIs/burn-rate (HIGH_WATERMARK and DRAWDOWN treated differently)
  - writes latest Markdown report
  - dispatches alerts
  - exclusive lock: cannot run concurrently with any exclusive collection/governance job

- `collect_then_status`
  - executes collection and status recalculation together
  - exclusive lock: cannot run concurrently with any exclusive collection/governance job

- `inventory_collect`
  - calls Anypoint Platform APIs (Access Management, CloudHub, Runtime Manager, API Manager)
  - discovers org via /api/me, enumerates environments, collects apps and API instances per env
  - normalizes responses and persists snapshot to SQLite
  - diffs against previous snapshot and logs changes
  - exclusive lock: cannot run concurrently with any exclusive collection/governance job

- `collect_all`
  - runs usage_collect + inventory_collect + audit_collect + usage_status in sequence
  - emails any un-notified alerts after status calculation
  - exclusive lock: cannot run concurrently with any exclusive collection/governance job

- `audit_collect`
  - calls Anypoint Audit Log Query API
  - normalizes audit events
  - persists audit events to SQLite
  - classifies risk tier using configurable rules from `config/audit_rules.json`
  - creates alert-center entries for risky events
  - exclusive lock: cannot run concurrently with any exclusive collection/governance job

Job runs are persisted in SQLite table:

```text
job_run
```

Each job has:

- `run_id`
- `job_name`
- `status`
- `requested_by`
- `started_at_utc`
- `finished_at_utc`
- `duration_seconds`
- `summary_json`
- `error`

## Scheduler Model

The scheduler is an in-process background thread with persistent state.

Default:

- disabled on startup (unless previously enabled — state restores from `data/scheduler_state.json`)
- interval: configurable from Settings UI (default 12 hours, minimum 5 minutes)
- job: `collect_all`

Configuration:

- Interval and unit (minutes/hours/days) editable from `/settings` page
- Saved to `config/settings.json` under `scheduler.interval_value` and `scheduler.interval_unit`
- Changes take effect on next scheduler cycle (no restart required)

Controls:

- Start Scheduler / Stop Scheduler from dashboard, jobs page, or settings page

## KPI Calculation Model

Two usage models with different projection logic:

- **HIGH_WATERMARK** (Mule Flows, API Manager, Governed APIs):
  Current concurrent value compared directly against the limit.
  No burn rate, no cumulative projection, no exhaustion date.
  Monthly and daily budgets equal the full limit.

- **DRAWDOWN** (Mule Messages, Data Throughput, Flex Gateway, Object Store, MQ):
  Consumed from a finite pool over the contract period.
  Daily burn rate extrapolated to project contract-end total.
  Monthly budget = limit / contract months.
  Daily budget = limit / contract days.
  Exhaustion date calculated from remaining pool / daily burn.

Unit conversion (bytes to GB for Data Throughput) is applied consistently at the threshold evaluation layer and propagated via `unit_divisor` to all windowed KPI calculations.

## Synthetic Data

For development and demo purposes:

```powershell
python scripts\seed_synthetic.py --scenario hot      # above-limit burn
python scripts\seed_synthetic.py --scenario steady   # comfortable pace
python scripts\seed_synthetic.py --clear             # remove all synthetic data
```

Synthetic data uses the same `usage_record` table as real API data (no fixture prefix filtering). Seeded records match real Anypoint Usage API payload field names.

## Data Boundary

Fixture data (`tests/fixtures/usage/`) is isolated by `fixture-` run_id prefix and excluded from normal dashboard queries.

Synthetic data (`synthetic-seed-` prefix) is included in normal queries to populate the dashboard during development.

## Alert Model

Alerts are persisted in the `alert_event` table with deduplication via unique `alert_key`.

States: open → acknowledged → resolved, open → suppressed, resolved/suppressed → open (reopen).

Five evaluation rules run on each status calculation:

- `threshold_crossed`: entitlement percent used exceeds configured threshold.
- `projected_overage`: projected contract-end usage exceeds 100%.
- `monthly_budget_exceeded`: current month usage exceeds monthly budget.
- `hwm_near_limit`: HIGH_WATERMARK meter within 80% of limit.
- `burn_acceleration`: 7-day burn rate accelerating >50% vs 30-day average.

Smart dispatch: email sent only for un-notified open alerts; marked as notified after send. JSONL file channel preserved alongside email.

Auto-lifecycle: resolved/suppressed alerts auto-reopen when condition recurs; stale alerts auto-resolve when condition clears.

Demo governance adds four rule-scoped alerts:

- `demo_expired`
- `demo_expires_soon`
- `closed_demo_resource_still_live`
- `unowned_inventory_resource`

## Anypoint Platform API Client

`scripts/anypoint_platform_api.py` — unified client for all non-usage Anypoint REST APIs.

Reuses `AnypointAuth` from `anypoint_auth.py`. Key pattern: `_request(path, method, payload, extra_headers)` with `_env_headers(org_id, env_id)` for env-scoped APIs that require `X-ANYPNT-ORG-ID` and `X-ANYPNT-ENV-ID` headers.

Methods cover: Access Management, CloudHub 1.0, Runtime Manager (hybrid), API Manager, Exchange, Anypoint MQ, and Audit Log.

Designed for extension — add new methods as new API endpoints are needed.

## Audit Log Model

SQLite tables:

- `audit_poll_run` — one row per audit collection run
- `audit_event` — normalized audit events with raw JSON preserved

Risk detection (configurable from Settings UI via `config/audit_rules.json`):

- Three tiers: critical, warning, info
- Action rules: broad patterns (e.g. "delete" is always critical)
- Combo rules: action + target pairs (e.g. "edit entitlement" is critical)
- Rules editable from `/settings` page (add/remove actions and combos per tier)
- 60-second cache; invalidated on save from UI

Risky events create alert-center records using rule:

```text
audit_risky_change
```

Live validation:

- validated against Anypoint on 2026-05-15
- 50 events collected from a 24-hour window
- 19 risky events detected and upserted into the alert center

## Inventory Model

5 SQLite tables in `scripts/inventory_store.py`:

- `inventory_snapshot` — one row per collection run
- `inventory_environment` — environments per snapshot
- `inventory_application` — unified CloudHub + hybrid apps (source column distinguishes)
- `inventory_api_instance` — API Manager instances
- `inventory_change` — diff log (added/removed/changed)

Snapshot-based: each collection creates a new snapshot, diffs against the previous one, and logs changes.

## Next Backlog

1. Historical trend charts (time-series usage visualization).
2. Export/reporting (PDF or CSV for stakeholders).
3. Environment-level usage breakdown from dimensional data.
4. API policy compliance checking.
5. Webhook/Slack notifications.
6. Docker packaging.
7. Multi-org support.
8. Data retention policy (auto-prune old data).
9. Dashboard auto-refresh.
10. Dark mode.
