"""Configuration loading for the Watchmen local tooling."""

from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def project_path(value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else ROOT / path


def load_json(path: str | Path) -> dict:
    resolved = project_path(str(path))
    with resolved.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def save_json(path: str | Path, data: dict) -> None:
    resolved = project_path(str(path))
    with resolved.open("w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2)
        handle.write("\n")


def load_settings(path: str = "config/settings.json") -> dict:
    return load_json(path)


def save_settings(data: dict, path: str = "config/settings.json") -> None:
    save_json(path, data)


def load_entitlements(path: str = "config/entitlements.json") -> dict:
    return load_json(path)


def save_entitlements(data: dict, path: str = "config/entitlements.json") -> None:
    save_json(path, data)


_AUDIT_RULES_PATH = "config/audit_rules.json"


def load_audit_rules(path: str = _AUDIT_RULES_PATH) -> dict | None:
    """Load audit rules from config. Returns None if file doesn't exist."""
    resolved = project_path(path)
    if not resolved.exists():
        return None
    return load_json(path)


def save_audit_rules(data: dict, path: str = _AUDIT_RULES_PATH) -> None:
    save_json(path, data)
