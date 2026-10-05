---
name: Project state and next steps
description: Current implementation status and what comes next
type: project
---
As of 2026-06-03, Increments 1-6, Phases A/B/C, Increment 12, zero-usage handling, OAuth 2.0 auth migration, and audit/alert pipeline overhaul are complete. 117 tests pass.

## Recent Updates (2026-06-03)

**Audit & Alert Pipeline Overhaul**

*Noise reduction*
- Incremental polling: each audit run now starts from the `window_end_utc` of the last successful run instead of always going back 7 days. In steady state (hourly scheduler) the window is ~1h instead of 168h — ~99% volume reduction.
- Safety cap: `audit.max_lookback_hours` (default 24) prevents unbounded catch-up if the service is restarted after a long outage.
- API-level action filter: `audit.exclude_actions` drives a whitelist passed directly to `POST /audit/v2/.../query`. Info-tier actions (login, logout, read, view, list, search, get) are excluded at the source and never stored. In this org 77% of events were login — these no longer arrive.

*Notification pipeline fixes*
- `collect_audit` job now calls `send_alert_email()` after each run — standalone audit runs notify immediately instead of waiting for the next `collect_all`.
- Extracted `_notify()` helper in `watchmen_jobs.py`; `_collect_all()` simplified to use it, eliminating the previous double-send risk.
- `write_file_alerts()` now calls `mark_alerts_notified()` after writing — fixes bug where file-channel alerts were re-sent by email on the next cycle.
- Hard-coded `http://127.0.0.1:5050/alerts` URL in emails replaced with configurable `app_base_url` from `settings.json`.
- Email recipients now resolved from `SMTP_RECEIVER_EMAIL` env var (`to_env` in settings) with fallback to `settings.alerts.email.to` list.
- SMTP configured and verified: `sender@example.com` → `alerts@example.com`.

*Alert quality*
- Audit alerts (rule=`audit_risky_change`) now auto-resolve after `audit.auto_resolve_days` days (default 7). Previously they accumulated indefinitely since they were outside the `resolve_stale_alerts()` active-key mechanism.
- New `resolve_old_audit_alerts(conn, max_age_days)` function added to `usage_store.py`, called at the end of every audit collection run.

*Settings added to `settings.json`*
- `app_base_url` — base URL used in alert emails
- `audit.auto_resolve_days` — days before open audit alerts auto-resolve (default 7)
- `audit.max_lookback_hours` — max catch-up window on restart (default 24)
- `audit.exclude_actions` — info-tier action names excluded from API queries

## Recent Updates (2026-05-30)

**OAuth 2.0 Authentication Migration**
- Replaced `ANYPOINT_USERNAME` / `ANYPOINT_PASSWORD` with Connected App OAuth 2.0 Client Credentials
- Connected App "Watchmen Dashboard" created in Anypoint Access Management with required scopes
- Token endpoint: `POST /accounts/api/v2/oauth2/token` using `client_secret_post` method
- Token TTL: ~54 minutes (`expires_in` from API). Auto-refreshed at 90% of lifetime — fully automatic, zero manual intervention
- Auth priority order: static `ANYPOINT_TOKEN` → OAuth client credentials → legacy username/password (deprecated)
- `tests/test_anypoint_auth.py` added: 15 unit + integration tests covering OAuth flow, priority enforcement, auth_status(), token request, and backward compatibility
- OAuth token confirmed working end-to-end; every Collect All job run passes using OAuth

## Recent Updates (2026-05-28)

**Zero-Usage Handling & Data Completeness**
- Implemented automatic detection and filling of missing usage data dates with zero-value records
- Added `fill_missing_dates_with_zeros()` function to auto-fill gaps during collection
- Created `backfill_zero_usage.py` script for retroactive zero-filling
- Days with zero activity (e.g., when apps are stopped) now show as explicit zeros instead of blank spaces
- Complete documentation in `docs/zero-usage-handling.md`

**Dashboard Enhancements**
- Implemented 15-day rolling window for graph display (configurable via settings)
- Historical data preserved in database; only display window limited
- Added daily consumption 60% threshold alert rule (`_rule_daily_consumption_60pct()`)
  - Severity escalation: warning at 60-90%, critical at 90%+
  - Only applies to DRAWDOWN metrics, not HIGH_WATERMARK
- Email notifications configured and tested for 60% alert and other threshold breaches

## Capabilities

**Dashboard & Monitoring**
- Real-time KPI dashboard with critical/warning severity indicators
- Daily consumption bar charts with configurable rolling window (default 15 days, all historical data preserved)
- Zero-value records for days with no activity (explicitly stored, not omitted)
- Daily budget lines derived from contract entitlements
- Activity feed combining audit events + inventory changes (last 24h)
- Health check endpoint (`/health`) with system status

