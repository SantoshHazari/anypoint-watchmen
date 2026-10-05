# Watchmen Roadmap

## Purpose

Build Watchmen as a small web-based control plane for Anypoint Platform usage governance.

The UI is the primary operational surface. CLI scripts remain support/debug tools.

## Current State

Status: `Increments 1-12 complete, Phases A/B/C complete`

Implemented:

- Flask web app with structured logging, 10 pages, LAN-accessible (`start.bat`).
- Dashboard with summary grid, system status, daily consumption bar charts (Mule Messages & Data Throughput with budget lines), recent activity feed.
- Jobs page (4 jobs) with labels, descriptions, and detail view per run.
- Internal job runner with exclusive locking.
- Internal scheduler with configurable interval (5min-days) from Settings UI, persistent state.
- SQLite persistence for snapshots, usage records, entitlement status, job runs.
- Usage API collection (14 meters) with actual consumption gauges (contract/month/day).
- Entitlement status calculation with HIGH_WATERMARK / DRAWDOWN distinction.
- Burn-rate KPIs: daily, 7-day, 30-day windowed, acceleration.
- Multi-timeframe consumption gauges: contract, monthly, and daily budget.
- Top consumer analytics by meter, app, environment.
- Chart.js visualizations: percent used, projected contract, burn rate comparison.
- Email alert channel with notification wiring across all alert sources.
- Synthetic usage data seeder with multiple scenarios.
- Synthetic Usage API fixtures for unit testing.
- `/health` endpoint (JSON, 200/503).
- Always-on authentication: username/password login with token caching and auto-refresh on 401.
- Platform inventory: full BG hierarchy, CloudHub 1.0/2.0, hybrid, API Manager, snapshot diff.
- Runtime management: start/stop individual apps (CH1/CH2), stop all apps per environment, live status refresh.
- App lifecycle scheduling: env-level operating hours, per-app overrides, always-on exclusion list, one-time actions, schedule engine with priority resolution and catch-up logic, audit log.
- Audit Log ingestion across all business groups with configurable three-tier risk classification (editable from Settings UI).
- Audit table with readable timestamps, search, sortable columns, user and BG filter dropdowns.
- Inventory change attribution: deterministic "Changed By" via audit `objectId` matching.
- Settings UI: scheduler config, audit risk rules (add/remove actions and combos per tier).
- 97 unit tests.

## Increment 1: Operational Hardening

Status: `complete`

Delivered:

- `/health` endpoint returning JSON with per-check detail.
- System status panel in dashboard: SQLite, Config, Anypoint Token, SMTP, Last Job.
- Job locking: exclusive sets prevent duplicate `usage_collect` / `collect_then_status`.
- Job detail page: run ID, status, duration, summary JSON, error traceback, raw path.
- Scheduler state persisted to `data/scheduler_state.json`; auto-restores on app restart.
- Structured logging via `watchmen_log` module across all services.

## Increment 2: KPI Expansion

Status: `complete`

Delivered:

- Windowed burn rates: 7-day and 30-day.
- Burn acceleration: percentage change between 7d and 30d rates.
- Projected exhaustion date per entitlement.
- Correct HIGH_WATERMARK projection (concurrent value, not cumulative extrapolation).
- Unit conversion applied consistently (bytes to GB for data throughput).
- Top consumers ranked by value across meter, app, environment.
- Multi-timeframe burn perspective:
  - Contract gauge: worst-case projected percent at contract end.
  - Month gauge: projected current-month usage vs monthly budget.
  - Day gauge: today's actual usage vs daily budget.
- Timeframe Detail table: per-entitlement month/day usage, budget, projection, ratio.
- Chart.js panels: entitlement usage %, projected contract %, burn rate comparison.
- Synthetic data seeder (`seed_synthetic.py`) with scenario support:
  - `hot`: some entitlements burning above limits.
  - `steady`: comfortable pace, well within all budgets.

## Increment 3: Alert Center

Status: `complete`

Delivered:

