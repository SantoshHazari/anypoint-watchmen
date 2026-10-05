# Solution Evolution Plan

## Current Capability

Implemented:

- Usage API connectivity validated.
- Token-based auth works.
- Auth logic separated into `scripts/anypoint_auth.py`.
- Usage API HTTP client separated into `scripts/anypoint_usage_api.py`.
- Probe CLI remains in `scripts/anypoint_usage_probe.py`.
- Meter catalog exists in `config/anypoint_usage_meters.json`.
- Zero-usage baseline captured.

Current limitation:

- The solution queries usage but does not yet normalize, persist, compare against entitlements, or alert.

## Target

Build a lightweight governance control plane for the Anypoint presales org:

- usage ingestion
- normalized local history
- entitlement comparison
- threshold/burn-rate alerts
- object/change detection
- governance reports
- later: dashboard/API

## Recommended Architecture

```text
Anypoint Usage API
        |
        v
Usage Extractor
        |
        v
Normalizer
        |
        v
Local Persistence
        |
        +--> Threshold Engine
        +--> Burn-rate Engine
        +--> Reporting
        +--> Alert Dispatch
```

Secondary source:

```text
Anypoint Audit Log API
        |
        v
Change Detection
        |
        v
Governance Alerts
```

## Proposed Modules

| Module | Responsibility |
|---|---|
| `anypoint_auth.py` | Token/connected-app auth. |
| `anypoint_usage_api.py` | Raw Usage API client. |
| `usage_meters.py` | Load/validate meter catalog. |
| `usage_extractor.py` | Query all configured meters for a time window. |
| `usage_normalizer.py` | Convert API responses into stable internal records. |
| `usage_store.py` | Persist snapshots and query history. |
| `entitlements.py` | Load max allowed usage and threshold policy. |
| `thresholds.py` | Calculate percent used, remaining, severity, burn rate. |
| `alerts.py` | Send or render alert events. |
| `reports.py` | Produce Markdown/CSV/JSON summaries. |
| `audit_api.py` | Query Audit Log API later. |

## Persistence Recommendation

Start with SQLite, not in-memory.

Reason:

- The value of this solution is historical trend and burn-rate analysis.
- In-memory state disappears between runs.
- JSON files are acceptable for raw snapshots, but weak for querying.
- SQLite is zero-infrastructure, file-based, portable, and good enough for this scale.

Recommended structure:

- `data/raw/usage/YYYY-MM-DD/*.json`
- `data/watchmen.sqlite`

Tables:

- `usage_snapshot`
- `usage_record`
- `entitlement_limit`
- `threshold_event`
- `run_log`

Minimum `usage_record` fields:

- `snapshot_id`
- `meter_key`
- `meter_name`
- `measurement`
- `meter_type`
- `timeseries`
- `period_start_utc`
- `period_end_utc`
- `org_id`
- `org_name`
- `env_id`
- `env_name`
- `env_type`
- `asset_id`
- `asset_name`
- `app_name`
- `deployment_model`
- `value`
- `raw_dimensions_json`

## Entitlement Config

Create a machine-readable limits file:

```text
config/entitlements.json
```

Fields:

- `key`
- `name`
- `limit`
- `unit`
- `meter_key`
- `measurement`
- `usage_model`
- `source`
- `status`
- `warning_thresholds`

Important:

- Mark current limits as `provisional` until contract is reconciled.
- Keep conflicting values visible, not hidden.

## Threshold Model

Use two alert classes.

### Absolute Thresholds

For each entitlement:

- 25%: unexpected usage review
- 50%: admin review
- 70%: presales lead review
- 80%: creation freeze
- 90%: cleanup/escalation
- 95%: emergency control

### Burn-rate Thresholds

Calculate:

- 1-day burn
- 7-day burn
- 30-day burn
- projected exhaustion date
- expected usage by current contract day

Alert when:

- projected exhaustion is before contract end
- recent burn rate exceeds planned runway
- any product expected to be zero has non-zero usage

## High-Watermark vs Drawdown

Handle differently.

High watermark:

- Mule flows
- API Manager Prod
- API Manager PreProd
- governed APIs
- likely private spaces/network connections

Rule:

- Evaluate max observed value in period.
- Alert on object count and stale objects.

Drawdown:

- Mule messages
- data throughput
- Flex Gateway calls
- Object Store requests
- MQ requests/message units
- automation credits

Rule:

- Sum usage across period.
- Calculate cumulative usage against contract window.
- Alert on burn rate.

## Near-Term Backlog

### Increment 1: Local Data Foundation

Deliverables:

- `usage_extractor.py`
- `usage_normalizer.py`
- `usage_store.py`
- SQLite schema
- raw JSON snapshot retention
- CLI command:

```powershell
python scripts\usage_collect.py --dimensions
```

Acceptance criteria:

- One command queries Usage API.
- Raw response is saved.
- Normalized records are inserted into SQLite.
- Re-running does not duplicate records.

### Increment 2: Entitlement Comparison

Deliverables:

- `config/entitlements.json`
- `entitlements.py`
- `thresholds.py`
- CLI command:

```powershell
python scripts\usage_status.py
```

Acceptance criteria:

- Shows usage vs limits.
- Marks provisional/conflicting limits.
- Separates high-watermark and drawdown.
- Emits machine-readable JSON and human-readable Markdown.

### Increment 3: Alert Events

Deliverables:

- `alerts.py`
- threshold-event table
- local alert report
- optional email/webhook later

Acceptance criteria:

- Threshold crossing creates one event.
- Same threshold is not repeatedly emitted every run unless state changes.
- Alert output includes owner attribution when available.

### Increment 4: Audit Change Detection

Deliverables:

- Audit Log API client.
- Detection rules for risky events:
  - new user
  - permission change
  - connected app creation
  - API Manager instance creation
  - Runtime deployment
  - Exchange publish
  - Governance profile activation

Acceptance criteria:

- Daily change report.
- Alerts for unexpected changes in a supposedly controlled org.

### Increment 5: Operational Reporting

Deliverables:

- weekly Markdown report
- CSV summary
- optional simple HTML dashboard

Acceptance criteria:

- Admin can answer:
  - what changed?
  - what consumed?
  - who/what owns it?
  - how close are we to overage?
  - what needs cleanup?

## Questions To Decide

1. What is the contract start/end date for burn-rate calculations?
2. Should we treat current entitlement values as provisional until the signed contract arrives?
3. Do you want SQLite as the first persistence layer?
4. Where should alert notifications go first: file report, email, Teams, Slack, or webhook?
5. Do you want this to run locally on your machine, on a small VM, or later inside Anypoint itself?
6. Should the tool enforce a "zero expected usage" alert for all meters until users/apps are intentionally onboarded?
7. What is the canonical org/BG naming model: only root + `Example-Org`, or more BGs expected soon?
8. Should threshold policy be conservative soft caps below contract limits, for example 70% of actual entitlement as operating max?
9. Do we need to track demo ownership manually at first, or can we derive ownership from app/API metadata later?
10. Should we include Audit Log API in the next increment or focus only on usage first?

## Recommended Decision

Proceed with Increment 1 next:

- SQLite persistence.
- Raw JSON snapshots.
- Normalized usage records.
- No alerting yet.

Rationale:

- Without persistent normalized history, thresholds and burn-rate calculations will be unreliable.
- SQLite keeps the solution local and simple while preserving enough structure for later reports and alerts.
