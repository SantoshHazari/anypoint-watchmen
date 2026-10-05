import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from audit_normalizer import classify_risk, extract_audit_events, invalidate_rules_cache, is_risky_event, normalize_audit_event
from audit_store import audit_counts, ensure_audit_schema, insert_audit_events, insert_audit_poll_run, list_audit_events


def test_extract_audit_events_from_data_wrapper():
    response = {"data": [{"id": "evt-1"}]}
    assert extract_audit_events(response) == [{"id": "evt-1"}]


def test_normalize_audit_event_common_shape():
    raw = {
        "id": "evt-1",
        "time": "2026-05-15T10:00:00Z",
        "product": "Runtime Manager",
        "objectType": "Application",
        "action": "Deploy",
        "object": {"id": "app-1", "name": "demo-app"},
        "user": {"id": "user-1", "email": "admin@example.com"},
        "environment": {"id": "env-1", "name": "demo-nonprod"},
    }
    event = normalize_audit_event(raw)
    assert event["event_key"] == "evt-1"
    assert event["product"] == "Runtime Manager"
    assert event["object_type"] == "Application"
    assert event["action"] == "Deploy"
    assert event["object_name"] == "demo-app"
    assert event["user_name"] == "admin@example.com"
    assert event["env_name"] == "demo-nonprod"
    assert not is_risky_event(event)  # deploy+Application is info under current rules


def test_audit_store_insert_and_counts():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    ensure_audit_schema(conn)
    poll_id = insert_audit_poll_run(
        conn,
        run_id="audit-test-1",
        collected_at_utc="2026-05-15T10:00:00Z",
        org_id="org-1",
        org_name="Test",
        window_start_utc="2026-05-15T09:00:00Z",
        window_end_utc="2026-05-15T10:00:00Z",
    )
    events = [
        {
            "event_key": "evt-1",
            "event_time_utc": "2026-05-15T09:30:00Z",
            "product": "Runtime Manager",
            "object_type": "Application",
            "action": "Deploy",
            "object_id": "app-1",
            "object_name": "demo-app",
            "user_id": "user-1",
            "user_name": "admin@example.com",
            "env_id": "env-1",
            "env_name": "demo",
            "connected_app": "",
            "risky": True,
            "raw": {"id": "evt-1"},
        }
    ]
    inserted, risky = insert_audit_events(conn, poll_id, events)
    assert inserted == 1
    assert risky == 1
    assert insert_audit_events(conn, poll_id, events)[0] == 0
    listed = list_audit_events(conn)
    assert len(listed) == 1
    counts = audit_counts(conn)
    assert counts["total"] == 1
    assert counts["risky"] == 1


def test_classify_risk_defaults():
    """Verify hardcoded defaults work when no config file."""
    invalidate_rules_cache()
    assert classify_risk({"action": "delete", "object_type": "app"}) == "critical"
    assert classify_risk({"action": "deploy", "object_type": "app"}) == "info"
    assert classify_risk({"action": "deploy", "object_type": "api (in environment)"}) == "warning"
    assert classify_risk({"action": "login", "object_type": "user"}) == "info"
    assert classify_risk({"action": "read", "object_type": "data"}) == "info"


def test_classify_risk_combo():
    """Combos override broad action rules."""
    invalidate_rules_cache()
    # "create" is normally warning, but "create organization" is critical combo
    assert classify_risk({"action": "create", "object_type": "organization"}) == "critical"
    # "edit" is normally info, but "edit entitlement" is critical combo
    assert classify_risk({"action": "edit", "object_type": "entitlement"}) == "critical"


def test_classify_risk_with_config(tmp_path):
    """classify_risk respects config file rules."""
    import json
    from unittest.mock import patch

    custom_rules = {
        "actions": {
            "critical": ["nuke"],
            "warning": ["poke"],
            "info": ["nap"],
        },
        "combos": {
            "critical": [{"action": "poke", "target": "reactor"}],
            "warning": [],
        },
    }
    config_path = tmp_path / "audit_rules.json"
    config_path.write_text(json.dumps(custom_rules), encoding="utf-8")

    invalidate_rules_cache()
    with patch("watchmen_config.load_audit_rules", return_value=custom_rules):
        assert classify_risk({"action": "nuke", "object_type": "x"}) == "critical"
        assert classify_risk({"action": "poke", "object_type": "reactor"}) == "critical"  # combo
        assert classify_risk({"action": "poke", "object_type": "bear"}) == "warning"
        assert classify_risk({"action": "nap", "object_type": "x"}) == "info"
        assert classify_risk({"action": "unknown", "object_type": "x"}) == "info"

    invalidate_rules_cache()  # clean up
