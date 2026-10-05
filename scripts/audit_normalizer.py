"""Normalize Anypoint Audit Log events from product-specific payloads."""

from __future__ import annotations

import hashlib
import json
import time
from typing import Any


def _pick(raw: dict, *keys: str) -> Any:
    for key in keys:
        if key in raw and raw[key] not in (None, ""):
            return raw[key]
    return None


def _nested(raw: dict, key: str, *fields: str) -> Any:
    value = raw.get(key)
    if isinstance(value, dict):
        return _pick(value, *fields)
    return value if value not in (None, "") else None


def _string(value: Any) -> str:
    if value is None:
        return ""
    return str(value)


def _event_key(raw: dict) -> str:
    existing = _pick(raw, "id", "eventId", "event_id", "auditEventId")
    if existing:
        return str(existing)
    rendered = json.dumps(raw, sort_keys=True, default=str)
    return hashlib.sha256(rendered.encode("utf-8")).hexdigest()[:32]


def _extract_from_objects(raw: dict) -> dict:
    """Extract fields from the objects[] array in the raw audit event.

    The Anypoint Audit API stores key identifiers (objectId, objectName,
    environmentId, environmentName) inside objects[] rather than at the
    top level.  We take the first non-parent object as the primary.
    """
    objects = raw.get("objects")
    if not isinstance(objects, list) or not objects:
        return {}
    # Use the first object entry as the primary source
    obj = objects[0] if isinstance(objects[0], dict) else {}
    return {
        "object_id": _string(obj.get("objectId", "")),
        "object_name": _string(obj.get("objectName", "")),
        "object_type": _string(obj.get("objectType", "")),
        "env_id": _string(obj.get("environmentId", "")),
        "env_name": _string(obj.get("environmentName", "")),
    }


def normalize_audit_event(raw: dict) -> dict:
    product = _string(_pick(raw, "product", "Product", "platform", "Platform"))
    object_type = _string(_pick(raw, "objectType", "object_type", "type", "Type"))
    action = _string(_pick(raw, "action", "Action"))
    event_time = _string(_pick(raw, "time", "Time", "timestamp", "createdAt", "eventDate", "date"))
    object_id = _string(_pick(raw, "objectId", "object_id") or _nested(raw, "object", "id", "objectId"))
    object_name = _string(_pick(raw, "objectName", "object_name") or _nested(raw, "object", "name", "objectName"))
    user_id = _string(_pick(raw, "userId", "user_id") or _nested(raw, "user", "id", "userId"))
    user_name = _string(_pick(raw, "userName", "user_name", "User Name") or _nested(raw, "user", "name", "username", "email"))
    env_id = _string(_pick(raw, "environmentId", "environment_id", "envId") or _nested(raw, "environment", "id", "environmentId"))
    env_name = _string(_pick(raw, "environmentName", "environment_name", "envName") or _nested(raw, "environment", "name", "environmentName"))
    connected_app = _string(_pick(raw, "connectedApp", "connected_app", "Connected App") or _nested(raw, "client", "name", "clientName"))

    # Enrich from objects[] array — Anypoint stores IDs there, not top-level
    obj_data = _extract_from_objects(raw)
    if not object_type and obj_data.get("object_type"):
        object_type = obj_data["object_type"]
    if not object_id and obj_data.get("object_id"):
        object_id = obj_data["object_id"]
    if not object_name and obj_data.get("object_name"):
        object_name = obj_data["object_name"]
    if not env_id and obj_data.get("env_id"):
        env_id = obj_data["env_id"]
    if not env_name and obj_data.get("env_name"):
        env_name = obj_data["env_name"]

    # Extract subaction for richer context
    payload = raw.get("payload") or {}
    subaction = payload.get("subaction", "") if isinstance(payload, dict) else ""

    return {
        "event_key": _event_key(raw),
        "event_time_utc": event_time,
        "product": product,
        "object_type": object_type,
        "action": action,
        "object_id": object_id,
        "object_name": object_name,
        "user_id": user_id,
        "user_name": user_name,
        "env_id": env_id,
        "env_name": env_name,
        "connected_app": connected_app,
        "subaction": subaction,
        "raw": raw,
    }


def extract_audit_events(response: dict | list) -> list[dict]:
    if isinstance(response, list):
        return response
    for key in ("data", "events", "items", "auditEvents", "content"):
        value = response.get(key)
        if isinstance(value, list):
            return value
    return []


