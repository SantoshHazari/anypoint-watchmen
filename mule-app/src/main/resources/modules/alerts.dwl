%dw 2.0
import fmtNum from modules::util

fun usageMessage(s): String =
  "$(s.name) at $(fmtNum(s.pct_used))% of limit ($(fmtNum(s.usage_value)) / $(fmtNum(s.limit_value)) $(s.unit default ''))"

// statuses: entitlement_status rows. openAlerts: unresolved alert_event rows with rule usage_threshold.
// Returns the inserts, severity updates and auto-resolutions needed. One unresolved alert per meter.
fun planUsageAlerts(statuses: Array, openAlerts: Array, at: String) = do {
  var hot = statuses filter ((s) -> (s.status == "WARNING") or (s.status == "CRITICAL"))
  var hotKeys = hot map ((s) -> s.meter_key)
  var openBySubject = openAlerts groupBy ((a) -> a.subject)
  ---
  {
    inserts: (hot filter ((s) -> openBySubject[s.meter_key] == null)) map ((s) -> {
      rule: "usage_threshold",
      severity: lower(s.status),
      subject: s.meter_key,
      message: usageMessage(s),
      source_ref: s.meter_key,
      created_at: at
    }),
    updates: (hot filter ((s) -> (openBySubject[s.meter_key] != null)
                and (openBySubject[s.meter_key][0].severity != lower(s.status)))) map ((s) -> {
      id: openBySubject[s.meter_key][0].id,
      severity: lower(s.status),
      message: usageMessage(s),
      updated_at: at
    }),
    resolves: (openAlerts filter ((a) -> not (hotKeys contains a.subject))) map ((a) -> {
      id: a.id,
      at: at
    })
  }
}

fun auditAlerts(events: Array, at: String): Array =
  (events filter ((e) -> (e.risk == "critical") or (e.risk == "warning"))) map ((e) -> {
    rule: "audit_risky_change",
    severity: e.risk,
    subject: "$(e.object_type default 'Unknown'): $(e.object_name default '-')",
    message: "$(e.user_name default 'Someone') performed '$(e.action default '?')$(if (e.subaction != null) ' / ' ++ e.subaction else '')' on $(e.object_type default 'object') '$(e.object_name default '-')' at $(e.event_ts)",
    source_ref: e.event_id,
    created_at: at
  })