- Alert event table with deduplication via `alert_key` unique index.
- Alert states: open, acknowledged, resolved, suppressed.
- Full lifecycle: open → ack → resolve → reopen, open → suppress → reopen.
- Auto-reopen: resolved/suppressed alerts reopen when condition recurs.
- Auto-resolve: stale alerts resolve when condition clears.
- Five alert rules:
  - `threshold_crossed`: entitlement percent used exceeds threshold.
  - `projected_overage`: projected contract usage exceeds 100%.
  - `monthly_budget_exceeded`: current month usage exceeds monthly budget.
  - `hwm_near_limit`: HIGH_WATERMARK meter within 80% of limit.
  - `burn_acceleration`: 7-day burn rate accelerating >50% vs 30-day.
- Alert list page with filter tabs (All/Open/Acknowledged/Resolved/Suppressed).
- Alert detail page with metadata grid, state-sensitive action buttons, payload JSON.
- Inline actions on list page: Ack, Suppress, Resolve, Reopen.
- Smart email dispatch: only un-notified alerts, marks as notified after send.
- Dashboard integration: Open Alerts count in summary grid.
- JSONL file alert channel preserved alongside email.

## Increment 4: Configuration UI

Status: `complete`

Delivered:

- Settings page with three editable sections: Policy, Entitlement Limits, Contract & Alerts.
- Inline edit for entitlement limits with float/int preservation.
- Date validation (YYYY-MM-DD) for contract dates.
- Custom CSS design system (no Bootstrap dependency).
- Toggle edit/view mode per limit row.
- Settings round-trip tested: edit → save → reload confirms persistence.

## Phase A: Inventory Collection

Status: `complete`

Delivered:

- Unified Anypoint Platform API client (`anypoint_platform_api.py`) with 19 methods:
  - Discovery: get_me, get_organization, list_environments, list_business_groups, walk_organization_tree, list_org_members.
  - CloudHub 2.0 / RTF: list_ch2_deployments, get_ch2_deployment.
  - CloudHub 1.0: list_cloudhub_apps, get_cloudhub_app.
  - Runtime Manager: list_hybrid_apps, list_servers, list_server_groups, list_clusters.
  - API Manager: list_api_instances, get_api_instance.
  - Exchange: list_exchange_assets.
  - MQ: list_mq_regions, list_mq_destinations.
  - Audit Log: query_audit_log.
- Recursive business group hierarchy traversal with `_bg_path` for display.
- Response normalizer (`inventory_normalizer.py`): unified CloudHub 1.0/2.0/hybrid app shape, API instance flattening from assets.
- SQLite inventory store (`inventory_store.py`): 6 tables (snapshot, business_group, environment, application, api_instance, change).
- All inventory tables carry `bg_id`/`bg_name` for business group context.
- Orchestration service (`inventory_services.py`): full BG tree walk, per-BG environment/app/API discovery, snapshot diff detection.
- CH2.0 detail enrichment: list endpoint is sparse, so each deployment gets a detail fetch for replicas, instanceType, vCores.
- Web UI: `/inventory` page with BG table, environment filter tabs (BG/env format), app/API tables, recent changes. `/inventory/changes` detail page.
- Dashboard: Business Groups, Inventory Apps, and API Instances counts in summary grid.
- 20 unit tests covering normalization (BG, CH1, CH2, hybrid, API), store, dedup, and diff detection.
- Validated against live Anypoint Platform (Example-Org org): 2 BGs, 4 environments, 1 CH2.0 app discovered.

## Phase B: Audit Log Integration

Status: `complete`

Objective:

Poll the Anypoint Audit Log API to detect platform changes (deployments, user changes, role modifications, API creation/deletion, policy changes).

Delivered:

- `scripts/audit_normalizer.py` — normalizes raw events, enriches object_type from `objects[]` array and subaction from payload.
- `scripts/audit_store.py` — SQLite schema with risk_tier and subaction columns.
- `scripts/audit_services.py` — orchestration with risk-tier-aware alert generation.
- `web/templates/audit.html` — tier filter tabs (All/Critical/Warning/All Risky) and color-coded badges.
- `/audit` route with tier filtering.
- `audit_collect` job (included in `collect_all`).
- Three-tier risk classification:
  - **Critical**: destructive or high-privilege changes (delete, revoke, disable, entitlement edits, org/env lifecycle).
  - **Warning**: cost-impacting or access-changing (create/deploy, permission changes, policy lifecycle).
  - **Info**: routine operations (logins, reads, views).
- Combo-based matching: `action + object_type + subaction` for precise classification.
- Alert center entries for critical/warning events with tier-appropriate severity.
- Unit tests in `tests/test_audit.py`.

