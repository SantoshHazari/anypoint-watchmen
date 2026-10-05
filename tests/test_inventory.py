import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from inventory_normalizer import (
    flatten_api_assets,
    normalize_business_group,
    normalize_ch2_deployment,
    normalize_cloudhub_app,
    normalize_environment,
    normalize_hybrid_app,
)
from inventory_store import (
    ensure_inventory_schema,
    insert_api_instances,
    insert_applications,
    insert_business_groups,
    insert_changes,
    insert_environments,
    insert_inventory_snapshot,
    inventory_counts,
    latest_inventory,
    list_changes,
    previous_snapshot_data,
)


# -- Fixtures --

SAMPLE_ORG = {
    "id": "org-root",
    "name": "Root Org",
    "isMaster": True,
    "subOrganizationIds": ["bg-child-1"],
    "_bg_path": "Root Org",
    "owner": {"id": "user-001"},
}

SAMPLE_CHILD_BG = {
    "id": "bg-child-1",
    "name": "Sales BG",
    "isMaster": False,
    "subOrganizationIds": [],
    "_bg_path": "Root Org > Sales BG",
    "owner": {"id": "user-002"},
}

SAMPLE_ENV = {
    "id": "env-001",
    "name": "Production",
    "organizationId": "org-root",
    "type": "production",
    "isProduction": True,
    "clientId": "client-xyz",
}

SAMPLE_CH_APP = {
    "domain": "my-api-app",
    "fullDomain": "my-api-app.us-e2.cloudhub.io",
    "status": "STARTED",
    "workers": {"amount": 2, "type": {"name": "Micro", "cpu": "0.1 vCores", "memory": "500 MB"}},
    "muleVersion": {"version": "4.4.0"},
    "region": "us-east-2",
    "fileName": "my-api-app-1.0.0-mule-application.jar",
    "lastUpdateTime": 1715000000000,
}

SAMPLE_HYBRID_APP = {
    "data": {
        "id": 12345,
        "name": "on-prem-app",
        "lastReportedStatus": "RUNNING",
        "artifact": {"name": "on-prem-app", "fileName": "on-prem-app.zip", "muleVersion": "4.3.0"},
        "target": {"targetId": "srv-01", "name": "prod-server-1"},
        "lastModified": "2026-05-14T10:00:00Z",
    }
}

SAMPLE_CH2_DEPLOYMENT = {
    "id": "00000000-0000-4000-8000-000000000001",
    "name": "nettools-dummy-001",
    "creationDate": 1778887931084,
    "lastModifiedDate": 1778888036612,
    "target": {
        "provider": "MC",
        "targetId": "cloudhub-us-east-2",
        "deploymentSettings": {
            "instanceType": "mule.nano",
            "http": {"inbound": {"publicUrl": "https://nettools-dummy-001.usa-e2.cloudhub.io"}},
            "runtime": {"version": "4.11.4:4e-java17", "releaseChannel": "EDGE", "java": "17"},
        },
        "replicas": 1,
    },
    "status": "APPLIED",
    "application": {
        "status": "RUNNING",
        "desiredState": "STARTED",
        "ref": {"groupId": "org-root", "artifactId": "net-tools-api", "version": "1.0.0", "packaging": "jar"},
        "vCores": 0.05,
    },
    "currentRuntimeVersion": "4.11.4:4e-java17",
}

SAMPLE_API_ASSETS = [
    {
        "groupId": "org-root",
        "assetId": "customer-api",
        "apis": [
            {
                "id": 1001,
                "instanceLabel": "v1-prod",
                "assetVersion": "1.2.0",
                "technology": "mule4",
                "endpoint": {"uri": "http://impl:8081", "proxyUri": "https://proxy.io/v1", "isCloudHub": True},
                "autodiscoveryInstanceName": "v1:1001",
            },
            {
                "id": 1002,
                "instanceLabel": "v2-prod",
                "assetVersion": "2.0.0",
                "technology": "mule4",
                "endpoint": {"uri": "http://impl:8082", "proxyUri": "https://proxy.io/v2", "isCloudHub": False},
                "autodiscoveryInstanceName": "v2:1002",
            },
        ],
    }
]


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    ensure_inventory_schema(c)
    return c