# -- Risk tiering --
#
# Events are classified as "critical", "warning", or "info" based on
# action + object type + subaction combinations. Only critical and warning
# are flagged as risky (stored with risky=1 in the DB).
#
# Rules are loaded from config/audit_rules.json (editable via Settings UI).
# If the file is missing or unreadable, hardcoded defaults below are used.

# Hardcoded defaults (fallback if no config file)
_DEFAULT_ACTIONS = {
    "critical": {"delete", "revoke", "disable"},
    "warning": {"create", "deploy", "grant", "enable", "approve", "reject", "start", "stop"},
    "info": {"login", "logout", "read", "view", "list", "search", "get"},
}

_DEFAULT_COMBOS = {
    "critical": {
        ("edit", "entitlement"), ("edit", "edit entitlement"),
        ("create", "organization"), ("delete", "organization"),
        ("create", "environment"), ("delete", "environment"),
        ("delete", "application"), ("delete", "api"), ("delete", "automated policy"),
        ("delete", "user"), ("delete", "role"), ("delete", "team"),
        ("delete", "connectedapp"), ("delete", "connected app"),
    },
    "warning": {
        ("permissions change", "add permissions"), ("permissions change", "remove permissions"),
        ("create", "automated policy"), ("edit", "automated policy"),
        ("create", "application"), ("create", "api"), ("edit", "application"),
        ("create", "connectedapp"), ("create", "connected app"),
        ("create", "alert"),
    },
}

# Cached rules with 60s TTL
_rules_cache: dict = {"data": None, "ts": 0.0}


def _get_rules() -> dict:
    """Load rules from config file with 60s cache. Falls back to defaults."""
    now = time.monotonic()
    if _rules_cache["data"] is not None and (now - _rules_cache["ts"]) < 60:
        return _rules_cache["data"]

    try:
        from watchmen_config import load_audit_rules
        cfg = load_audit_rules()
    except Exception:
        cfg = None

    if cfg:
        actions = {
            tier: set(cfg.get("actions", {}).get(tier, []))
            for tier in ("critical", "warning", "info")
        }
        combos = {}
        for tier in ("critical", "warning"):
            combos[tier] = {
                (c["action"], c["target"])
                for c in cfg.get("combos", {}).get(tier, [])
                if "action" in c and "target" in c
            }
        rules = {"actions": actions, "combos": combos}
    else:
        rules = {"actions": _DEFAULT_ACTIONS, "combos": _DEFAULT_COMBOS}

    _rules_cache["data"] = rules
    _rules_cache["ts"] = now
    return rules


def invalidate_rules_cache() -> None:
    """Force rules to reload on next classify_risk call."""
    _rules_cache["data"] = None
    _rules_cache["ts"] = 0.0


def classify_risk(event: dict) -> str:
    """Classify an audit event as 'critical', 'warning', or 'info'.

    Uses action, object_type, and subaction to determine the risk tier.
    Rules are loaded from config/audit_rules.json (cached 60s).
    """
    rules = _get_rules()
    action = (event.get("action") or "").lower().strip()
    object_type = (event.get("object_type") or "").lower().strip()
    subaction = (event.get("subaction") or "").lower().strip()

    # Check explicit info actions first (logins, reads)
    if action in rules["actions"].get("info", set()):
        return "info"

    # Check critical combos — exact match on object_type, substring on subaction
    for combo_action, combo_target in rules["combos"].get("critical", set()):
        if combo_action == action and (combo_target == object_type or combo_target in subaction):
            return "critical"

    # Check warning combos — exact match on object_type, substring on subaction
    for combo_action, combo_target in rules["combos"].get("warning", set()):
        if combo_action == action and (combo_target == object_type or combo_target in subaction):
            return "warning"

    # Check broad action patterns
    if action in rules["actions"].get("critical", set()):
        return "critical"
    if action in rules["actions"].get("warning", set()):
        return "warning"

    # Default: anything with "edit" or "update" that didn't match a specific combo
    if action in ("edit", "update"):
        return "info"

    return "info"


def is_risky_event(event: dict) -> bool:
    """Return True if the event is critical or warning tier."""
    return classify_risk(event) in ("critical", "warning")
