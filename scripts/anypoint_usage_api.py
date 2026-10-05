"""Small Anypoint Usage API client."""

from __future__ import annotations

import json
import urllib.error
import urllib.request

from anypoint_auth import AnypointAuth


class UsageApiClient:
    def __init__(self, auth: AnypointAuth, timeout_seconds: int = 45) -> None:
        self.auth = auth
        self.timeout_seconds = timeout_seconds

    def describe_meters(self) -> dict | list:
        return self._request_json("/metering/usage/api/v1/meters:describe")

    def search(self, query: str) -> dict | list:
        return self._request_json(
            "/metering/usage/api/v1/meters:search",
            method="POST",
            payload={"query": query},
        )

    def _request_json(self, path: str, method: str = "GET", payload: dict | None = None) -> dict | list:
        body = json.dumps(payload).encode("utf-8") if payload is not None else None
        req = urllib.request.Request(
            f"{self.auth.normalized_host}{path}",
            data=body,
            method=method,
            headers=self.auth.headers(),
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout_seconds) as resp:
                raw = resp.read().decode("utf-8")
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"HTTP {exc.code} for {path}: {raw}") from exc
