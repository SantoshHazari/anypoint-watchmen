"""Authentication helpers for Anypoint Platform API scripts.

Supports three modes (checked in order):
  1. ANYPOINT_TOKEN env var — static bearer token
  2. ANYPOINT_CLIENT_ID + ANYPOINT_CLIENT_SECRET — OAuth 2.0 Client Credentials (Connected App)
  3. ANYPOINT_USERNAME + ANYPOINT_PASSWORD — login and cache token, auto-refresh on expiry (deprecated)

The module keeps a cached token so repeated calls to load_auth() are cheap,
and automatically re-authenticates when a 401 is encountered.
"""

from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
import os


DEFAULT_HOST = "https://anypoint.mulesoft.com"

# Module-level token cache
_token_cache: dict = {"token": "", "obtained_at": 0.0, "expires_in": 3600, "host": ""}
_token_lock = threading.Lock()


@dataclass(frozen=True)
class AnypointAuth:
    host: str
    token: str

    @property
    def normalized_host(self) -> str:
        return self.host.rstrip("/")

    def headers(self) -> dict[str, str]:
        return {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.token}",
        }


def _oauth_client_credentials(host: str, client_id: str, client_secret: str) -> tuple[str, int]:
    """Authenticate with Connected App client credentials (OAuth 2.0 Client Credentials flow).

    Uses client_secret_post method: credentials sent in the request body.
    Endpoint confirmed via: GET /accounts/api/v2/oauth2/.well-known/openid-configuration

    Returns (access_token, expires_in_seconds).
    """
    url = f"{host.rstrip('/')}/accounts/api/v2/oauth2/token"
    body = f"grant_type=client_credentials&client_id={client_id}&client_secret={client_secret}".encode()
    req = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"}
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode())
            token = data.get("access_token")
            if not token:
                raise RuntimeError(f"OAuth token request succeeded but no access_token in response: {list(data.keys())}")
            expires_in = int(data.get("expires_in", 3600))
            return token, expires_in
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"OAuth Client Credentials authentication failed: HTTP {exc.code} {exc.reason}") from exc


def _login(host: str, username: str, password: str) -> str:
    """Authenticate with username/password and return a bearer token (DEPRECATED - use OAuth instead)."""
    url = f"{host.rstrip('/')}/accounts/login"
    body = json.dumps({"username": username, "password": password}).encode()
    req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode())
            token = data.get("access_token") or data.get("token_type", "")
            if not token:
                raise RuntimeError(f"Login succeeded but no access_token in response: {list(data.keys())}")
            return token
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"Login failed: HTTP {exc.code} {exc.reason}") from exc


def _can_use_oauth() -> bool:
    """Return True if Connected App OAuth credentials are configured."""
    return bool(os.environ.get("ANYPOINT_CLIENT_ID") and os.environ.get("ANYPOINT_CLIENT_SECRET"))


def _can_login() -> bool:
    """Return True if username/password credentials are configured."""
    return bool(os.environ.get("ANYPOINT_USERNAME") and os.environ.get("ANYPOINT_PASSWORD"))


def _obtain_token(host: str) -> str:
    """Get a fresh token. Tries OAuth first, then username/password. Caches the result."""
    token = None
    expires_in = 3600  # default

    # Try OAuth 2.0 Client Credentials (Connected App) first
    if _can_use_oauth():
        client_id = os.environ.get("ANYPOINT_CLIENT_ID", "")
        client_secret = os.environ.get("ANYPOINT_CLIENT_SECRET", "")
        token, expires_in = _oauth_client_credentials(host, client_id, client_secret)
    # Fall back to username/password
    elif _can_login():
        username = os.environ.get("ANYPOINT_USERNAME", "")
        password = os.environ.get("ANYPOINT_PASSWORD", "")
        token = _login(host, username, password)
    else:
        raise RuntimeError(
            "Cannot obtain token: neither OAuth credentials (ANYPOINT_CLIENT_ID + ANYPOINT_CLIENT_SECRET) "
            "nor username/password (ANYPOINT_USERNAME + ANYPOINT_PASSWORD) are configured"
        )

    with _token_lock:
        _token_cache["token"] = token
        _token_cache["obtained_at"] = time.time()
        _token_cache["expires_in"] = expires_in
        _token_cache["host"] = host
    return token


def refresh_token(host: str | None = None) -> str:
    """Force a fresh token and return it. Called on 401 errors."""
    resolved_host = host or os.environ.get("ANYPOINT_HOST", DEFAULT_HOST)
    if not (_can_use_oauth() or _can_login()):
        raise RuntimeError(
            "Cannot refresh token: neither OAuth credentials (ANYPOINT_CLIENT_ID + ANYPOINT_CLIENT_SECRET) "
            "nor username/password (ANYPOINT_USERNAME + ANYPOINT_PASSWORD) are set"
        )
    return _obtain_token(resolved_host)


def auth_status() -> dict:
    """Return auth health info for the health check page."""
    token_env = os.environ.get("ANYPOINT_TOKEN", "")
    has_oauth = _can_use_oauth()
    has_login_creds = _can_login()
    has_token = bool(token_env) or bool(_token_cache["token"])

    if has_token or has_oauth or has_login_creds:
        if token_env:
            method = "static token env"
        elif has_oauth:
            method = "OAuth 2.0 Connected App (auto-refresh)"
        else:
            method = "username/password login (auto-refresh)"
        return {"ok": True, "detail": f"authenticated via {method}"}
    return {
        "ok": False,
        "detail": "No credentials configured (set ANYPOINT_TOKEN or ANYPOINT_CLIENT_ID + ANYPOINT_CLIENT_SECRET or ANYPOINT_USERNAME + ANYPOINT_PASSWORD)"
    }


def load_auth(host: str | None = None, token_env: str = "ANYPOINT_TOKEN") -> AnypointAuth:
    resolved_host = host or os.environ.get("ANYPOINT_HOST", DEFAULT_HOST)

    # 1. Try explicit token from env
    token = os.environ.get(token_env)
    if token:
        return AnypointAuth(host=resolved_host, token=token)

    # 2. Try cached token (refresh proactively at 90% of its lifetime)
    with _token_lock:
        cached = _token_cache["token"]
        age = time.time() - _token_cache["obtained_at"]
        expires_in = _token_cache["expires_in"]
    if cached and age < expires_in * 0.9:
        return AnypointAuth(host=resolved_host, token=cached)

    # 3. Try OAuth 2.0 Client Credentials (Connected App) or username/password
    if _can_use_oauth() or _can_login():
        token = _obtain_token(resolved_host)
        return AnypointAuth(host=resolved_host, token=token)

    raise RuntimeError(
        f"No credentials found. Set ${token_env} or both $ANYPOINT_CLIENT_ID and $ANYPOINT_CLIENT_SECRET (OAuth) "
        f"or both $ANYPOINT_USERNAME and $ANYPOINT_PASSWORD (deprecated)."
    )