# -- Normalizer tests --


class TestNormalization:
    def test_normalize_business_group(self):
        result = normalize_business_group(SAMPLE_ORG)
        assert result["bg_id"] == "org-root"
        assert result["name"] == "Root Org"
        assert result["is_master"] == 1
        assert result["bg_path"] == "Root Org"

    def test_normalize_child_business_group(self):
        result = normalize_business_group(SAMPLE_CHILD_BG)
        assert result["bg_id"] == "bg-child-1"
        assert result["bg_path"] == "Root Org > Sales BG"
        assert result["is_master"] == 0

    def test_normalize_environment_with_bg(self):
        result = normalize_environment(SAMPLE_ENV, bg_id="org-root", bg_name="Root Org")
        assert result["env_id"] == "env-001"
        assert result["name"] == "Production"
        assert result["bg_id"] == "org-root"
        assert result["bg_name"] == "Root Org"

    def test_normalize_environment_without_bg(self):
        result = normalize_environment(SAMPLE_ENV)
        assert result["bg_id"] == ""
        assert result["bg_name"] == ""

    def test_normalize_cloudhub_app_with_bg(self):
        result = normalize_cloudhub_app(SAMPLE_CH_APP, "env-001", bg_id="bg-child-1", bg_name="Sales BG")
        assert result["source"] == "cloudhub"
        assert result["domain"] == "my-api-app"
        assert result["bg_id"] == "bg-child-1"
        assert result["bg_name"] == "Sales BG"
        assert result["worker_count"] == 2

    def test_normalize_cloudhub_app_scalar_workers(self):
        raw = {**SAMPLE_CH_APP, "workers": 1, "workerType": {"name": "Micro"}}
        result = normalize_cloudhub_app(raw, "env-001")
        assert result["worker_count"] == 1
        assert result["worker_size"] == "Micro"

    def test_normalize_hybrid_app_with_bg(self):
        result = normalize_hybrid_app(SAMPLE_HYBRID_APP, "env-002", bg_id="org-root", bg_name="Root Org")
        assert result["source"] == "hybrid"
        assert result["bg_id"] == "org-root"
        assert result["bg_name"] == "Root Org"

    def test_normalize_ch2_deployment(self):
        result = normalize_ch2_deployment(SAMPLE_CH2_DEPLOYMENT, "env-001", bg_id="org-root", bg_name="Root Org")
        assert result["source"] == "ch2"
        assert result["domain"] == "nettools-dummy-001"
        assert result["status"] == "RUNNING"
        assert result["worker_count"] == 1
        assert result["worker_size"] == "mule.nano"
        assert result["mule_version"] == "4.11.4:4e-java17"
        assert result["region"] == "cloudhub-us-east-2"
        assert result["vcores"] == 0.05
        assert result["provider"] == "MC"
        assert result["bg_id"] == "org-root"
        assert result["bg_name"] == "Root Org"
        assert "nettools-dummy-001" in result["full_domain"]

    def test_flatten_api_assets_with_bg(self):
        result = flatten_api_assets(SAMPLE_API_ASSETS, "env-001", bg_id="org-root", bg_name="Root Org")
        assert len(result) == 2
        assert result[0]["bg_id"] == "org-root"
        assert result[0]["bg_name"] == "Root Org"
        assert result[1]["bg_name"] == "Root Org"


# -- Store tests --


