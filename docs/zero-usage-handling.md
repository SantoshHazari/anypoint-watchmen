# Zero Usage Handling & Data Completeness

## Overview

When applications are stopped or inactive, the Anypoint API may not return usage data for those days. Previously, the dashboard would show "no data" (blank spaces), which could be ambiguous - was it a collection failure, or genuinely zero activity?

**Solution**: The system now explicitly stores and displays **zero-value records** for days with no activity, making it clear that zero consumption is a legitimate, intentional data point.

## How It Works

### Automatic Detection & Filling (Future Collections)

Starting with the latest code, each time `collect_all` runs (every 60 minutes):

1. **Collect**: Fetch usage data from Anypoint API
2. **Normalize**: Convert API responses to standard records
3. **Detect Gaps**: Check if API data covers the full collection window
   - Example: Window is May 14-28, but API only returns May 14-25 for `mule_messages`
4. **Fill Zeros**: Insert explicit `value = 0.0` records for missing dates
5. **Store**: Persist all records (including zeros) to database

#### Code Location

- **Function**: `fill_missing_dates_with_zeros()` in `scripts/watchmen_services.py`
- **Integration**: Called after `normalize_result()` in `collect_usage()`
- **Logging**: Logs which dates were filled, e.g.:
  ```
  Filling 3 missing dates with zeros for mule_messages (have 12 dates with data)
  ```

### Retroactive Backfill

For historical data gaps (e.g., May 26-28, 2026):

**Script**: `scripts/backfill_zero_usage.py`

**Usage**:
```bash
python scripts/backfill_zero_usage.py
```

**What it does**:
- Scans database for all meters
- For each meter, finds dates in the range with no data
- Inserts zero-value records for those dates
- Reports summary of what was inserted

**Example output**:
```
Backfill Summary:
  Dates: 2026-05-26 to 2026-05-28
  Meters processed: 4
  Records inserted: 12
  
Details:
  mule_messages:
    Missing dates: ['2026-05-26', '2026-05-27', '2026-05-28']
    Records inserted: 3
  data_throughput:
    Missing dates: ['2026-05-26', '2026-05-27', '2026-05-28']
    Records inserted: 3
  ... (etc)
```

## Data Model

### Zero-Value Records

Records inserted for zero-consumption days have these characteristics:

| Field | Value | Notes |
|-------|-------|-------|
| `value` | `0.0` | Explicitly zero |
| `measurement` | `zero_fill` | (for backfill) or normal measurement name (auto-fill) |
| `raw_record` | `{"zero_fill": true, "reason": "no_api_data"}` | (for backfill) |
| `period_start_utc` | Day start, e.g., `2026-05-26T00:00:00+00:00` | Standardized format |
| `period_end_utc` | Next day start, e.g., `2026-05-27T00:00:00+00:00` | For P1D timeseries |

### Deduplication

The database `INSERT OR IGNORE` statement ensures duplicates are skipped if:
- Same `snapshot_id`, `meter_key`, `period_start_utc`, `period_end_utc`, etc. already exist

This means if the API later returns data for a previously-filled date, the new data takes precedence (via ROW_NUMBER deduplication in queries).

## Dashboard Impact

### Graphs

The daily usage chart now shows:
- ✅ Non-zero days: Bars with actual values
- ✅ Zero days (apps stopped): Bars at zero height (no blank space)
- ✅ Rolling window: Last 15 days with complete coverage

**Before**: May 25 → (blank) → June X

**After**: May 25 → May 26 (0) → May 27 (0) → May 28 (0) → June X

### Tables

Entitlement status tables include zero-consumption days, providing a complete audit trail:
- Which days had zero activity
- When activity resumed
- Accurate burn rate calculations over the window

## Use Cases

### Scenario 1: Scheduled Maintenance
- Applications stopped for maintenance on May 26-28
- API returns zero activity
- Dashboard correctly shows `0` for those dates
- Burn rate KPIs reflect the interruption

### Scenario 2: Partial API Response
- Collection window: May 14-28
- API only returns data through May 25 (processing delay)
- System auto-fills May 26-28 with zeros
- Within 24-48 hours, real data arrives and replaces zeros

### Scenario 3: Historical Gap Recovery
- Discover that May 26-28 data is missing
- Run `backfill_zero_usage.py` retroactively
- Dashboard updates to show the complete picture
- KPIs recalculate based on new baseline

## Configuration

### Days Limit (Rolling Window)

In `config/settings.json`:
```json
{
  "dashboard": {
    "days_limit": 15
  }
}
```

This controls how many days of history appear on the dashboard:
- `10`: Last 10 days
- `15`: Last 15 days (default)
- `30`: Last 30 days

**Note**: All historical data is preserved in the database; only the display window is limited.

## Monitoring

### Check for Recent Fills

```sql
SELECT 
  DATE(period_start_utc) as date,
  meter_key,
  COUNT(*) as zero_count
FROM usage_record
WHERE value = 0.0
  AND raw_record_json LIKE '%zero_fill%'
GROUP BY DATE(period_start_utc), meter_key
ORDER BY period_start_utc DESC;
```

### Verify Auto-Fill Execution

Check logs:
```bash
grep "Filling.*missing dates" /var/log/watchmen.log
```

Expected output:
```
[2026-05-28 19:30:15] Filling 0 missing dates with zeros for mule_messages (have 18 dates with data)
[2026-05-28 19:30:15] Filling 0 missing dates with zeros for data_throughput (have 18 dates with data)
```

(Zero fills when API has complete coverage; non-zero when API returns partial data)

## Troubleshooting

### Question: Why do I see zeros for recent dates?

**Answer**: The Anypoint API may have a processing delay (typically 24-48 hours) before returning timeseries data. Zeros indicate the system detected missing data and filled it. Once the API returns the actual values, they'll replace the zeros.

### Question: Can I distinguish between "zero activity" and "zero-fill placeholders"?

**Answer**: Yes. Check the `raw_record_json` field:
- `{"zero_fill": true}` = Backfill placeholder
- `{"zero_fill": false}` or normal payload = Actual zero from API
- `null` or JSON with data = Non-zero record

### Question: Will the dashboard show stale data if API is down?

**Answer**: No. The `fill_missing_dates_with_zeros()` function only runs when `collect_all` succeeds. If the job fails, no fills occur. This prevents false "zero" records when collection actually failed.

## See Also

- `scripts/backfill_zero_usage.py` - Backfill script
- `scripts/watchmen_services.py` - `fill_missing_dates_with_zeros()` function
- `config/settings.json` - Rolling window configuration
- Dashboard graphs - Visual confirmation of zero days
