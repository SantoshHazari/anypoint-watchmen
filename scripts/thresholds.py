"""Entitlement threshold calculations."""

from __future__ import annotations


SEVERITY_BY_THRESHOLD = {
    95: "critical",
    90: "critical",
    80: "warning",
    70: "warning",
    50: "info",
}


def _usage_for_limit(raw_usage: float, limit: dict) -> float:
    if limit.get("api_unit") == "bytes" and limit.get("unit") == "GB":
        return raw_usage / (1024**3)
    return raw_usage


def evaluate_entitlements(entitlements: dict, usage_by_meter: dict[str, float]) -> list[dict]:
    thresholds = sorted(entitlements["policy"]["default_thresholds_percent"])
    statuses = []
    for limit in entitlements["limits"]:
        raw_usage = usage_by_meter.get(limit["meter_key"], 0.0)
        usage_value = _usage_for_limit(raw_usage, limit)
        limit_value = float(limit["limit"])
        percent = (usage_value / limit_value * 100) if limit_value else 0.0
        crossed = [threshold for threshold in thresholds if percent >= threshold]
        threshold = max(crossed) if crossed else None
        severity = SEVERITY_BY_THRESHOLD.get(threshold, "ok") if threshold else "ok"
        unit_divisor = (1024**3) if (limit.get("api_unit") == "bytes" and limit.get("unit") == "GB") else 1.0
        statuses.append(
            {
                "key": limit["key"],
                "name": limit["name"],
                "usage_value": usage_value,
                "raw_usage_value": raw_usage,
                "limit_value": limit_value,
                "unit": limit["unit"],
                "unit_divisor": unit_divisor,
                "usage_model": limit["usage_model"],
                "percent_used": round(percent, 4),
                "remaining": max(limit_value - usage_value, 0),
                "severity": severity,
                "threshold_percent": threshold,
                "status": limit.get("status"),
                "source": limit.get("source"),
                "conflicting_values": limit.get("conflicting_values", []),
            }
        )
    return statuses
