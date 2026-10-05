"""Orchestration for Anypoint Platform inventory collection."""

from __future__ import annotations

import datetime as dt
import json
import uuid

from anypoint_auth import load_auth
from anypoint_platform_api import AnypointPlatformClient
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
    previous_snapshot_data,
)
from usage_store import connect
from watchmen_config import load_settings, project_path
from watchmen_log import get_logger

log = get_logger("inventory")


def _diff_entities(
    old_map: dict[str, dict],
    new_map: dict[str, dict],
    entity_type: str,
    compare_fields: list[str],
) -> list[dict]:
    changes = []
    for key, new_item in new_map.items():
        if key not in old_map:
            changes.append({
                "change_type": "added",
                "entity_type": entity_type,
                "entity_key": key,
                "env_id": new_item.get("env_id", ""),
                "old": None,
                "new": new_item,
            })
        else:
            old_item = old_map[key]
            diffs = {f for f in compare_fields if str(new_item.get(f, "")) != str(old_item.get(f, ""))}
            if diffs:
                changes.append({
                    "change_type": "changed",
                    "entity_type": entity_type,
                    "entity_key": key,
                    "env_id": new_item.get("env_id", ""),
                    "old": {f: old_item.get(f) for f in diffs},
                    "new": {f: new_item.get(f) for f in diffs},
                })
    for key, old_item in old_map.items():
        if key not in new_map:
            changes.append({
                "change_type": "removed",
                "entity_type": entity_type,
                "entity_key": key,
                "env_id": old_item.get("env_id", ""),
                "old": old_item,
                "new": None,
            })
    return changes


APP_COMPARE_FIELDS = ["status", "worker_count", "worker_size", "mule_version", "runtime_version"]
API_COMPARE_FIELDS = ["asset_version", "technology", "endpoint_uri", "proxy_uri", "instance_label"]


def _collect_bg_inventory(
    client: AnypointPlatformClient,
    bg_id: str,
    bg_name: str,
    all_envs: list[dict],
    all_apps: list[dict],
    all_apis: list[dict],
    errors: list[dict],
) -> None:
    """Collect environments, apps, and API instances for a single business group."""
    try:
        envs_raw = client.list_environments(bg_id)
    except RuntimeError as exc:
        log.warning("  %s: environments error: %s", bg_name, exc)
        errors.append({"bg": bg_name, "source": "environments", "error": str(exc)})
        return

    envs = [normalize_environment(e, bg_id=bg_id, bg_name=bg_name) for e in envs_raw]
    all_envs.extend(envs)
    log.info("  %s: %d environments", bg_name, len(envs))

    for env in envs:
        eid = env["env_id"]
        ename = env["name"]
        label = f"{bg_name} / {ename}"

        # CloudHub 2.0 / Runtime Fabric deployments
        try:
            ch2_list = client.list_ch2_deployments(bg_id, eid)
            ch2_detailed = []
            for dep in ch2_list:
                dep_id = dep.get("id", "")
                try:
                    detail = client.get_ch2_deployment(bg_id, eid, dep_id)
                    ch2_detailed.append(detail)
                except RuntimeError:
                    ch2_detailed.append(dep)  # fall back to list data
            ch2_apps = [normalize_ch2_deployment(a, eid, bg_id=bg_id, bg_name=bg_name) for a in ch2_detailed]
            all_apps.extend(ch2_apps)
            if ch2_apps:
                log.info("    %s: %d CH2/RTF deployments", label, len(ch2_apps))
        except RuntimeError as exc:
            log.warning("    %s: CH2/RTF error: %s", label, exc)
            errors.append({"bg": bg_name, "env": ename, "source": "ch2", "error": str(exc)})

        # CloudHub 1.0 apps
        try:
            ch_raw = client.list_cloudhub_apps(bg_id, eid)
            ch_apps = [normalize_cloudhub_app(a, eid, bg_id=bg_id, bg_name=bg_name) for a in ch_raw]
            all_apps.extend(ch_apps)
            if ch_apps:
                log.info("    %s: %d CloudHub 1.0 apps", label, len(ch_apps))
        except RuntimeError as exc:
            log.warning("    %s: CloudHub 1.0 error: %s", label, exc)
            errors.append({"bg": bg_name, "env": ename, "source": "cloudhub", "error": str(exc)})

        # Hybrid apps
        try:
            hy_raw = client.list_hybrid_apps(bg_id, eid)
            hy_apps = [normalize_hybrid_app(a, eid, bg_id=bg_id, bg_name=bg_name) for a in hy_raw]
            all_apps.extend(hy_apps)
            if hy_apps:
                log.info("    %s: %d hybrid apps", label, len(hy_apps))
        except RuntimeError as exc:
            log.warning("    %s: hybrid error: %s", label, exc)
            errors.append({"bg": bg_name, "env": ename, "source": "hybrid", "error": str(exc)})

        # API Manager instances
        try:
            api_raw = client.list_api_instances(bg_id, eid)
            if isinstance(api_raw, list):
                api_instances = flatten_api_assets(api_raw, eid, bg_id=bg_id, bg_name=bg_name)
            else:
                api_instances = []
            all_apis.extend(api_instances)
            if api_instances:
                log.info("    %s: %d API instances", label, len(api_instances))
        except RuntimeError as exc:
            log.warning("    %s: API Manager error: %s", label, exc)
            errors.append({"bg": bg_name, "env": ename, "source": "api_manager", "error": str(exc)})