**Usage & Entitlement Tracking**
- Automated collection of Anypoint Platform usage meters (Mule Messages, Data Throughput, etc.)
- Entitlement limit configuration with threshold-based alerting
- Usage deduplication and normalization across collection runs
- Contract period tracking (start/end dates, daily budget calculation)
- Usage status recalculation with severity classification (ok/warning/critical)

**Inventory Discovery**
- Full Business Group hierarchy traversal (recursive sub-org discovery)
- CloudHub 1.0 and CloudHub 2.0/RTF application discovery
- API Manager instance discovery (endpoints, versions, technology)
- Environment enumeration across all business groups
- Snapshot-based inventory with point-in-time comparisons
- Change detection: added/removed/changed apps, APIs, and environments
- Change attribution: links inventory changes to audit trail users
- Environment filtering on inventory page

**Runtime Management**
- Start individual app (CloudHub 1.0 and CloudHub 2.0)
- Stop individual app (CloudHub 1.0 and CloudHub 2.0)
- Stop All apps in an environment (batch operation)
- Live status refresh from Anypoint API with loading spinner
- Post-action status verification

**App Lifecycle Scheduling**
- Environment-level operating hours (start/stop windows with day-of-week control)
- Three schedule modes: stop-only, start-only, operating window (both)
- Multiple schedule entries per environment
- Per-app custom schedule overrides (replace env schedule for specific apps)
- Always-on exclusion list (apps never touched by any schedule)
- One-time scheduled actions (stop/start app or environment at a specific time)
- Schedule engine: daemon thread polling every 30s with three-gate decision model
- Execution tracking table prevents duplicate actions without blocking legitimate re-fires
- Within-tick status overrides (subsequent schedule entries see correct state)
- Catch-up logic on server restart (missed actions within 2h grace period)
- Live countdown timers ("Next In" column) on schedule dashboard
- Relative timestamps ("X ago") on schedule logs
- Full schedule audit log with BG/environment resolution

**Audit Log & Governance**
- Anypoint Platform audit event polling and storage
- Risk classification: critical/high/medium/info tiers
- Configurable risky event rules (action-based and action+target combos)
- Filterable audit view by risk tier, user, and business group
- User and BG aggregation for audit analysis

**Alert Center**
- Threshold-based alerts from usage and inventory changes
- Alert rules:
  - Threshold crossed (warning/critical based on entitlement percentage)
  - Projected contract overage (high watermark metrics)
  - Monthly budget exceeded (for drawdown metrics)
  - HIGH_WATERMARK near limit (≥80% triggers warning, ≥95% critical)
  - Burn rate acceleration (7d burn >50% higher than 30d burn)
  - Daily consumption 60% threshold (warning 60-90%, critical ≥90%)
- Alert lifecycle: open, acknowledged, resolved, suppressed
- Alert detail view with context
- Alert counts on dashboard
- Email notifications for new/severity-changed alerts

**Job Management**
- 4 automated jobs: collect_all, recalculate_status, collect_inventory, collect_audit
- Configurable scheduler with interval control (minutes/hours)
- Manual job trigger from UI
- Job run history with detail view (run_id, duration, status)
- Start/stop scheduler from UI

**Settings & Configuration**
- Governance policy configuration (operating mode, threshold percentages)
- Entitlement limit editing per meter
- Contract period (start/end date) management
- Alert email recipient configuration
- Scheduler interval configuration (min 5 minutes)
- Audit risk rule management (add/remove actions and combos per tier)

## Implementation Summary

**Completed:**
- Increments 1-4: Operational hardening, KPI expansion, alert center, config UI
- Phase A (Inventory): Full BG hierarchy, CH1/CH2/hybrid/API discovery, snapshot diffing
- Phase B (Audit Log): Event polling, risky change classification, alerts
- Phase C (Demo Registry): Ownership tracking, expiry governance, unowned resource alerts
- Increment 6 (Governance): Delivered via Phases A+B+C — change detection, risky events, inventory diffs
- Job consolidation: 4 jobs with labels/descriptions
- Increment 12 (App Lifecycle Scheduling): Full scheduling system with execution tracking

**Jobs:** collect_all (full pipeline), recalculate_status, collect_inventory, collect_audit

**Pages:** / (dashboard), /inventory, /inventory/changes, /audit, /alerts, /jobs, /settings, /health, /schedules, /schedules/log

**Anypoint org:** Example-Org (`<org-id>`), sub-orgs poc-sandbox, training, GTM-demos. 4 BGs, 4 envs, 5 CH2.0 apps.

**Backlog (user-prioritized):**
- Configurable governance rules (risky event classification from Settings UI)
- Export/reporting (PDF/CSV for stakeholders)
- Multi-org support
- Notification wiring for audit/inventory changes
- Usage-to-demo attribution
