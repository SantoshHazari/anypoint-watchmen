"""Normalize raw Anypoint API responses into flat dicts for SQLite storage."""

from __future__ import annotations


def _get(d: dict, *keys, default=None):
    for k in keys:
        if k in d:
            return d[k]
    return default


def normalize_business_group(org_raw: dict) -> dict:
    owner = org_raw.get("owner", {}) or {}
    return {
        "bg_id": org_raw.get("id", ""),
        "name": org_raw.get("name", ""),
        "parent_bg_id": org_raw.get("parentOrganizationIds", [""])[0] if org_raw.get("parentOrganizationIds") else "",
        "bg_path": org_raw.get("_bg_path", org_raw.get("name", "")),
        "is_master": 1 if org_raw.get("isMaster") else 0,
        "owner_id": owner.get("id", ""),
    }


def normalize_environment(raw: dict, bg_id: str = "", bg_name: str = "") -> dict:
    return {
        "env_id": raw["id"],
        "name": raw.get("name", ""),
        "org_id": raw.get("organizationId", ""),
        "env_type": raw.get("type", ""),
        "is_production": 1 if raw.get("isProduction") else 0,
        "client_id": raw.get("clientId", ""),
        "bg_id": bg_id,
        "bg_name": bg_name,
    }


def normalize_cloudhub_app(raw: dict, env_id: str, bg_id: str = "", bg_name: str = "") -> dict:
    workers = raw.get("workers", {})
    # Some CloudHub 1.0 responses return workers as a bare count with a separate workerType
    if not isinstance(workers, dict):
        workers = {"amount": workers or 0, "type": raw.get("workerType", {})}
    worker_type = workers.get("type", {})
    if not isinstance(worker_type, dict):
        worker_type = {"name": str(worker_type)}
    return {
        "source": "cloudhub",
        "env_id": env_id,
        "domain": raw.get("domain", ""),
        "full_domain": raw.get("fullDomain", ""),
        "status": raw.get("status", ""),
        "worker_count": workers.get("amount", 0),
        "worker_size": worker_type.get("name", ""),
        "worker_cpu": worker_type.get("cpu", ""),
        "worker_memory": worker_type.get("memory", ""),
        "mule_version": _get(raw, "muleVersion", default={}).get("version", "") if isinstance(_get(raw, "muleVersion", default={}), dict) else str(_get(raw, "muleVersion", default="")),
        "region": raw.get("region", ""),
        "file_name": raw.get("fileName", ""),
        "last_update_time": str(raw.get("lastUpdateTime", "")),
        "runtime_version": _get(raw, "muleVersion", default={}).get("version", "") if isinstance(_get(raw, "muleVersion", default={}), dict) else "",
        "bg_id": bg_id,
        "bg_name": bg_name,
    }


def normalize_ch2_deployment(raw: dict, env_id: str, bg_id: str = "", bg_name: str = "") -> dict:
    """Normalize a CloudHub 2.0 / Runtime Fabric deployment from Runtime Manager v2."""
    target = raw.get("target", {})
    deploy_settings = target.get("deploymentSettings", {})
    app = raw.get("application", {})
    ref = app.get("ref", {})
    runtime = deploy_settings.get("runtime", {})
    http_inbound = deploy_settings.get("http", {}).get("inbound", {})
    return {
        "source": "ch2",
        "env_id": env_id,
        "deployment_id": raw.get("id", ""),
        "domain": raw.get("name", ""),
        "full_domain": http_inbound.get("publicUrl", ""),
        "status": app.get("status", raw.get("status", "")),
        "worker_count": target.get("replicas", 0),
        "worker_size": deploy_settings.get("instanceType", ""),
        "worker_cpu": "",
        "worker_memory": "",
        "mule_version": runtime.get("version", raw.get("currentRuntimeVersion", "")),
        "region": target.get("targetId", ""),
        "file_name": f"{ref.get('artifactId', '')}-{ref.get('version', '')}.{ref.get('packaging', 'jar')}",
        "last_update_time": str(raw.get("lastModifiedDate", "")),
        "runtime_version": runtime.get("version", raw.get("currentRuntimeVersion", "")),
        "vcores": app.get("vCores", 0),
        "provider": target.get("provider", ""),
        "desired_state": app.get("desiredState", ""),
        "bg_id": bg_id,
        "bg_name": bg_name,
    }


def normalize_hybrid_app(raw: dict, env_id: str, bg_id: str = "", bg_name: str = "") -> dict:
    data = raw.get("data", raw) if isinstance(raw, dict) else raw
    artifact = data.get("artifact", {})
    target = data.get("target", {})
    return {
        "source": "hybrid",
        "env_id": env_id,
        "domain": artifact.get("name", data.get("name", str(data.get("id", "")))),
        "full_domain": "",
        "status": data.get("lastReportedStatus", data.get("desiredStatus", "")),
        "worker_count": 0,
        "worker_size": "",
        "worker_cpu": "",
        "worker_memory": "",
        "mule_version": artifact.get("muleVersion", ""),
        "region": "",
        "file_name": artifact.get("fileName", ""),
        "last_update_time": str(data.get("lastModified", data.get("updatedAt", ""))),
        "runtime_version": artifact.get("muleVersion", ""),
        "server_id": str(target.get("targetId", target.get("serverId", ""))),
        "server_name": target.get("name", ""),
        "bg_id": bg_id,
        "bg_name": bg_name,
    }


def normalize_api_instance(raw: dict, env_id: str, bg_id: str = "", bg_name: str = "") -> dict:
    endpoint = raw.get("endpoint", {}) or {}
    return {
        "env_id": env_id,
        "api_id": raw.get("id", ""),
        "instance_label": raw.get("instanceLabel", ""),
        "group_id": raw.get("groupId", ""),
        "asset_id": raw.get("assetId", ""),
        "asset_version": raw.get("assetVersion", ""),
        "technology": raw.get("technology", ""),
        "endpoint_uri": endpoint.get("uri", ""),
        "proxy_uri": endpoint.get("proxyUri", ""),
        "is_cloudhub": 1 if endpoint.get("isCloudHub") else 0,
        "autodiscovery_name": raw.get("autodiscoveryInstanceName", ""),
        "bg_id": bg_id,
        "bg_name": bg_name,
    }


def flatten_api_assets(assets_response: list[dict], env_id: str, bg_id: str = "", bg_name: str = "") -> list[dict]:
    """API Manager returns assets with nested api instances. Flatten to individual instances."""
    instances = []
    for asset in assets_response:
        apis = asset.get("apis", [])
        for api in apis:
            merged = {**asset, **api}
            merged.pop("apis", None)
            instances.append(normalize_api_instance(merged, env_id, bg_id, bg_name))
    return instances