class TestInventoryStore:
    def test_insert_snapshot_and_business_groups(self, conn):
        sid = insert_inventory_snapshot(conn, run_id="inv-bg-01", collected_at_utc="2026-05-15T12:00:00", org_id="org-root", org_name="Root Org")
        bgs = [normalize_business_group(SAMPLE_ORG), normalize_business_group(SAMPLE_CHILD_BG)]
        count = insert_business_groups(conn, sid, bgs)
        assert count == 2

    def test_insert_envs_with_bg(self, conn):
        sid = insert_inventory_snapshot(conn, run_id="inv-bg-02", collected_at_utc="2026-05-15T12:00:00", org_id="org-root")
        envs = [normalize_environment(SAMPLE_ENV, bg_id="org-root", bg_name="Root Org")]
        count = insert_environments(conn, sid, envs)
        assert count == 1

    def test_insert_applications_with_bg(self, conn):
        sid = insert_inventory_snapshot(conn, run_id="inv-bg-03", collected_at_utc="2026-05-15T12:00:00", org_id="org-root")
        apps = [
            normalize_cloudhub_app(SAMPLE_CH_APP, "env-001", bg_id="org-root", bg_name="Root Org"),
            normalize_hybrid_app(SAMPLE_HYBRID_APP, "env-002", bg_id="bg-child-1", bg_name="Sales BG"),
        ]
        count = insert_applications(conn, sid, apps)
        assert count == 2

    def test_insert_api_instances_with_bg(self, conn):
        sid = insert_inventory_snapshot(conn, run_id="inv-bg-04", collected_at_utc="2026-05-15T12:00:00", org_id="org-root")
        instances = flatten_api_assets(SAMPLE_API_ASSETS, "env-001", bg_id="org-root", bg_name="Root Org")
        count = insert_api_instances(conn, sid, instances)
        assert count == 2

    def test_insert_ch2_applications(self, conn):
        sid = insert_inventory_snapshot(conn, run_id="inv-ch2-01", collected_at_utc="2026-05-15T12:00:00", org_id="org-root")
        apps = [normalize_ch2_deployment(SAMPLE_CH2_DEPLOYMENT, "env-001", bg_id="org-root", bg_name="Root Org")]
        count = insert_applications(conn, sid, apps)
        assert count == 1
        inv = latest_inventory(conn)
        app = inv["applications"][0]
        assert app["source"] == "ch2"
        assert app["domain"] == "nettools-dummy-001"
        assert app["worker_size"] == "mule.nano"

    def test_dedup_on_insert(self, conn):
        sid = insert_inventory_snapshot(conn, run_id="inv-bg-05", collected_at_utc="2026-05-15T12:00:00", org_id="org-root")
        apps = [normalize_cloudhub_app(SAMPLE_CH_APP, "env-001", bg_id="org-root", bg_name="Root Org")]
        assert insert_applications(conn, sid, apps) == 1
        assert insert_applications(conn, sid, apps) == 0

    def test_latest_inventory_includes_bgs(self, conn):
        sid = insert_inventory_snapshot(conn, run_id="inv-bg-06", collected_at_utc="2026-05-15T12:00:00", org_id="org-root", org_name="Root Org")
        insert_business_groups(conn, sid, [normalize_business_group(SAMPLE_ORG), normalize_business_group(SAMPLE_CHILD_BG)])
        insert_environments(conn, sid, [normalize_environment(SAMPLE_ENV, bg_id="org-root", bg_name="Root Org")])
        insert_applications(conn, sid, [normalize_cloudhub_app(SAMPLE_CH_APP, "env-001", bg_id="org-root", bg_name="Root Org")])
        insert_api_instances(conn, sid, flatten_api_assets(SAMPLE_API_ASSETS, "env-001", bg_id="org-root", bg_name="Root Org"))

        inv = latest_inventory(conn)
        assert inv["snapshot"]["run_id"] == "inv-bg-06"
        assert len(inv["business_groups"]) == 2
        assert len(inv["environments"]) == 1
        assert len(inv["applications"]) == 1
        assert len(inv["api_instances"]) == 2
        # Verify BG context flows through
        assert inv["environments"][0]["bg_name"] == "Root Org"
        assert inv["applications"][0]["bg_name"] == "Root Org"
        assert inv["api_instances"][0]["bg_name"] == "Root Org"

    def test_inventory_counts_includes_bgs(self, conn):
        sid = insert_inventory_snapshot(conn, run_id="inv-bg-07", collected_at_utc="2026-05-15T12:00:00", org_id="org-root")
        insert_business_groups(conn, sid, [normalize_business_group(SAMPLE_ORG), normalize_business_group(SAMPLE_CHILD_BG)])
        insert_environments(conn, sid, [normalize_environment(SAMPLE_ENV, bg_id="org-root", bg_name="Root Org")])

        counts = inventory_counts(conn)
        assert counts["business_groups"] == 2
        assert counts["environments"] == 1


