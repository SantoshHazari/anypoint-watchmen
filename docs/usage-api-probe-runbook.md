# Usage API Probe Runbook

## Status

Validated on 2026-05-15 against:

```text
https://anypoint.mulesoft.com
```

Results:

- `meters:describe`: HTTP 200.
- Usage search across selected control meters: HTTP 200.
- Dimensional queries with enriched names: HTTP 200.
- Current baseline for 2026-04-12 through 2026-05-11 UTC: no records returned, consistent with zero platform usage.

Generated baseline files:

- `docs/usage-probe-zero-baseline-2026-05-15.json`
- `docs/usage-probe-dimensional-validation-2026-05-15.json`

## Files

- `scripts/anypoint_usage_probe.py`
- `config/anypoint_usage_meters.json`

## How To Run

PowerShell:

```powershell
$env:ANYPOINT_HOST = "https://anypoint.mulesoft.com"
$env:ANYPOINT_TOKEN = "<temporary bearer token>"

python scripts\anypoint_usage_probe.py --dimensions --output docs\usage-probe-latest.json

Remove-Item Env:\ANYPOINT_TOKEN -ErrorAction SilentlyContinue
```

No token is stored by the script.

## What It Queries

- Mule flows
- Mule messages
- data throughput / network bytes
- API Manager production instances
- API Manager pre-production instances
- API Manager unclassified instances
- governed APIs
- Flex Gateway API calls
- Object Store effective API requests
- Anypoint MQ API requests
- Anypoint MQ message units
- IDP processed pages
- Composer tasks
- RPA bot minutes

## API Constraints

- Usage API does not allow the query end time to be in the last three days.
- Daily queries are limited to 30 days.
- `timestamp` and `TIMESERIES` are mandatory.
- `SELECT *` is not supported.

## Next Engineering Step

Promote the probe into a real extractor:

- Use connected app credentials instead of manual bearer tokens.
- Normalize API responses into a stable internal format.
- Add entitlement limits and threshold calculations.
- Add alert routing.
- Persist immutable daily snapshots.
