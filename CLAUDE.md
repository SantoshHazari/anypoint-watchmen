# Anypoint Platform Watchmen — Project Memory

## What this project is

A self-hosted dashboard for monitoring MuleSoft Anypoint Platform usage, entitlements, inventory, audit log, and alerts. Runs as a Flask web app on `http://localhost:5050`. It monitors a single Anypoint org (org ID configured locally, not stored in the repo).

## Running the app

```
cd C:\backupD\new-anypoint-platform-watchmen
python web/app.py        # starts on port 5050
```

The scheduler runs `collect_all` every 60 minutes automatically.  
Manual job trigger: `POST http://localhost:5050/jobs/run/collect_all`

## Key directories

```
scripts/        Core logic (auth, audit, alerts, jobs, inventory, usage, config)
web/            Flask app (app.py) + templates/
config/         settings.json, entitlements.json, audit_rules.json
data/           watchmen.sqlite (DB), raw usage JSON, scheduler state
docs/           project-state.md, SECURITY.md, other docs
tests/          pytest suite (117 passing, 4 skipped)
.env            Credentials — NEVER commit (in .gitignore)
```

## Authentication

OAuth 2.0 Client Credentials (Connected App "Watchmen Dashboard"):
- Credentials in `.env`: `ANYPOINT_CLIENT_ID`, `ANYPOINT_CLIENT_SECRET`
- Token endpoint: `POST /accounts/api/v2/oauth2/token` (client_secret_post)
- TTL ~54 min, auto-renewed at 90% lifetime by `load_auth()`
- `ANYPOINT_USERNAME` / `ANYPOINT_PASSWORD` removed — OAuth only

## Database

SQLite at `data/watchmen.sqlite`. Key tables:
- `usage_snapshot`, `usage_record` — usage metrics
- `entitlement_status` — computed status per meter
- `alert_event` — all alerts (usage + audit), lifecycle: open→acknowledged→resolved
- `job_run` — job history
- `audit_poll_run`, `audit_event` — audit log
- `inventory_*` — apps, APIs, environments, BGs

## Audit pipeline (as of 2026-06-03)

**Incremental polling**: starts from `window_end_utc` of last successful run, not 7 days back. Falls back to `audit.max_lookback_hours` (24h) if no prior run or service was down.

**API-level action filter**: `audit.exclude_actions` in settings.json drives a whitelist sent to `POST /audit/v2/.../query`. Excluded: login, logout, read, view, list, search, get, permissions change. Combined with incremental polling → ~99% volume reduction vs. original naive polling.

**Risk classifier** (`audit_normalizer.py:classify_risk`): combo targets use **exact match** on `object_type` (case-insensitive), substring only on `subaction`. Rules in `audit_rules.json` — combos checked before broad action tier. Warning broad tier: only `create` (catch-all). Critical/Warning defined by explicit combos (see `config/audit_rules.json`).

**Auto-resolution**: audit alerts (`rule=audit_risky_change`) auto-resolve after `audit.auto_resolve_days` days (default 7) via `resolve_old_audit_alerts()` in `usage_store.py`.

## Alert / notification pipeline

- Two alert sources: usage/entitlement rules (`alerts.py`) + audit risky events (`audit_services.py`) — both write to `alert_event` table
- `collect_all` AND `collect_audit` jobs both call `send_alert_email()` via `_notify()` helper in `watchmen_jobs.py`
- Email: `sender@example.com` → recipients in `settings.json alerts.email.to` (Gmail App Password in `.env`)
- Recipients managed exclusively via Settings UI → `settings.alerts.email.to` — no env var override
- Alert email URL uses `app_base_url` from settings.json (default `http://localhost:5050`)

## settings.json — key fields

```json
{
  "app_base_url": "http://localhost:5050",
  "audit": {
    "auto_resolve_days": 7,
    "max_lookback_hours": 24,
    "exclude_actions": ["login","logout","read","view","list","search","get","permissions change"]
  },
  "alerts": {
    "email": { "to": ["alerts@example.com"] }
  },
  "scheduler": { "interval_value": 60, "interval_unit": "minutes" }
}
```

## Entitlement limits (as of 2026-07-24)

