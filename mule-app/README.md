# Anypoint Watchmen (Mule 4)

A Mule 4 port of the Anypoint Platform Watchmen dashboard. It monitors one Anypoint org's usage against contract entitlements, keeps an inventory, polls the audit log, raises alerts and emails them. A dashboard and REST API are served on port 5050.

## What runs

| Job (`run-job` flow) | Steps | Trigger |
|---|---|---|
| `collect_all` | inventory → usage → entitlement status → usage alerts → audit → email | scheduler, every 60 min |
| `collect_audit` | audit → email | `POST /jobs/run/collect_audit` |
| `collect_usage` | usage → entitlement status → usage alerts → email | `POST /jobs/run/collect_usage` |
| `collect_inventory` | inventory | `POST /jobs/run/collect_inventory` |

Each step runs in isolation. If one fails, the job is recorded as `partial` and the remaining steps still run. Every run is written to `job_run`.

## Project layout

```
src/main/mule/
  global.xml      HTTP listener/request, DB, SMTP, object store, API error handler
  auth.xml        OAuth client_credentials, token cached and renewed at 90% of its lifetime
  usage.xml       Usage API (AMQL meters:search) -> usage_record -> entitlement_status
  audit.xml       incremental audit polling, API-level action whitelist, risk classification
  alerts.xml      usage + audit alerts, auto-resolve, email notification
  inventory.xml   business groups, environments, CloudHub 2.0 apps, API Manager instances
  jobs.xml        DB init on startup, scheduler, job runner
  settings.xml    runtime-editable settings (app_setting table)
  api.xml         dashboard + REST API
src/main/resources/
  config/app.yaml               ports, DB, scheduler, thresholds, SMTP
  config/entitlements.json      contract limits + Usage API meter mapping
  config/audit_rules.json       critical / warning combos and broad actions
  config/default-settings.json  seeded into app_setting on first start
  modules/*.dwl                 DataWeave logic (risk, usage maths, alert planning)
  sql/schema.sql                tables (H2 and PostgreSQL compatible)
  web/index.html                dashboard
src/test/munit/                 MUnit tests for the DataWeave logic
```

## Run it in Anypoint Studio

1. **File → Import → Anypoint Studio project from File System** and select this folder.
2. In the **Watchmen Dashboard** Connected App (client credentials), grant the scopes the app calls: Usage Viewer, View Audit Logs, View Organization / Environments, Runtime Manager read, and API Manager read.
3. **Run → Run Configurations → Arguments**, add:
   ```
   -M-Danypoint.client.id=<client id>
   -M-Danypoint.client.secret=<client secret>
   -M-Danypoint.orgId=<root org id>
   ```
   These have no defaults on purpose, so the app fails to deploy if they are missing. Never put them in `app.yaml`.
4. Run, then open http://localhost:5050. The first `collect_all` starts about a minute after startup. You can also start it from the **Jobs** tab.

The local database is an H2 file at `~/watchmen-data/watchmen`.

### Email alerts

Set `email.enabled: "true"` and the SMTP user in `app.yaml`. Pass the password with `-M-Demail.smtp.password=<gmail app password>`. Add recipients in the dashboard **Settings** tab.

## Before trusting the numbers: verify the meter mapping

Only `runtime_flow_count / mule_flow_count` and `runtime_network_bytes_count / network_bytes_count` come from MuleSoft's published Usage API examples. Every other `meter` / `measurement` pair in `config/entitlements.json` is a placeholder and marked `"verified": false`.

1. With the app running, call `GET http://localhost:5050/api/usage/meters`. This wraps `meters:describe` and lists every meter your org exposes.
2. Fix each entry's `meter` and `measurement`, then set `verified` to `true`.
3. Also set `contract.id`, `contract.start` and `contract.end` to the values on the invoice.

Meters that fail are logged in `usage_snapshot.errors` and on the Jobs tab. The other meters still collect.

The audit-record and inventory field mappings are also written defensively, accepting several possible field names. Check a first run against the raw data. `audit_event.raw_json` keeps the original record.

## REST API

| Method | Path | Notes |
|---|---|---|
| GET | `/` | dashboard |
| GET | `/health` | |
| GET | `/api/entitlements` | `entitlements` (contract) + `monitoring` |
| GET | `/api/alerts?status=unresolved\|open\|acknowledged\|resolved\|all&limit=` | |
| POST | `/api/alerts/{id}/acknowledge`, `/api/alerts/{id}/resolve` | |
| GET | `/api/audit?risk=all\|critical\|warning\|info&limit=` | events + last 10 polls |
| GET | `/api/inventory` | |
| GET | `/api/jobs` | last 50 runs |
| POST | `/jobs/run/{collect_all\|collect_audit\|collect_usage\|collect_inventory}` | returns 202, runs async |
| GET / PUT | `/api/settings` | recipients, excluded actions, auto-resolve days, lookback hours |
| GET | `/api/usage/meters` | Usage API meter catalogue |

The API has no authentication. Keep it on localhost, or put it behind API Manager / a policy before exposing it.

## Deploying to CloudHub 2.0

The H2 file does not survive restarts on CloudHub. Point `db.url` / `db.driver` at PostgreSQL (`jdbc:postgresql://…`, `org.postgresql.Driver`); the schema is compatible. Set the credentials as **secure** properties in Runtime Manager. `mule-artifact.json` already hides them.

## Tests

`mvn clean test` runs two MUnit suites:

- **watchmen-logic-test-suite**: risk classification, entitlement status maths, usage-alert planning and Usage API response parsing.
- **watchmen-e2e-test-suite**: runs `collect_all` twice against in-memory H2 with mocked Anypoint responses. It checks the job results, entitlement statuses, alerts (and that the second run creates no duplicates), audit exclusion, inventory and the API flows.

The mocks only prove the flows work with the response shapes assumed here. The first run against your real org is still the check on field names.
