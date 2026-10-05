import pytest
from unittest.mock import patch
import sys
from pathlib import Path

# Ensure the web directory is in the path to import app
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "web"))

from app import app


@pytest.fixture
def client():
    app.config["TESTING"] = True
    with app.test_client() as client:
        yield client


@patch("app.load_entitlements")
@patch("app.load_settings")
def test_settings_page_get(mock_load_settings, mock_load_entitlements, client):
    mock_load_entitlements.return_value = {"policy": {}, "limits": []}
    mock_load_settings.return_value = {}
    
    response = client.get("/settings")
    assert response.status_code == 200
    assert b"Policy Configuration" in response.data


@patch("app.load_entitlements")
@patch("app.save_entitlements")
def test_settings_update_policy(mock_save_entitlements, mock_load_entitlements, client):
    mock_load_entitlements.return_value = {"policy": {}}
    
    response = client.post(
        "/settings/policy",
        data={
            "operating_mode": "standard",
            "notes": "Test notes",
            "default_thresholds_percent": "50, 60, 70"
        }
    )
    
    # Should redirect back to settings
    assert response.status_code == 302
    assert response.headers["Location"] == "/settings"
    
    # Verify save was called with the correct parsed integers
    saved_data = mock_save_entitlements.call_args[0][0]
    assert saved_data["policy"]["operating_mode"] == "standard"
    assert saved_data["policy"]["default_thresholds_percent"] == [50, 60, 70]


@patch("app.load_entitlements")
@patch("app.save_entitlements")
def test_settings_update_limit(mock_save_entitlements, mock_load_entitlements, client):
    mock_load_entitlements.return_value = {
        "limits": [{"key": "mule_flows", "limit": 200, "status": "provisional_conservative"}]
    }
    
    response = client.post(
        "/settings/limit/mule_flows",
        data={"limit": "250", "status": "active"}
    )
    
    assert response.status_code == 302
    
    saved_data = mock_save_entitlements.call_args[0][0]
    assert saved_data["limits"][0]["limit"] == 250  # int when whole number
    assert saved_data["limits"][0]["status"] == "active"


@patch("app.load_settings")
@patch("app.save_settings")
def test_settings_update_system(mock_save_settings, mock_load_settings, client):
    mock_load_settings.return_value = {"contract": {}, "alerts": {}}

    response = client.post(
        "/settings/system",
        data={
            "start_date": "2026-05-15",
            "end_date": "2027-05-15",
            "email_to": "admin1@example.com, admin2@example.com"
        }
    )
    assert response.status_code == 302
    saved_data = mock_save_settings.call_args[0][0]
    assert saved_data["contract"]["start_date"] == "2026-05-15"
    assert saved_data["alerts"]["email"]["to"] == ["admin1@example.com", "admin2@example.com"]


@patch("app.load_settings")
@patch("app.save_settings")
def test_settings_update_scheduler(mock_save_settings, mock_load_settings, client):
    mock_load_settings.return_value = {"scheduler": {"interval_value": 12, "interval_unit": "hours"}}

    response = client.post(
        "/settings/scheduler",
        data={"interval_value": "30", "interval_unit": "minutes"}
    )
    assert response.status_code == 302
    assert response.headers["Location"] == "/settings"

    saved = mock_save_settings.call_args[0][0]
    assert saved["scheduler"]["interval_value"] == 30
    assert saved["scheduler"]["interval_unit"] == "minutes"


@patch("app.load_settings")
@patch("app.save_settings")
def test_settings_scheduler_min_5(mock_save_settings, mock_load_settings, client):
    """Interval value below 5 is clamped to 5."""
    mock_load_settings.return_value = {"scheduler": {}}

    response = client.post(
        "/settings/scheduler",
        data={"interval_value": "2", "interval_unit": "minutes"}
    )
    assert response.status_code == 302
    saved = mock_save_settings.call_args[0][0]
    assert saved["scheduler"]["interval_value"] == 5


@patch("app.load_settings")
@patch("app.save_settings")
def test_settings_scheduler_invalid_unit(mock_save_settings, mock_load_settings, client):
    """Invalid unit returns 400."""
    mock_load_settings.return_value = {"scheduler": {}}

    response = client.post(
        "/settings/scheduler",
        data={"interval_value": "10", "interval_unit": "weeks"}
    )
    assert response.status_code == 400


@patch("app.load_audit_rules")
@patch("app.save_audit_rules")
def test_audit_rules_add_action(mock_save, mock_load, client):
    mock_load.return_value = {
        "actions": {"critical": ["delete"], "warning": [], "info": []},
        "combos": {"critical": [], "warning": []},
    }

    response = client.post(
        "/settings/audit-rules/add",
        data={"tier": "critical", "rule_type": "action", "action": "purge"}
    )
    assert response.status_code == 302

    saved = mock_save.call_args[0][0]
    assert "purge" in saved["actions"]["critical"]


@patch("app.load_audit_rules")
@patch("app.save_audit_rules")
def test_audit_rules_add_combo(mock_save, mock_load, client):
    mock_load.return_value = {
        "actions": {"critical": [], "warning": [], "info": []},
        "combos": {"critical": [], "warning": []},
    }

    response = client.post(
        "/settings/audit-rules/add",
        data={"tier": "warning", "rule_type": "combo", "action": "edit", "target": "policy"}
    )
    assert response.status_code == 302

    saved = mock_save.call_args[0][0]
    assert {"action": "edit", "target": "policy"} in saved["combos"]["warning"]


@patch("app.load_audit_rules")
@patch("app.save_audit_rules")
def test_audit_rules_remove_action(mock_save, mock_load, client):
    mock_load.return_value = {
        "actions": {"critical": ["delete", "revoke"], "warning": [], "info": []},
        "combos": {"critical": [], "warning": []},
    }

    response = client.post(
        "/settings/audit-rules/remove",
        data={"tier": "critical", "rule_type": "action", "action": "revoke"}
    )
    assert response.status_code == 302

    saved = mock_save.call_args[0][0]
    assert "revoke" not in saved["actions"]["critical"]
    assert "delete" in saved["actions"]["critical"]


@patch("app.load_audit_rules")
@patch("app.save_audit_rules")
def test_audit_rules_remove_combo(mock_save, mock_load, client):
    mock_load.return_value = {
        "actions": {"critical": [], "warning": [], "info": []},
        "combos": {"critical": [{"action": "edit", "target": "entitlement"}, {"action": "delete", "target": "org"}], "warning": []},
    }

    response = client.post(
        "/settings/audit-rules/remove",
        data={"tier": "critical", "rule_type": "combo", "action": "edit", "target": "entitlement"}
    )
    assert response.status_code == 302

    saved = mock_save.call_args[0][0]
    assert {"action": "edit", "target": "entitlement"} not in saved["combos"]["critical"]
    assert {"action": "delete", "target": "org"} in saved["combos"]["critical"]