Reconciled against the **vendor Monthly Usage Summary** (official invoice, Statement Period Jun 2026 — `resources/usage-summary-2026-06.pdf`). `config/entitlements.json` now uses `operating_mode: contract_confirmed`. Confirmed limits:

| Metric | Limit | Model |
|---|---|---|
| Mule Flows | 280 | high watermark |
| Mule Messages | 47,000,000 | drawdown |
| Data Throughput | 56,500 GB | drawdown |
| API Manager Production | 52 | high watermark |
| API Manager Pre-Production | 52 | high watermark |
| APIs Under Governance | 52 | high watermark |
| Flex Gateway API Calls | 120,000,000 | drawdown |
| API Access Requests | 100 | drawdown |
| IDP Processed Pages | 300,000 | drawdown (9M Automation Credits @ 30/page) |

**Monitoring tier (as of 2026-07-24):** the collector now pulls **all 27 meters** the Usage API exposes for this org (`config/anypoint_usage_meters.json`), up from 14. Meters with no contractual limit on the invoice are entitlements with `usage_model: "MONITOR"` and `limit: 0` — tracked for awareness only, **no thresholds, no alerts**. `evaluate_alerts()` in `alerts.py` excludes any status where `usage_model == "MONITOR"` or `limit_value <= 0`. The dashboard renders two tables: **Contract Entitlements** (invoice order, alertable) and **Additional Metrics — Monitoring** (`usage_model == 'MONITOR'`).

Notable monitoring meters: **LLM Proxy tokens** (`llm_total_tokens` / `llm_prompt_tokens` / `llm_completion_tokens` = Usage API `flex_total_tokens`/`flex_prompt_tokens`/`flex_completion_tokens`) — real consumption (~37k tokens/28d) but **not billed on the contract**, so absent from the invoice. Also: Flex outbound bytes, extra Anypoint MQ meters, Object Store, Composer, RPA, API Experience Hub, and RTF CPU core meters (0 usage — no RTF clusters).

`object_store_effective_api_requests` and `anypoint_mq_api_requests` were downgraded from provisional entitlements to MONITOR (not on invoice).

## Pending work (as of 2026-06-03)

### Audit/Alerts UI overhaul — COMPLETE (2026-06-03)
- ✅ `permissions change` excluded at API level (was 64% of noise)
- ✅ `audit_rules.json` redesigned: explicit governance combos, exact object_type matching
- ✅ Historical noise purged from DB (1,671 events deleted)
- ✅ Settings UI: new "Excluded at API level" section, Info tier removed
- ✅ Audit filter buttons: tier-colored (red/amber/purple), readable

### REQ-001 — IDP Accelerator Demo (admin actions remaining)
- Connected App `IDP-Accelerator-Demo-connected-app` created ✅
  - Client ID / Secret: (stored separately — not in repo)
  - Scope: Execute Published Actions / poc-sandbox
- IDP Manage Actions permission granted to the two demo users ✅
- Share clientId/clientSecret with the demo users ⏳
- Confirm 1 pre-prod API Manager instance allocated ⏳
- Set cleanup reminder for 2026-06-30 ⏳

### Backlog (lower priority)
- Export/reporting (PDF/CSV)
- Multi-org support
- Notification wiring for inventory changes

## Anypoint org structure

- Root: Example-Org (`<org-id>`)
- Sub-orgs: poc-sandbox, training, GTM-demos
- 4 BGs, 4 environments, 5 CloudHub 2.0 apps

## Connected Apps in the org

- **Watchmen Dashboard** — OAuth for this monitoring tool (client_credentials, Usage/Audit/Inventory scopes)
- **IDP-Accelerator-Demo-connected-app** — IDP demo for the demo users (Execute Published Actions / poc-sandbox)
- A pre-existing CRM integrations app

## Recent git log (top 5)

```
38f3576  docs: document audit/alert pipeline overhaul (2026-06-03)
fecdcc4  chore: update latest usage status snapshot
b07c434  feat: API-level audit action filter — exclude info-tier events at source
228c195  feat: incremental audit polling — pull only since last successful run
10a764d  fix: repair audit/alert notification pipeline and add auto-resolution
```
