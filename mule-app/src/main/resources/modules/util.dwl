%dw 2.0

fun isoUtc(dt: DateTime): String = (dt >> "UTC") as String {format: "yyyy-MM-dd'T'HH:mm:ss'Z'"}

fun nowIso(): String = isoUtc(now())

fun epochMs(dt: DateTime): Number = dt as Number {unit: "milliseconds"}

// Audit/usage APIs return timestamps either as epoch millis or ISO strings.
fun toIso(v): String =
  if (v is Number) isoUtc(v as DateTime {unit: "milliseconds"})
  else if ((v is String) and not isEmpty(v)) isoUtc(v as DateTime)
  else nowIso()

// H2 returns upper-case column names, PostgreSQL lower-case: normalise so flows and the UI see one shape.
fun lowerKeys(rows) = (rows default []) map ((row) -> row mapObject ((v, k) -> {(lower(k as String)): v}))

fun fmtNum(n): String = if (n == null) "-" else (n as Number) as String {format: "#,##0.##"}

fun truncate(s, maxLen: Number): String | Null =
  if (s == null) null
  else if (sizeOf(s as String) <= maxLen) s as String
  else (s as String)[0 to maxLen - 1]
