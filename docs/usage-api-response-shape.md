# Usage API Response Shape

## Source

Observed from real Usage API calls on 2026-05-15.

Endpoint:

```text
POST /metering/usage/api/v1/meters:search
```

## Watchmen Raw Snapshot Shape

```json
{
  "run_id": "usage-...",
  "host": "https://anypoint.mulesoft.com",
  "collected_at_utc": "2026-05-15T01:08:16.225271+00:00",
  "window_utc": {
    "start": "2026-04-12T00:00:00+00:00",
    "end": "2026-05-11T23:59:59+00:00"
  },
  "timeseries": "P1D",
  "dimensions": true,
  "results": [
    {
      "key": "mule_messages",
      "meter": "runtime_mule_message_count",
      "measurement": "mule_message_count",
      "query": "SELECT ...",
      "ok": true,
      "response": {
        "metadata": {
          "responseAsOf": "2026-05-15T01:08:16.940931358Z"
        },
        "data": []
      }
    }
  ]
}
```

## Usage API Meter Response Shape

Observed empty response:

```json
{
  "metadata": {
    "responseAsOf": "2026-05-15T01:08:16.940931358Z"
  },
  "data": []
}
```

Synthetic non-empty row shape follows the fields selected in `config/anypoint_usage_meters.json`.

Example:

```json
{
  "metadata": {
    "responseAsOf": "2026-05-15T01:08:16.940931358Z"
  },
  "data": [
    {
      "timestamp": 1775952000000,
      "org_name": "Example-Org",
      "env_name": "demo-nonprod",
      "env_type": "Sandbox",
      "app_name": "demo-order-api",
      "deployment_model": "CloudHub 2.0",
      "mule_message_count": 25000
    }
  ]
}
```

## Notes

- Empty real responses are structurally useful for the wrapper and metadata.
- Synthetic fixtures are required to validate row-level parsing until real usage exists.
- Fixture rows must use selected meter fields, not arbitrary invented fields.
- If future real responses use `startTime`/`endTime` instead of `timestamp`, the normalizer already supports both.
