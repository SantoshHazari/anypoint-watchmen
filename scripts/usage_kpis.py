"""Expandable usage KPI calculations."""

from __future__ import annotations

import datetime as dt


SECONDS_PER_DAY = 86400


def _parse_utc(value: str) -> dt.datetime:
    return dt.datetime.fromisoformat(value.replace("Z", "+00:00"))


def _days_between(start_utc: str, end_utc: str) -> float:
    start = _parse_utc(start_utc)
    end = _parse_utc(end_utc)
    return max((end - start).total_seconds() / SECONDS_PER_DAY, 0.0)


def _windowed_burn(usage_total: float, usage_window: float, window_days: float) -> float | None:
    if window_days <= 0:
        return None
    return usage_window / window_days


def _budget_ratio(actual: float, budget: float) -> float:
    if budget <= 0:
        return 0.0
    return round(actual / budget * 100, 2)


def _budget_status(ratio: float) -> str:
    if ratio >= 150:
        return "critical"
    if ratio >= 120:
        return "danger"
    if ratio >= 100:
        return "warning"
    if ratio >= 80:
        return "elevated"
    return "ok"


def add_burn_rate_kpis(
    statuses: list[dict],
    *,
    window_start_utc: str,
    window_end_utc: str,
    contract_end_utc: str,
    usage_7d: dict[str, float] | None = None,
    usage_30d: dict[str, float] | None = None,
    usage_month: dict[str, float] | None = None,
    usage_today: dict[str, float] | None = None,
    month_elapsed_days: float = 0.0,
    month_total_days: float = 30.0,
) -> list[dict]:
    elapsed_days = _days_between(window_start_utc, window_end_utc)
    remaining_days = _days_between(window_end_utc, contract_end_utc)
    total_contract_days = elapsed_days + remaining_days
    contract_months = total_contract_days / 30.44
    enriched: list[dict] = []
    for status in statuses:
        item = dict(status)
        key = item["key"]
        usage = float(item["usage_value"])
        limit = item["limit_value"]
        divisor = item.get("unit_divisor", 1.0)
        is_hwm = item.get("usage_model") == "HIGH_WATERMARK"

        if is_hwm:
            # HIGH_WATERMARK: value is a concurrent peak, not cumulative.
            # "Projection" is the current value itself; burn rate is meaningless.
            daily_burn = 0.0
            allowed_daily_burn_total = 0.0
            allowed_usage_to_date = limit
            usage_vs_expected = usage - limit
            usage_vs_expected_percent = (usage / limit * 100) if limit > 0 else 0.0
            allowed_remaining_daily_burn = 0.0
            allowed_remaining_monthly_burn = 0.0
            projected_total = usage
            projected_percent = (usage / limit * 100) if limit else 0.0
            days_to_exhaustion = None
            exhaustion_date = None
            burn_7d = None
            burn_30d = None
            burn_acceleration = None
            # For HWM the budget IS the limit — compare current value directly
            monthly_budget = float(limit)
            month_usage = (usage_month or {}).get(key, usage)
            month_projected = month_usage
            month_ratio = _budget_ratio(month_usage, limit)
            month_actual_ratio = month_ratio
            daily_budget = float(limit)
            today_usage = (usage_today or {}).get(key, usage)
            day_ratio = _budget_ratio(today_usage, limit)
        else:
            # DRAWDOWN / SUM: cumulative consumption against a finite pool.
            daily_burn = usage / elapsed_days if elapsed_days > 0 else 0.0
            allowed_daily_burn_total = limit / total_contract_days if total_contract_days > 0 else 0.0
            allowed_usage_to_date = allowed_daily_burn_total * elapsed_days
            usage_vs_expected = usage - allowed_usage_to_date
            usage_vs_expected_percent = (usage / allowed_usage_to_date * 100) if allowed_usage_to_date > 0 else 0.0
            allowed_remaining_daily_burn = item["remaining"] / remaining_days if remaining_days > 0 else 0.0
            allowed_remaining_monthly_burn = allowed_remaining_daily_burn * 30
            projected_total = usage + (daily_burn * remaining_days)
            projected_percent = (projected_total / limit * 100) if limit else 0.0
            if daily_burn > 0 and item["remaining"] > 0:
                days_to_exhaustion = item["remaining"] / daily_burn
                if days_to_exhaustion > 36500:  # cap at ~100 years
                    exhaustion_date = None
                else:
                    exhaustion_date = (_parse_utc(window_end_utc) + dt.timedelta(days=days_to_exhaustion)).date().isoformat()
            else:
                days_to_exhaustion = None
                exhaustion_date = None

            burn_7d = _windowed_burn(usage, (usage_7d or {}).get(key, 0.0) / divisor, min(elapsed_days, 7))
            burn_30d = _windowed_burn(usage, (usage_30d or {}).get(key, 0.0) / divisor, min(elapsed_days, 30))
            burn_acceleration = None
            if burn_7d is not None and burn_30d is not None and burn_30d > 0:
                burn_acceleration = round((burn_7d - burn_30d) / burn_30d * 100, 4)

            monthly_budget = limit / contract_months if contract_months > 0 else 0.0
            month_usage = (usage_month or {}).get(key, 0.0) / divisor
            if month_elapsed_days > 0 and month_total_days > 0:
                month_projected = month_usage / month_elapsed_days * month_total_days
            else:
                month_projected = month_usage
            month_ratio = _budget_ratio(month_projected, monthly_budget)
            month_actual_ratio = _budget_ratio(month_usage, monthly_budget)
            daily_budget = limit / total_contract_days if total_contract_days > 0 else 0.0
            today_usage = (usage_today or {}).get(key, 0.0) / divisor
            day_ratio = _budget_ratio(today_usage, daily_budget)

        item["kpis"] = {
            "elapsed_days": round(elapsed_days, 4),
            "remaining_contract_days": round(remaining_days, 4),
            "total_contract_days": round(total_contract_days, 4),
            "is_high_watermark": is_hwm,
            "allowed_usage_to_date": round(allowed_usage_to_date, 6),
            "usage_vs_expected_to_date": round(usage_vs_expected, 6),
            "usage_vs_expected_to_date_percent": round(usage_vs_expected_percent, 4),
            "average_daily_burn": round(daily_burn, 6),
            "burn_7d": round(burn_7d, 6) if burn_7d is not None else None,
            "burn_30d": round(burn_30d, 6) if burn_30d is not None else None,
            "burn_acceleration_pct": burn_acceleration,
            "allowed_daily_burn_total_contract": round(allowed_daily_burn_total, 6),
            "allowed_remaining_daily_burn": round(allowed_remaining_daily_burn, 6),
            "allowed_remaining_monthly_burn": round(allowed_remaining_monthly_burn, 6),
            "projected_contract_total": round(projected_total, 6),
            "projected_contract_percent": round(projected_percent, 4),
            "days_to_exhaustion": round(days_to_exhaustion, 4) if days_to_exhaustion is not None else None,
            "projected_exhaustion_date": exhaustion_date,
            "burn_rate_status": _burn_rate_status(projected_percent),
            "monthly_budget": round(monthly_budget, 4),
            "month_usage": round(month_usage, 4),
            "month_projected": round(month_projected, 4),
            "month_ratio": month_ratio,
            "month_actual_ratio": month_actual_ratio,
            "month_status": _budget_status(month_ratio),
            "daily_budget": round(daily_budget, 4),
            "today_usage": round(today_usage, 4),
            "day_ratio": day_ratio,
            "day_status": _budget_status(day_ratio),
        }
        enriched.append(item)
    return enriched


def _burn_rate_status(projected_percent: float) -> str:
    if projected_percent >= 100:
        return "projected_overage"
    if projected_percent >= 80:
        return "projected_risk"
    if projected_percent >= 50:
        return "watch"
    return "ok"
