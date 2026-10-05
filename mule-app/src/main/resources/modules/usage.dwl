%dw 2.0

// Every numeric value stored under `key` at any depth of a meters:search response.
// Defensive on purpose: the response nests values inside time-series buckets.
fun collectValues(node, key: String): Array<Number> =
  node match {
    case o is Object -> flatten(o pluck ((v, k) ->
        if ((k as String) == key and (v is Number)) [v]
        else if ((k as String) == key and (v is String) and (v matches /-?\d+(\.\d+)?/)) [v as Number]
        else collectValues(v, key)))
    case a is Array -> flatten(a map ((item) -> collectValues(item, key)))
    else -> []
  }

fun sumMeasurement(response, key: String): Number = sum(collectValues(response, key))

fun amqlQuery(meter: String, measurement: String, fromMs: Number, toMs: Number): String =
  "SELECT $(measurement) FROM $(meter) WHERE timestamp between $(fromMs) and $(toMs) TIMESERIES P1M"

// records: [{meter_key, period, usage_value}] for every period since contract start.
fun computeStatuses(cfg, records: Array, period: String, warnPct: Number, critPct: Number, at: String): Array =
  (cfg.entitlements default []) map ((e, idx) -> do {
    var vals = (records filter ((r) -> r.meter_key == e.key)) map ((r) -> r.usage_value as Number)
    // MONITOR shows the latest month that has data (the current month is empty for ~3 days after it starts)
    var latest = ((records filter ((r) -> (r.meter_key == e.key) and (r.period <= period) and ((r.usage_value as Number) > 0)))
                    orderBy ((r) -> r.period))[-1]
    var current = (latest.usage_value default 0) as Number
    var raw = e.usage_model match {
      case "DRAWDOWN" -> sum(vals)
      case "HIGH_WATERMARK" -> max(vals) default 0
      else -> current
    }
    var usage = raw / (e.divisor default 1)
    var limit = (e.limit default 0) as Number
    var pct = if (limit > 0) (usage / limit) * 100 else null
    var status =
      if ((e.usage_model == "MONITOR") or (limit <= 0)) "MONITOR"
      else if (pct >= critPct) "CRITICAL"
      else if (pct >= warnPct) "WARNING"
      else "OK"
    ---
    {
      meter_key: e.key,
      name: e.name,
      usage_model: e.usage_model,
      usage_value: usage,
      limit_value: limit,
      unit: e.unit,
      pct_used: pct,
      status: status,
      sort_order: idx,
      computed_at: at
    }
  })