Validation:

- 31 unit tests pass.
- Live Anypoint validation (Example-Org org):
  - 100+ audit events collected
  - 11 critical, 83 warning, 6 info correctly classified
  - Alert records upserted with severity matching tier

## Increment 5: Demo Ownership Registry / Phase C

Status: `removed in Increment 7`

The demo ownership feature was implemented but later removed as it did not add sufficient operational value. The demo_store.py and demo_services.py scripts remain in the codebase for reference but are not imported or used.

## Increment 6: Governance and Change Detection

Status: `complete`

Objective:

Detect platform changes that create cost or governance risk.

Delivered (across Phases A, B, C and subsequent hardening):

- Audit Log API integration: polls events, classifies risky changes with three-tier system, generates alerts.
- Inventory collection across full BG hierarchy with CloudHub 1.0, CloudHub 2.0/RTF, hybrid, and API Manager coverage.
- Inventory snapshot diff: detects added, removed, and changed applications and API instances between runs.
- Three-tier audit risk classification (critical/warning/info) with combo-based action+object+subaction matching.
- Dashboard "Recent Activity" feed combining risky audit events and inventory changes from last 24h.
- UI: `/inventory/changes` for recent platform changes, `/audit` with tier filter tabs and badges.
- Job consolidation: 4 clear jobs (Collect All, Recalculate Status, Collect Inventory, Collect Audit) with labels and descriptions.
- Always-on authentication: username/password login with token caching and automatic 401 refresh. No manual token management required.
- `.env` file support for credentials (gitignored).

## Increment 7: Scheduler Config, Notification Wiring, Audit Rules, UX

Status: `complete`

Delivered:

- **Configurable scheduler**: Interval (value + unit) editable from Settings UI. Minimum 5 minutes. Reloads each cycle without restart. `POST /settings/scheduler` route.
- **Notification wiring**: `_collect_all()` calls `send_alert_email()` after all collections, so audit, inventory, and usage alerts all trigger email notifications.
- **Configurable audit risk rules**: Rules stored in `config/audit_rules.json`, editable from Settings UI. Add/remove action rules and combo rules per tier (critical/warning/info). 60s cache with invalidation on save. Empty tiers allowed.
- **Audit table UX**: Readable timestamps (server-side Jinja filters for epoch-millis and ISO), relative time ("2h ago"), client-side search filter with match count, sortable columns with direction indicators.
- **Demo feature removed**: Demos page, routes, and test removed (not adding value).
- **Launch script**: `start.bat` for easy startup, binds to `0.0.0.0:5050` for LAN access.
- 37 unit tests (7 new settings tests, 3 new audit tests).

## Increment 8: Multi-BG Audit, Filters, Change Attribution

Status: `complete`

Delivered:

- **Multi-BG audit collection**: Audit log ingestion now walks the full business group hierarchy via `walk_organization_tree()`, collecting events from every BG — not just the root org. Each event tagged with `bg_id`/`bg_name`.
- **Audit normalizer: objects[] extraction**: The Anypoint Audit API stores key identifiers (`objectId`, `objectName`, `environmentId`, `environmentName`) inside the `objects[]` array, not at top level. The normalizer now extracts these, fixing empty Name/Environment columns in the audit table.
- **Backfill migration**: `backfill_objects_fields()` one-time migration repopulates `object_id`, `object_name`, `env_id`, `env_name` on existing audit rows from stored `raw_json`.
- **Audit filters**: User dropdown and BG dropdown on the audit page. Both preserve each other and the tier filter across navigation. Generic `applyFilter(param, value)` JS function.
- **Inventory change attribution**: "Changed By" column on `/inventory/changes` using deterministic `objectId` matching — correlates audit `object_id` with inventory `api_id` scoped to the same `env_id`. Shows "—" when no definitive match exists (no guessing).
- **Dashboard activity feed**: Capped at 8 items with `max-height:220px` scrollable container. Gauges and charts no longer pushed off-screen.
- **Readable timestamps**: Jobs page "Started" column and dashboard activity feed now use `audit_time`/`audit_relative` Jinja filters (e.g. "May 19, 14:32 (3h ago)").
- **Audit event limit**: Raised from 100 to 200 on the audit page.