def collect_inventory(
    *,
    host: str | None = None,
    token_env: str = "ANYPOINT_TOKEN",
    settings_path: str = "config/settings.json",
) -> dict:
    auth = load_auth(host, token_env)
    settings = load_settings(settings_path)
    client = AnypointPlatformClient(auth)

    # Discover root org
    me = client.get_me()
    user_info = me.get("user", me)
    root_org_id = user_info.get("organizationId", "")
    root_org_name = ""
    member_of = user_info.get("memberOfOrganizations", [])
    for org in member_of:
        if org.get("id") == root_org_id:
            root_org_name = org.get("name", "")
            break

    collected_at = dt.datetime.now(dt.timezone.utc)
    run_id = f"inv-{collected_at.strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex[:8]}"

    # Walk full business group hierarchy
    log.info("Discovering business group hierarchy for %s (%s)", root_org_id, root_org_name)
    org_tree = client.walk_organization_tree(root_org_id)
    bg_records = [normalize_business_group(org) for org in org_tree]
    log.info("Found %d business groups (including root)", len(bg_records))

    all_envs: list[dict] = []
    all_apps: list[dict] = []
    all_apis: list[dict] = []
    errors: list[dict] = []

    # Collect inventory for each business group
    for org in org_tree:
        bg_id = org.get("id", "")
        bg_name = org.get("name", bg_id)
        log.info("Collecting inventory for BG: %s (%s)", bg_name, bg_id)
        _collect_bg_inventory(client, bg_id, bg_name, all_envs, all_apps, all_apis, errors)

    # Persist
    conn = connect(project_path(settings["storage"]["sqlite_path"]))
    try:
        ensure_inventory_schema(conn)
        snapshot_id = insert_inventory_snapshot(
            conn,
            run_id=run_id,
            collected_at_utc=collected_at.isoformat(),
            org_id=root_org_id,
            org_name=root_org_name,
        )
        bg_count = insert_business_groups(conn, snapshot_id, bg_records)
        env_count = insert_environments(conn, snapshot_id, all_envs)
        app_count = insert_applications(conn, snapshot_id, all_apps)
        api_count = insert_api_instances(conn, snapshot_id, all_apis)

        # Diff against previous snapshot
        prev = previous_snapshot_data(conn, snapshot_id)
        new_app_map = {f"{a['source']}:{a['env_id']}:{a['domain']}": a for a in all_apps}
        new_api_map = {f"{a['env_id']}:{a['api_id']}": a for a in all_apis}
        changes = _diff_entities(prev["applications"], new_app_map, "application", APP_COMPARE_FIELDS)
        changes += _diff_entities(prev["api_instances"], new_api_map, "api_instance", API_COMPARE_FIELDS)
        change_count = insert_changes(conn, snapshot_id, changes)

        conn.commit()
    finally:
        conn.close()

    summary = {
        "run_id": run_id,
        "org_id": root_org_id,
        "org_name": root_org_name,
        "business_groups": bg_count,
        "environments": env_count,
        "applications": app_count,
        "api_instances": api_count,
        "changes_detected": change_count,
        "errors": errors,
    }
    log.info(
        "Inventory %s: %d BGs, %d envs, %d apps, %d APIs, %d changes, %d errors",
        run_id, bg_count, env_count, app_count, api_count, change_count, len(errors),
    )
    return summary


if __name__ == "__main__":
    import pprint
    result = collect_inventory()
    pprint.pprint(result)
