%dw 2.0
import some from dw::core::Arrays
import toIso, truncate from modules::util

fun norm(s): String = lower(trim((s default "") as String))

// object_type and action: exact (case-insensitive). subaction: substring.
fun comboMatches(evt, c): Boolean =
  ((c.object_type == null) or (norm(evt.object_type) == norm(c.object_type)))
  and ((c.action == null) or (norm(evt.action) == norm(c.action)))
  and ((c.subaction == null) or (norm(evt.subaction) contains norm(c.subaction)))

fun actionIn(evt, actions): Boolean = (actions default []) some ((a) -> norm(a) == norm(evt.action))

// Combos are checked before the broad action tiers.
fun classifyRisk(evt, rules): String = do {
  var crit = rules.tiers.critical default {}
  var warn = rules.tiers.warning default {}
  ---
  if ((crit.combos default []) some ((c) -> comboMatches(evt, c))) "critical"
  else if ((warn.combos default []) some ((c) -> comboMatches(evt, c))) "warning"
  else if (actionIn(evt, crit.broad_actions)) "critical"
  else if (actionIn(evt, warn.broad_actions)) "warning"
  else "info"
}

fun isExcluded(evt, excludeActions): Boolean = actionIn(evt, excludeActions)

// Maps a raw Audit Log Query API v2 record to the audit_event row shape.
// Real records: no id field; object details live in objects[0]; subaction in payload.subaction;
// connected-app actions have clientName instead of userName.
fun normalizeEvent(raw, rules) = do {
  var obj = (raw.objects default [])[0] default {}
  var evt = {
    event_id: truncate(
      (raw.id default
        ((raw.anypointTransactionId default "") as String) ++ "|" ++ ((raw.timestamp default "") as String)
        ++ "|" ++ ((raw.action default "") as String) ++ "|" ++ ((obj.objectId default raw.objectId default "") as String)) as String,
      100),
    event_ts: toIso(raw.timestamp default raw.createdAt),
    action: raw.action,
    subaction: raw.payload.subaction default raw.subaction,
    object_type: obj.objectType default raw.objectType,
    object_name: obj.objectName default raw.objectName default obj.objectId,
    user_name: raw.userName default raw.clientName,
    org_id: raw.organizationId default raw.payload.properties.connectedApp.org_id,
    env_id: obj.environmentId default raw.environmentId
  }
  ---
  evt ++ {
    risk: classifyRisk(evt, rules),
    raw_json: truncate(write(raw, "application/json", {indent: false}), 8000)
  }
}