## Increment 9: Usage Deduplication Fix

Status: `complete`

Delivered:

- **Root cause**: Usage aggregation queries (SUM/MAX) operated across all `usage_record` rows without deduplication. The `usage_record` UNIQUE constraint includes `snapshot_id`, so each scheduler-triggered collection run inserted a duplicate copy of every data point. With 159 snapshots, values were inflated up to 160x (e.g., Mule Messages showed 99,148 instead of actual 3,541).
- **Fix**: All five aggregation functions in `usage_store.py` now use a `WITH deduped AS (...)` CTE with `ROW_NUMBER() OVER (PARTITION BY meter_key, period_start_utc, app_name, env_id, raw_dimensions_json ORDER BY snapshot_id DESC)` to keep only the most recent snapshot's value per unique data point before aggregating.
- **Functions fixed**: `latest_usage_by_meter()`, `usage_by_meter_window()`, `latest_day_usage()`, `top_consumers()`, `usage_totals_by_meter()` — all refactored to use shared `_dedup_usage_query()` helper where applicable.
- **Validated**: Dashboard values now match Anypoint Platform exactly (Mule Messages: 4,680, Mule Flows: 3, Data Throughput: 0.06 GB for May 19).
- **7 unit tests** in `test_usage_dedup.py` covering SUM dedup, MAX_CONCURRENT dedup, multi-day dedup, multi-app correctness, latest_day_usage, top_consumers, and usage_totals.
- **44 total tests** (37 existing + 7 new), all passing.

## Increment 10: Dashboard Daily Consumption Charts

Status: `complete`

Delivered:

- **Gauges removed**: The three consumption doughnut gauges (contract/month/day) were misleading — they showed worst-case % without identifying which entitlement was driving it. Removed entirely.
- **Daily bar charts**: Two side-by-side Chart.js bar charts for Mule Messages and Data Throughput (the two key DRAWDOWN meters). Each shows daily bars for the last 30 days with color-coding (green/yellow/orange/red) based on proximity to the daily budget line.
- **Budget line overlay**: Red dashed horizontal line at `contract_limit / contract_days` per meter (Mule Messages: ~54,795/day, Data Throughput: ~109.6 GB/day). Gives immediate visual reference for safe daily burn rate.
- **`daily_usage_timeseries()`**: New deduplicated query function in `usage_store.py`. Uses the same `ROW_NUMBER()` CTE dedup pattern as other queries. Supports both DRAWDOWN (SUM) and MAX_CONCURRENT (MAX) meters.
- **Data throughput bytes→GB conversion**: Raw API values in bytes converted to GB in the dashboard route before passing to the template.
- **Chart.js height fix**: Canvas wrapped in `position:relative; height:260px` container to prevent infinite growth in grid layout (responsive Chart.js bug).
- **Dashboard cleanup**: Removed unused `totals` and `consumers` template variables.

## Increment 11: Runtime Management (Start/Stop Apps)

Status: `complete`

Delivered:

- **Service layer** (`scripts/runtime_services.py`): Isolated functions for `start_app()`, `stop_app()`, `stop_all_apps_in_env()`, and `refresh_app_statuses()`. Designed for reuse by web routes, future automated idle-detection, CLI, or scheduled jobs.
- **API client methods**: 4 new methods on `AnypointPlatformClient` — `start_ch2_deployment`, `stop_ch2_deployment` (PATCH with desiredState), `start_cloudhub_app`, `stop_cloudhub_app` (POST to /status).
- **CH2 deployment_id capture**: Normalizer now includes `deployment_id` in app records (stored in `extra_json`), required for CH2 start/stop API calls.
- **Web routes**: `POST /runtime/app/start`, `POST /runtime/app/stop`, `POST /runtime/env/stop-all`, `POST /runtime/refresh-statuses`. All support both form POST (redirect with flash) and JSON API (`Accept: application/json`).
- **Flash message system**: Base template renders dismissible success/error/warning banners via Flask flash. All runtime actions provide immediate feedback with live status check after the API call.
- **Inventory UI actions**: Start/Stop buttons per app (color-coded by state), Stop All button per environment (with confirmation dialog), Refresh Statuses button next to Applications heading.
- **Live status refresh**: `refresh_app_statuses()` fetches current status from Anypoint API for each app and updates the DB row in-place — no new snapshot needed, completes in 2-3 seconds.
- **12 unit tests** covering start/stop dispatch, error handling, stop-all with skip/fail counting, state detection.
- **56 total tests**, all passing.

