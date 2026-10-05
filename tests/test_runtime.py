"""Tests for runtime_services — start/stop app orchestration."""

import sys
from pathlib import Path
from unittest.mock import MagicMock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from runtime_services import start_app, stop_app, stop_all_apps_in_env, _is_stopped


# -- Unit helpers --

def test_is_stopped_recognises_stopped_states():
    assert _is_stopped("STOPPED") is True
    assert _is_stopped("UNDEPLOYED") is True
    assert _is_stopped("NOT_RUNNING") is True
    assert _is_stopped("DELETED") is True
    assert _is_stopped("", "STOPPED") is True


def test_is_stopped_recognises_running_states():
    assert _is_stopped("RUNNING") is False
    assert _is_stopped("STARTED") is False
    assert _is_stopped("DEPLOYING") is False
    assert _is_stopped("", "") is False


# -- start_app / stop_app --

def test_start_ch2_calls_correct_method():
    client = MagicMock()
    client.start_ch2_deployment.return_value = {"status": "APPLYING"}
    result = start_app(
        client,
        source="ch2",
        org_id="org1",
        env_id="env1",
        domain="my-app",
        deployment_id="dep-123",
    )
    assert result["ok"] is True
    assert result["action"] == "start"
    assert result["source"] == "ch2"
    assert result["domain"] == "my-app"
    client.start_ch2_deployment.assert_called_once_with("org1", "env1", "dep-123")


def test_stop_ch2_calls_correct_method():
    client = MagicMock()
    client.stop_ch2_deployment.return_value = {"status": "APPLYING"}
    result = stop_app(
        client,
        source="ch2",
        org_id="org1",
        env_id="env1",
        domain="my-app",
        deployment_id="dep-123",
    )
    assert result["ok"] is True
    assert result["action"] == "stop"
    client.stop_ch2_deployment.assert_called_once_with("org1", "env1", "dep-123")


def test_start_cloudhub_calls_correct_method():
    client = MagicMock()
    client.start_cloudhub_app.return_value = {}
    result = start_app(
        client,
        source="cloudhub",
        org_id="org1",
        env_id="env1",
        domain="my-ch1-app",
    )
    assert result["ok"] is True
    client.start_cloudhub_app.assert_called_once_with("org1", "env1", "my-ch1-app")


def test_stop_cloudhub_calls_correct_method():
    client = MagicMock()
    client.stop_cloudhub_app.return_value = {}
    result = stop_app(
        client,
        source="cloudhub",
        org_id="org1",
        env_id="env1",
        domain="my-ch1-app",
    )
    assert result["ok"] is True
    client.stop_cloudhub_app.assert_called_once_with("org1", "env1", "my-ch1-app")


def test_ch2_without_deployment_id_fails():
    client = MagicMock()
    result = start_app(
        client,
        source="ch2",
        org_id="org1",
        env_id="env1",
        domain="my-app",
        deployment_id="",
    )
    assert result["ok"] is False
    assert "deployment_id required" in result["detail"]


def test_unsupported_source_fails():
    client = MagicMock()
    result = stop_app(
        client,
        source="hybrid",
        org_id="org1",
        env_id="env1",
        domain="my-app",
    )
    assert result["ok"] is False
    assert "Unsupported source" in result["detail"]


def test_api_error_returns_failure():
    client = MagicMock()
    client.stop_cloudhub_app.side_effect = RuntimeError("HTTP 500: internal error")
    result = stop_app(
        client,
        source="cloudhub",
        org_id="org1",
        env_id="env1",
        domain="my-app",
    )
    assert result["ok"] is False
    assert "HTTP 500" in result["detail"]


# -- stop_all_apps_in_env --

def test_stop_all_skips_already_stopped():
    client = MagicMock()
    client.list_ch2_deployments.return_value = [
        {"id": "d1", "name": "app1", "application": {"status": "RUNNING", "desiredState": "STARTED"}},
        {"id": "d2", "name": "app2", "application": {"status": "NOT_RUNNING", "desiredState": "STOPPED"}},
    ]
    client.list_cloudhub_apps.return_value = []
    client.stop_ch2_deployment.return_value = {"status": "APPLYING"}

    result = stop_all_apps_in_env(client, org_id="org1", env_id="env1")
    assert result["ok"] is True
    assert result["stopped"] == 1
    assert result["skipped"] == 1
    assert result["failed"] == 0
    assert result["total"] == 2
    client.stop_ch2_deployment.assert_called_once_with("org1", "env1", "d1")


def test_stop_all_mixed_ch1_and_ch2():
    client = MagicMock()
    client.list_ch2_deployments.return_value = [
        {"id": "d1", "name": "ch2-app", "application": {"status": "RUNNING", "desiredState": "STARTED"}},
    ]
    client.list_cloudhub_apps.return_value = [
        {"domain": "ch1-app", "status": "STARTED"},
    ]
    client.stop_ch2_deployment.return_value = {}
    client.stop_cloudhub_app.return_value = {}

    result = stop_all_apps_in_env(client, org_id="org1", env_id="env1")
    assert result["ok"] is True
    assert result["stopped"] == 2
    assert result["skipped"] == 0
    client.stop_ch2_deployment.assert_called_once()
    client.stop_cloudhub_app.assert_called_once()


def test_stop_all_counts_failures():
    client = MagicMock()
    client.list_ch2_deployments.return_value = [
        {"id": "d1", "name": "app1", "application": {"status": "RUNNING", "desiredState": "STARTED"}},
    ]
    client.list_cloudhub_apps.return_value = []
    client.stop_ch2_deployment.side_effect = RuntimeError("timeout")

    result = stop_all_apps_in_env(client, org_id="org1", env_id="env1")
    assert result["ok"] is False
    assert result["failed"] == 1
    assert result["stopped"] == 0
