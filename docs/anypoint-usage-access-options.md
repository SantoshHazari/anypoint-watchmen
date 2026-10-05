# Anypoint Usage Access Options

## Direct Answer

Codex does not currently have access to your Anypoint tenant.

Available technical paths:

| Path | Can Codex use it? | Best for | Verdict |
|---|---|---|---|
| Anypoint Usage API with connected app/client credentials | Yes, if you create least-privilege credentials or token locally | Automation | Best target architecture |
| `curl` with bearer token | Yes, if a token is provided locally | API validation/prototyping | Best immediate test |
| CSV export from Usage Reports UI | Yes, if you export/upload or if browser automation has authenticated access | Fallback/bootstrap | Good backup |
| Browser automation against Anypoint UI | Yes, only if authenticated in the in-app browser/session Codex can access | UI discovery/manual export | Fallback only |
| Existing desktop Chrome session | Not currently guaranteed | Reusing your logged-in browser | Needs explicit browser/profile access support |

## Recommended Approach

Use API-first automation.

Order:

1. Validate Usage Reports manually in the UI.
2. Validate Anypoint Usage API with a short-lived bearer token and `curl`.
3. Create a least-privilege connected app for recurring automation.
4. Build a scheduled extractor that persists daily/monthly usage snapshots.
5. Use browser automation only for UI discovery or if API scope is blocked.

## Why API First

- Official Usage API exists.
- Usage data is queryable by product meter.
- API returns daily/monthly usage.
- Data includes business group/application dimensions where available.
- API output is easier to diff, alert, store, and audit than screenshots/CSV downloads.

Reference:

- https://docs.mulesoft.com/general/usage-reports
- https://anypoint.mulesoft.com/exchange/portals/anypoint-platform/f1e97bc6-315a-4490-82a7-23abe036327a/usage-api/

## Usage API Details

Base path pattern:

```text
https://<control-plane-host>/metering/usage/api/v1
```

Typical hosts:

- US: `https://anypoint.mulesoft.com`
- EU: `https://eu1.anypoint.mulesoft.com`

Core endpoints:

```bash
GET  /metering/usage/api/v1/meters:describe
GET  /metering/usage/api/v1/meters/{meterName}:describe
POST /metering/usage/api/v1/meters:search
```

Auth:

- Bearer token required.
- Caller must have `Usage Viewer`.
- `Usage Viewer` must be assigned at root org level.

Known API restrictions:

- `timestamp` filter is mandatory.
- `TIMESERIES` is mandatory.
- Daily granularity `P1D` cannot span more than 30 days.
- Other queries have max 60-day range.
- Query end time cannot be in the last 3 days.
- `SELECT *` is not supported.
- At least one measurement must be selected.
- Column aliases are not supported.

## First Curl Test

Assumption:

- User provides a valid bearer token locally as environment variable.
- Do not paste long-lived secrets into chat.

PowerShell:

```powershell
$env:ANYPOINT_HOST = "https://anypoint.mulesoft.com"
$env:ANYPOINT_TOKEN = "<set locally, do not commit>"

curl.exe -s `
  "$env:ANYPOINT_HOST/metering/usage/api/v1/meters:describe" `
  -H "Content-Type: application/json" `
  -H "Authorization: Bearer $env:ANYPOINT_TOKEN"
```

Expected result:

- JSON describing available meters.
- We then identify meter names for:
  - runtime flow count
  - runtime messages
  - runtime network bytes
  - API Manager
  - API Governance
  - Flex/Omni Gateway
  - MQ
  - Object Store

## Example Usage Queries

Runtime flows monthly:

```powershell
curl.exe -s `
  "$env:ANYPOINT_HOST/metering/usage/api/v1/meters:search" `
  -H "Content-Type: application/json" `
  -H "Authorization: Bearer $env:ANYPOINT_TOKEN" `
  -d '{ "query": "SELECT mule_flow_count FROM runtime_flow_count WHERE timestamp between 1704067200000 and 1706745599000 TIMESERIES P1M" }'
```

Runtime network bytes monthly:

```powershell
curl.exe -s `
  "$env:ANYPOINT_HOST/metering/usage/api/v1/meters:search" `
  -H "Content-Type: application/json" `
  -H "Authorization: Bearer $env:ANYPOINT_TOKEN" `
  -d '{ "query": "SELECT network_bytes_count FROM runtime_network_bytes_count WHERE timestamp between 1704067200000 and 1706745599000 TIMESERIES P1M" }'
```

## Browser Automation Assessment

Browser automation is viable for:

- Confirming the UI navigation.
- Reading visible Usage Reports screens.
- Exporting CSV if the browser session is authenticated and download is allowed.
- Capturing screenshots for the governance runbook.

Browser automation is not ideal for:

- Recurring monitoring.
- Reliable threshold checks.
- Contractual usage reconciliation.
- Secrets handling.
- Long-term unattended execution.

Use it as fallback or setup discovery, not as the control plane for governance.

## Access Model Recommendation

### Short-term validation

- You generate a temporary bearer token.
- Store it locally in `$env:ANYPOINT_TOKEN`.
- Codex runs `curl` from the workspace.
- We inspect meter names and response shapes.

### Long-term automation

- Create connected app:
  - type: app acts on its own behalf
  - grant: client credentials
  - scope: least privilege needed for Usage API / Usage Viewer
  - root org only
- Store secret outside repository.
- Schedule extractor.
- Persist normalized data.
- Alert from normalized data.

### If connected app cannot access Usage API

Fallback options:

- delegated user OAuth flow
- manually refreshed token for prototype only
- scheduled CSV export if API access is blocked
- browser automation for interim validation only

## Security Rules

- Do not paste long-lived tokens or client secrets into chat.
- Use local environment variables or a local secret store.
- Do not commit `.env` or token files.
- Create a dedicated automation identity.
- Assign only `Usage Viewer` and required read scopes.
- Rotate client secret.
- Audit connected app usage.

## Decision

Best path:

1. API-first with Usage API.
2. `curl` for initial validation.
3. Connected app for durable automation.
4. Browser only for UI discovery and fallback CSV export.