## Increment 12: App Lifecycle Scheduling

Status: `complete`

Delivered:

- **Schedule data layer** (`scripts/schedule_store.py`): SQLite schema for 4 tables (`schedule_environment`, `schedule_app_override`, `schedule_one_time`, `schedule_log`) with full CRUD operations. Upserts via `ON CONFLICT ... DO UPDATE`. Days of week as CSV integers (0=Mon..6=Sun). Unified `override_type` field ('custom'/'always_on') for per-app overrides and exclusion list.
- **Schedule engine** (`scripts/schedule_engine.py`): Daemon thread (`ScheduleEngine`) polling every 30s (configurable). Priority resolution: always_on > one-time > custom app schedule > env schedule > no schedule. Catch-up logic handles server restarts (past stop_time → stop, during window + stopped → start). Deduplication via `schedule_log` prevents repeated actions. One-time actions have 2-hour grace period; expired actions auto-marked.
- **Web routes**: 10 new endpoints — schedule dashboard (`GET /schedules`), env schedule CRUD, app override CRUD, one-time action create/cancel, schedule log view, engine start/stop controls.
- **Schedule dashboard** (`web/templates/schedules.html`): Engine status bar (running/stopped badge, poll interval, timezone), summary metrics (env schedules, custom overrides, always-on, pending one-time, actions today), environment schedule table with add form (env dropdown, time inputs, day-of-week checkboxes), custom app schedule table, always-on exclusion list, one-time actions (pending + recent), estimated savings calculator (off-hours × apps × vCore-hrs), recent log preview with "View All" link.
- **Schedule log page** (`web/templates/schedule_log.html`): Full audit log table with timestamp, action, trigger type, target, status, detail.
- **Navigation**: "Schedules" link added between Inventory and Audit in base template.
- **Settings integration**: `schedule` section in `settings.json` with `poll_interval` and `auto_start` options.
- **41 new tests** (22 store + 19 engine) covering CRUD operations, priority resolution, weekend shutdown, deduplication, one-time execution/expiry, custom override precedence, engine start/stop lifecycle.
- **97 total tests**, all passing.

## Backlog

Items for future work, not yet planned or prioritized:

- **Idle app detection**: Detect apps idle for X hours (no messages/throughput) and flag for review. Complements the schedule engine's time-based stop.
- **Export / reporting**: PDF or CSV export of inventory, audit trail, and usage status for stakeholder presentations.
- **Environment-level usage breakdown**: Drill-down per environment from dimensional usage data.
- **API policy compliance**: Check which APIs have/don't have required policies applied.
- **Connected App auth**: Replace username/password with OAuth client_credentials for production use.
- **Webhook/Slack notifications**: Push alerts to Slack or Teams in addition to email.
- **Docker packaging**: Dockerfile for easy deployment.
- **Multi-org support**: Support multiple Anypoint orgs from one Watchmen instance.
- **Data retention policy**: Auto-prune old raw JSON and SQLite rows.
- **Dashboard auto-refresh**: Periodic AJAX poll for live updates.
- **Dark mode**: Dark theme option for the UI.
- **Alert suppression rules**: Auto-suppress known noise patterns.
- **Cost projections**: Overage cost estimates based on burn rate and contract pricing.
- **RBAC / team audit**: Who has what roles, stale permissions, unused connected apps.
- **Backfill tool**: Collect historical usage beyond the 30-day API window.

## Technical Principles

- UI-first operations.
- Usage API is source of consumption facts.
- SQLite is source of history and state.
- JSON config is source of policy until config UI matures.
- HIGH_WATERMARK meters: concurrent peak, projected as-is (no cumulative extrapolation).
- DRAWDOWN meters: consumed from pool, projected via daily burn rate.
- Unit conversion (e.g. bytes to GB) applied at threshold evaluation and all KPI layers.
- Synthetic data seeder for development; fixtures for unit testing.
- CLI remains available but is not the primary user interface.
- Keep modules isolated:
  - collection
  - storage
  - KPI calculation
  - thresholds
  - alert dispatch
  - job orchestration
  - web presentation