# -- Diff tests --


class TestDiff:
    def test_detect_added_app(self, conn):
        sid1 = insert_inventory_snapshot(conn, run_id="inv-diff-01", collected_at_utc="2026-05-15T10:00:00", org_id="org-root")
        conn.commit()

        sid2 = insert_inventory_snapshot(conn, run_id="inv-diff-02", collected_at_utc="2026-05-15T11:00:00", org_id="org-root")
        apps = [normalize_cloudhub_app(SAMPLE_CH_APP, "env-001", bg_id="org-root", bg_name="Root Org")]
        insert_applications(conn, sid2, apps)

        prev = previous_snapshot_data(conn, sid2)
        assert len(prev["applications"]) == 0

        from inventory_services import _diff_entities, APP_COMPARE_FIELDS
        new_map = {f"{a['source']}:{a['env_id']}:{a['domain']}": a for a in apps}
        changes = _diff_entities(prev["applications"], new_map, "application", APP_COMPARE_FIELDS)
        assert len(changes) == 1
        assert changes[0]["change_type"] == "added"

    def test_detect_removed_app(self, conn):
        sid1 = insert_inventory_snapshot(conn, run_id="inv-diff-03", collected_at_utc="2026-05-15T10:00:00", org_id="org-root")
        apps = [normalize_cloudhub_app(SAMPLE_CH_APP, "env-001", bg_id="org-root", bg_name="Root Org")]
        insert_applications(conn, sid1, apps)
        conn.commit()

        sid2 = insert_inventory_snapshot(conn, run_id="inv-diff-04", collected_at_utc="2026-05-15T11:00:00", org_id="org-root")

        prev = previous_snapshot_data(conn, sid2)
        assert len(prev["applications"]) == 1

        from inventory_services import _diff_entities, APP_COMPARE_FIELDS
        changes = _diff_entities(prev["applications"], {}, "application", APP_COMPARE_FIELDS)
        assert len(changes) == 1
        assert changes[0]["change_type"] == "removed"

    def test_detect_changed_app(self, conn):
        sid1 = insert_inventory_snapshot(conn, run_id="inv-diff-05", collected_at_utc="2026-05-15T10:00:00", org_id="org-root")
        apps = [normalize_cloudhub_app(SAMPLE_CH_APP, "env-001", bg_id="org-root", bg_name="Root Org")]
        insert_applications(conn, sid1, apps)
        conn.commit()

        modified = dict(SAMPLE_CH_APP)
        modified["status"] = "DEPLOYING"
        sid2 = insert_inventory_snapshot(conn, run_id="inv-diff-06", collected_at_utc="2026-05-15T11:00:00", org_id="org-root")
        new_apps = [normalize_cloudhub_app(modified, "env-001", bg_id="org-root", bg_name="Root Org")]
        insert_applications(conn, sid2, new_apps)

        prev = previous_snapshot_data(conn, sid2)
        from inventory_services import _diff_entities, APP_COMPARE_FIELDS
        new_map = {f"{a['source']}:{a['env_id']}:{a['domain']}": a for a in new_apps}
        changes = _diff_entities(prev["applications"], new_map, "application", APP_COMPARE_FIELDS)
        assert len(changes) == 1
        assert changes[0]["change_type"] == "changed"
        assert changes[0]["new"]["status"] == "DEPLOYING"

    def test_insert_and_list_changes(self, conn):
        sid = insert_inventory_snapshot(conn, run_id="inv-diff-07", collected_at_utc="2026-05-15T12:00:00", org_id="org-root")
        changes = [
            {"change_type": "added", "entity_type": "application", "entity_key": "cloudhub:env-001:my-app",
             "env_id": "env-001", "old": None, "new": {"domain": "my-app"}},
        ]
        insert_changes(conn, sid, changes)
        conn.commit()
        result = list_changes(conn, limit=10)
        assert len(result) == 1
        assert result[0]["change_type"] == "added"
        assert result[0]["new"]["domain"] == "my-app"
