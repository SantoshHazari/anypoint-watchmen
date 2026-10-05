# Synthetic Usage Data

## Two Mechanisms

Watchmen has two ways to populate the database with test data:

### 1. Fixtures (unit testing)

Location: `tests/fixtures/usage/`

Scenarios: `zero.json`, `small.json`, `spike.json`, `near_limit.json`

These use the `fixture-` run_id prefix and are excluded from normal dashboard queries. Use `--include-fixtures` to include them in CLI status calculations.

Regenerate:

```powershell
python scripts\generate_usage_fixtures.py
```

Ingest:

```powershell
python scripts\usage_ingest_fixture.py tests\fixtures\usage\near_limit.json
```

### 2. Synthetic Seeder (development / demo)

Location: `scripts/seed_synthetic.py`

The seeder inserts realistic multi-dimensional usage records directly into SQLite, matching the exact field names and structure of real Anypoint Usage API payloads. Records are included in normal dashboard queries.

Seed:

```powershell
python scripts\seed_synthetic.py --scenario hot
python scripts\seed_synthetic.py --scenario steady
```

Clear:

```powershell
python scripts\seed_synthetic.py --clear
```

## Scenarios

### `hot` — Above-limit burn

Models a team where Flex Gateway API Calls and Mule Messages are burning above sustainable limits. Dashboard shows contract projections over 100%, month ratios in warning/danger range, and exhaustion dates within the contract period.

Useful for validating: warning badges, gauge coloring, projected overage detection, exhaustion date calculations.

### `steady` — Comfortable pace

Models a team cruising well within all budgets. Most meters at 40-60% of projected contract usage, all month ratios in the green range.

Useful for validating: green/ok states, healthy gauge rendering, the difference between a stressed and relaxed dashboard.

## Design Principles

- Daily volumes are calibrated against entitlement limits in `config/entitlements.json`.
- HIGH_WATERMARK meters (flows, API instances, governed APIs) report the same concurrent count each day.
- DRAWDOWN meters (messages, throughput, API calls, MQ requests) accumulate daily with jitter.
- Weekend traffic is 40% of weekday traffic.
- Growth rate is configurable per scenario (1.5%/day for hot, 0.5%/day for steady).
- Seeded data spans from contract start date through today, so all timeframe perspectives (contract, month, day) populate.
- `random.seed(42)` ensures reproducible output.

## Isolation

- Synthetic records use `synthetic-seed-` run_id prefix.
- Fixture records use `fixture-` run_id prefix.
- Normal API collection records use `usage-` run_id prefix.
- Dashboard queries exclude `fixture-` by default but include `synthetic-seed-`.
- `--clear` removes all synthetic records without affecting fixtures or real data.

## Important Caveat

Synthetic data is structurally realistic but not contractual evidence. Use it for UI validation and development. Real API responses remain the source of truth once platform usage starts.
