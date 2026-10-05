"""Unified Anypoint Platform API client for non-usage endpoints."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any

from anypoint_auth import AnypointAuth, refresh_token, _can_login


class AnypointPlatformClient:
    def __init__(self, auth: AnypointAuth, timeout_seconds: int = 45) -> None:
        self.auth = auth
        self.timeout_seconds = timeout_seconds

    def _request(
        self,
        path: str,
        method: str = "GET",
        payload: dict | None = None,
        extra_headers: dict[str, str] | None = None,
    ) -> dict | list:
        headers = self.auth.headers()
        if extra_headers:
            headers.update(extra_headers)
        body = json.dumps(payload).encode("utf-8") if payload is not None else None
        req = urllib.request.Request(
            f"{self.auth.normalized_host}{path}",
            data=body,
            method=method,
            headers=headers,
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout_seconds) as resp:
                raw = resp.read().decode("utf-8")
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as exc:
            # Auto-refresh token on 401 and retry once
            if exc.code == 401 and _can_login():
                new_token = refresh_token(self.auth.host)
                self.auth = AnypointAuth(host=self.auth.host, token=new_token)
                headers = self.auth.headers()
                if extra_headers:
                    headers.update(extra_headers)
                req = urllib.request.Request(
                    f"{self.auth.normalized_host}{path}",
                    data=body,
                    method=method,
                    headers=headers,
                )
                try:
                    with urllib.request.urlopen(req, timeout=self.timeout_seconds) as resp:
                        raw = resp.read().decode("utf-8")
                        return json.loads(raw) if raw else {}
                except urllib.error.HTTPError as retry_exc:
                    raw = retry_exc.read().decode("utf-8", errors="replace")
                    raise RuntimeError(f"HTTP {retry_exc.code} for {path} (after token refresh): {raw}") from retry_exc
            raw = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"HTTP {exc.code} for {path}: {raw}") from exc

    def _env_headers(self, org_id: str, env_id: str) -> dict[str, str]:
        return {"X-ANYPNT-ORG-ID": org_id, "X-ANYPNT-ENV-ID": env_id}

    # -- Discovery --

    def get_me(self) -> dict:
        return self._request("/accounts/api/me")

    def get_organization(self, org_id: str) -> dict:
        return self._request(f"/accounts/api/organizations/{org_id}")

    def list_environments(self, org_id: str) -> list[dict]:
        resp = self._request(f"/accounts/api/organizations/{org_id}/environments")
        return resp.get("data", resp) if isinstance(resp, dict) else resp

    def list_business_groups(self, org_id: str) -> list[dict]:
        """Return immediate child business groups (sub-organizations) of an org.

        Uses the /hierarchy endpoint which returns the full tree rooted at org_id.
        Falls back to /organizations/{id} and reading subOrganizationIds if the
        hierarchy endpoint is not available.
        """
        # The org detail response contains subOrganizationIds — fetch each one
        org = self.get_organization(org_id)
        sub_ids = org.get("subOrganizationIds", [])
        children = []
        for sid in sub_ids:
            try:
                child = self.get_organization(sid)
                children.append(child)
            except RuntimeError:
                pass  # skip inaccessible sub-orgs
        return children

    def walk_organization_tree(self, root_org_id: str) -> list[dict]:
        """Recursively discover all business groups under root_org_id.

        Returns a flat list of org dicts (including the root) with an added
        '_bg_path' field showing the hierarchy path (e.g. "Root > BG1 > BG1a").
        """
        result: list[dict] = []
        visited: set[str] = set()

        def _walk(org_id: str, path: str) -> None:
            if org_id in visited:
                return
            visited.add(org_id)
            try:
                org = self.get_organization(org_id)
            except RuntimeError:
                return
            org["_bg_path"] = path
            result.append(org)
            for sub_id in org.get("subOrganizationIds", []):
                sub_name = sub_id  # will be replaced by actual name
                try:
                    sub = self.get_organization(sub_id)
                    sub_name = sub.get("name", sub_id)
                    sub["_bg_path"] = f"{path} > {sub_name}"
                    result.append(sub)
                    visited.add(sub_id)
                    for nested_id in sub.get("subOrganizationIds", []):
                        _walk(nested_id, f"{path} > {sub_name}")
                except RuntimeError:
                    pass

        try:
            root = self.get_organization(root_org_id)
            root_name = root.get("name", root_org_id)
        except RuntimeError:
            root_name = root_org_id
        _walk(root_org_id, root_name)
        return result

    def list_org_members(self, org_id: str) -> list[dict]:
        resp = self._request(f"/accounts/api/organizations/{org_id}/members")
        return resp.get("data", resp) if isinstance(resp, dict) else resp

    # -- CloudHub 2.0 / Runtime Fabric (Runtime Manager v2) --

    def list_ch2_deployments(self, org_id: str, env_id: str) -> list[dict]:
        """List CloudHub 2.0 and Runtime Fabric deployments via Runtime Manager v2."""
        resp = self._request(
            f"/amc/application-manager/api/v2/organizations/{org_id}/environments/{env_id}/deployments",
            extra_headers=self._env_headers(org_id, env_id),
        )
        if isinstance(resp, dict):
            return resp.get("items", resp.get("data", []))
        return resp

    def get_ch2_deployment(self, org_id: str, env_id: str, deployment_id: str) -> dict:
        return self._request(
            f"/amc/application-manager/api/v2/organizations/{org_id}/environments/{env_id}/deployments/{deployment_id}",
            extra_headers=self._env_headers(org_id, env_id),
        )

    def start_ch2_deployment(self, org_id: str, env_id: str, deployment_id: str) -> dict:
        """Start a CloudHub 2.0 / RTF deployment by setting desiredState to STARTED."""
        return self._request(
            f"/amc/application-manager/api/v2/organizations/{org_id}/environments/{env_id}/deployments/{deployment_id}",
            method="PATCH",
            payload={"application": {"desiredState": "STARTED"}},
            extra_headers=self._env_headers(org_id, env_id),
        )

    def stop_ch2_deployment(self, org_id: str, env_id: str, deployment_id: str) -> dict:
        """Stop a CloudHub 2.0 / RTF deployment by setting desiredState to STOPPED."""
        return self._request(
            f"/amc/application-manager/api/v2/organizations/{org_id}/environments/{env_id}/deployments/{deployment_id}",
            method="PATCH",
            payload={"application": {"desiredState": "STOPPED"}},
            extra_headers=self._env_headers(org_id, env_id),
        )

    # -- CloudHub 1.0 --

    def list_cloudhub_apps(self, org_id: str, env_id: str) -> list[dict]:
        resp = self._request(
            "/cloudhub/api/applications",
            extra_headers=self._env_headers(org_id, env_id),
        )
        return resp if isinstance(resp, list) else resp.get("data", [])

    def get_cloudhub_app(self, org_id: str, env_id: str, domain: str) -> dict:
        return self._request(
            f"/cloudhub/api/applications/{domain}",
            extra_headers=self._env_headers(org_id, env_id),
        )

    def start_cloudhub_app(self, org_id: str, env_id: str, domain: str) -> dict:
        """Start a CloudHub 1.0 application."""
        return self._request(
            f"/cloudhub/api/applications/{domain}/status",
            method="POST",
            payload={"status": "start"},
            extra_headers=self._env_headers(org_id, env_id),
        )

    def stop_cloudhub_app(self, org_id: str, env_id: str, domain: str) -> dict:
        """Stop a CloudHub 1.0 application."""
        return self._request(
            f"/cloudhub/api/applications/{domain}/status",
            method="POST",
            payload={"status": "stop"},
            extra_headers=self._env_headers(org_id, env_id),
        )

    # -- Runtime Manager (hybrid / on-prem) --

    def list_hybrid_apps(self, org_id: str, env_id: str) -> list[dict]:
        resp = self._request(
            "/hybrid/api/v1/applications",
            extra_headers=self._env_headers(org_id, env_id),
        )
        return resp.get("data", resp) if isinstance(resp, dict) else resp

    def list_servers(self, org_id: str, env_id: str) -> list[dict]:
        resp = self._request(
            "/hybrid/api/v1/servers",
            extra_headers=self._env_headers(org_id, env_id),
        )
        return resp.get("data", resp) if isinstance(resp, dict) else resp

    def list_server_groups(self, org_id: str, env_id: str) -> list[dict]:
        resp = self._request(
            "/hybrid/api/v1/serverGroups",
            extra_headers=self._env_headers(org_id, env_id),
        )
        return resp.get("data", resp) if isinstance(resp, dict) else resp

    def list_clusters(self, org_id: str, env_id: str) -> list[dict]:
        resp = self._request(
            "/hybrid/api/v1/clusters",
            extra_headers=self._env_headers(org_id, env_id),
        )
        return resp.get("data", resp) if isinstance(resp, dict) else resp

    # -- API Manager --

    def list_api_instances(self, org_id: str, env_id: str) -> list[dict]:
        resp = self._request(
            f"/apimanager/api/v1/organizations/{org_id}/environments/{env_id}/apis",
            extra_headers=self._env_headers(org_id, env_id),
        )
        if isinstance(resp, dict):
            return resp.get("assets", resp.get("apis", resp.get("data", [])))
        return resp

    def get_api_instance(self, org_id: str, env_id: str, api_id: int | str) -> dict:
        return self._request(
            f"/apimanager/api/v1/organizations/{org_id}/environments/{env_id}/apis/{api_id}",
            extra_headers=self._env_headers(org_id, env_id),
        )

    # -- Exchange --

    def list_exchange_assets(self, org_id: str) -> list[dict]:
        resp = self._request(f"/exchange/api/v2/assets?organizationId={org_id}")
        return resp if isinstance(resp, list) else resp.get("data", [])

    # -- Anypoint MQ --

    def list_mq_regions(self, org_id: str, env_id: str) -> list[dict]:
        resp = self._request(
            f"/mq/admin/api/v1/organizations/{org_id}/environments/{env_id}/regions",
        )
        return resp if isinstance(resp, list) else resp.get("data", [])

    def list_mq_destinations(self, org_id: str, env_id: str, region_id: str) -> list[dict]:
        resp = self._request(
            f"/mq/admin/api/v1/organizations/{org_id}/environments/{env_id}/regions/{region_id}/destinations",
        )
        return resp if isinstance(resp, list) else resp.get("data", [])

    # -- Audit Log --

    def query_audit_log(
        self,
        org_id: str,
        *,
        start_date: str | None = None,
        end_date: str | None = None,
        platforms: list[str] | None = None,
        object_types: list[str] | None = None,
        actions: list[str] | None = None,
        environment_ids: list[str] | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> dict:
        body: dict[str, Any] = {"limit": limit, "offset": offset}
        if start_date:
            body["startDate"] = start_date
        if end_date:
            body["endDate"] = end_date
        if platforms:
            body["platforms"] = platforms
        if object_types:
            body["objectTypes"] = object_types
        if actions:
            body["actions"] = actions
        if environment_ids:
            body["environmentIds"] = environment_ids
        return self._request(
            f"/audit/v2/organizations/{org_id}/query",
            method="POST",
            payload=body,
        )
