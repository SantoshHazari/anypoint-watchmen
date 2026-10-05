# Anypoint Platform Watchmen

A self-hosted dashboard that watches a MuleSoft Anypoint Platform organization so you don't have to: it tracks usage against contract entitlements, alerts before consumption patterns turn into overage charges, monitors the audit log for risky governance changes, and can automatically stop non-production apps outside working hours.

Built with Python + Flask + SQLite. One external dependency. Runs anywhere Python runs.

## Why this exists

Our Anypoint organization runs on a fixed entitlement package: as long as consumption stays inside the included limits (Mule Messages, Data Throughput, Flex Gateway calls, IDP pages, etc.), it costs nothing extra — but anything beyond those limits is billed, and usage-based overage on an integration platform gets expensive quickly.

Anypoint's native usage view tells you what you *have consumed*, after the fact, if you go look. It does not project where you're heading, it doesn't alert, and it doesn't know your contract limits. Watchmen closes that gap:

- It knows the actual entitlement limits (reconciled against the official usage summary for the contract) and continuously compares real consumption against them.
- It computes burn rates and projections, and raises alerts **before** a limit is crossed — not after the invoice arrives.
- It emails those alerts to whoever should care.

## Features

| Area | What it does |
|---|---|
| **Usage & entitlements** | Collects all 27 meters the Anypoint Usage API exposes for the org, hourly. Compares the 9 contract entitlements against their limits; tracks the rest as monitoring-only metrics (incl. LLM Proxy tokens). |
| **Alerting** | 6 alert rules: threshold crossed (50/70/80/90/95%), projected contract overage, monthly budget exceeded, high-watermark near limit, burn-rate acceleration, daily budget consumption. Alert lifecycle: open → acknowledged → resolved, with email notification for new alerts. |
| **Audit log monitoring** | Incremental polling of the Audit Log API with API-level noise filtering (~99% volume reduction). Risk classification of governance events (critical/warning) via configurable action+object rules — e.g. deletion of an application, creation of an environment. Risky events become alerts too. |
| **Inventory** | Business groups, environments, CloudHub apps, and API Manager instances — with change tracking between collections. |
| **Runtime control** | Start/stop CloudHub applications from the UI; stop-all per environment. |
| **App lifecycle schedules** | A schedule engine that automatically starts/stops apps on configurable schedules (per environment or per app, with always-on overrides and one-time actions). Shutting down non-prod apps out of hours directly reduces metered consumption. |
| **Jobs & scheduler** | Background scheduler runs collection every 60 minutes. Full job history with per-run detail; any job can be triggered manually from the UI. |
| **Settings UI** | Alert thresholds, entitlement limits, audit risk rules, excluded audit actions, email recipients, and scheduler interval — all editable from the browser, persisted to `config/`. |

## Architecture at a glance

```
Anypoint Platform APIs                      Watchmen (this repo)
┌─────────────────────┐      OAuth 2.0     ┌──────────────────────────────┐
│ Usage/Metering API  │◄──Connected App───►│ Collectors (scripts/)        │
│ Audit Log API       │                    │   collect_all / _inventory / │
│ Accounts API        │                    │   _audit  (hourly scheduler) │
│ Runtime Mgr APIs    │                    ├──────────────────────────────┤
└─────────────────────┘                    │ SQLite (data/watchmen.sqlite)│
                                           ├──────────────────────────────┤
                        SMTP ◄─────────────│ Alert engine (scripts/)      │
                                           ├──────────────────────────────┤
                        Browser ◄──────────│ Flask UI (web/, port 5050)   │
                                           └──────────────────────────────┘
```

No message broker, no external database, no frontend framework — deliberately boring so it's easy to run and easy to hand over.

## Requirements

- Python 3.10+
- Flask (`pip install -r requirements.txt` — that's the whole list)
- An Anypoint **Connected App** (client credentials) with read scopes for Accounts/Environments, Usage, Audit Log, and Runtime Manager — plus Runtime Manager *manage* permission if you want start/stop and schedules to work.
- A Gmail account with an **App Password** (or any SMTP account) if you want email alerts.

## Installation

```bash
git clone <repo-url>
cd new-anypoint-platform-watchmen
pip install -r requirements.txt
```

Create a `.env` file in the project root (it is git-ignored — **never commit it**):

```ini
# Anypoint Connected App (Access Management → Connected Apps, "App acts on its own behalf")
ANYPOINT_CLIENT_ID=...
ANYPOINT_CLIENT_SECRET=...
# Optional: ANYPOINT_HOST=https://eu1.anypoint.mulesoft.com  (EU control plane)

# SMTP for alert emails (Gmail App Password recommended)
SMTP_SENDER_EMAIL=...
SMTP_SENDER_PASSWORD=...

# Flask session secret (any random string)
FLASK_SECRET_KEY=...
```

Then start it:

```bash
python web/app.py        # or double-click start.bat on Windows
```

Open **http://localhost:5050**. The background scheduler starts with the app and collects everything every 60 minutes; you can trigger a first collection immediately from the *Jobs* page (`collect_all`).

## Configuration

All runtime configuration lives in `config/` and most of it is editable from the Settings page:

| File | Purpose |
|---|---|
| `config/settings.json` | Scheduler interval, alert recipients (`alerts.email.to`), audit polling options (`exclude_actions`, `max_lookback_hours`, `auto_resolve_days`), `app_base_url` used in alert emails. |
| `config/entitlements.json` | The contract entitlement limits and usage models (`DRAWDOWN` / `HIGH_WATERMARK` / `MONITOR`). Update these when the contract changes — the Usage API only reports consumption, never limits. |
| `config/audit_rules.json` | Which audit action + object combinations count as critical/warning governance events. |
| `config/anypoint_usage_meters.json` | The meter catalog collected from the Usage API. |

## Day-to-day usage

- **Dashboard** (`/`) — contract entitlements vs. limits with projections, plus monitoring-only meters.
- **Alerts** (`/alerts`) — open/acknowledged/resolved alerts; acknowledge or resolve from the UI. Email arrives automatically for new ones.
- **Inventory** (`/inventory`) — what exists in the org; `/inventory/changes` shows what changed between collections.
- **Audit** (`/audit`) — risky governance events, defaulting to the Critical view.
- **Schedules** (`/schedules`) — automated start/stop schedules for apps; `/schedules/log` shows every action taken.
- **Jobs** (`/jobs`) — collection history; re-run anything manually.
- **Settings** (`/settings`) — thresholds, limits, audit rules, recipients, scheduler.

## Testing

```bash
python -m pytest tests
```

~120 tests covering auth, usage normalization/dedup, KPIs, alert rules, audit classification, inventory, runtime control, and the schedule engine. Tests run against fixtures — no live Anypoint credentials needed.

## Honest limitations (read before extending)

This started as a personal tool and has real, known boundaries:

- **Single org** — one Anypoint organization per instance (multi-org is on the backlog).
- **No authentication on the web UI** — it's meant for localhost or a trusted network segment. Put it behind a reverse proxy with auth before exposing it any wider (see `docs/SECURITY.md`).
- **SQLite + single process** — perfectly fine for this workload, not designed for horizontal scale.
- **Email is the only notification channel** — no Teams/Slack webhooks yet.
- **No export/reporting** (PDF/CSV) yet.

The `docs/` folder contains the deeper design notes: `project-state.md` (current state), `web-architecture.md`, `email-alerts-runbook.md`, `watchmen-roadmap.md` (where this could go next), and `SECURITY.md`.

## License / ownership

Internal tooling for a single Anypoint organization. Credentials, org IDs, and contract data are configuration — the code itself is org-agnostic.
