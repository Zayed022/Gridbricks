from __future__ import annotations

import calendar
import math
from collections import defaultdict
from datetime import date, datetime, time, timedelta, timezone
from typing import Any

UTC = timezone.utc


def parse_dt(value: Any) -> datetime | None:
    """Parse Elexon ISO timestamps into timezone-aware UTC datetimes."""
    if value is None:
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        text = str(value).strip()
        if not text:
            return None
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            dt = datetime.fromisoformat(text)
        except ValueError:
            for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M:%SZ"):
                try:
                    dt = datetime.strptime(str(value), fmt)
                    break
                except ValueError:
                    continue
            else:
                return None
    if dt.tzinfo is None:
        # Elexon returns UTC timestamps. Treat naive values as UTC explicitly.
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def parse_number(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        result = float(value)
        return result if math.isfinite(result) else None
    except (TypeError, ValueError):
        return None


def unwrap_records(payload: Any) -> list[dict[str, Any]]:
    """Support stream endpoints (arrays) and enveloped JSON responses."""
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, dict)]
    if isinstance(payload, dict):
        for key in ("data", "results", "records", "items"):
            value = payload.get(key)
            if isinstance(value, list):
                return [row for row in value if isinstance(row, dict)]
    return []


def month_bounds(month: str) -> tuple[datetime, datetime, int]:
    """Return UTC [start, end) bounds for the selected decision month."""
    parts = month.split("-")
    if len(parts) != 2:
        raise ValueError("Month must be in YYYY-MM format")
    try:
        year, mon = (int(part) for part in parts)
    except ValueError as exc:
        raise ValueError("Month must be in YYYY-MM format") from exc
    if mon < 1 or mon > 12:
        raise ValueError("Month must be in YYYY-MM format")
    start = datetime(year, mon, 1, tzinfo=UTC)
    last_day = calendar.monthrange(year, mon)[1]
    end = datetime(year + (mon == 12), 1 if mon == 12 else mon + 1, 1, tzinfo=UTC)
    return start, end, last_day


def decision_and_delivery_bounds(month: str) -> tuple[datetime, datetime, datetime, datetime, int]:
    """Decision dates are in the selected month; each decision serves D+1 delivery."""
    decision_start, decision_end, day_count = month_bounds(month)
    return decision_start, decision_end, decision_start + timedelta(days=1), decision_end + timedelta(days=1), day_count


def _normalise_rows(records: list[dict[str, Any]], month: str, is_forecast: bool) -> list[dict[str, Any]]:
    _, _, delivery_start, delivery_end, _ = decision_and_delivery_bounds(month)
    rows: list[dict[str, Any]] = []
    for row in records:
        target = parse_dt(row.get("startTime") or row.get("start_time"))
        value = parse_number(row.get("generation"))
        if target is None or value is None or not (delivery_start <= target < delivery_end):
            continue
        if not is_forecast:
            fuel = str(row.get("fuelType") or row.get("fueltype") or "").upper()
            if fuel != "WIND":
                continue
        published = parse_dt(row.get("publishTime") or row.get("publish_time"))
        revision_value = row.get("documentRevisionNumber") or row.get("revisionNumber") or row.get("revision")
        rows.append({
            "target": target,
            "generation": value,
            "publish": published,
            "revision": parse_number(revision_value) or 0,
            "settlement_period": row.get("settlementPeriod") or row.get("settlement_period"),
        })
    return rows


def select_actuals(records: list[dict[str, Any]], month: str) -> dict[datetime, dict[str, Any]]:
    """Select latest FUELHH WIND revision per target half-hour."""
    selected: dict[datetime, dict[str, Any]] = {}
    oldest = datetime.min.replace(tzinfo=UTC)
    for row in _normalise_rows(records, month, is_forecast=False):
        key = row["target"]
        old = selected.get(key)
        new_rank = (row["publish"] or oldest, row["revision"])
        old_rank = ((old or {}).get("publish") or oldest, (old or {}).get("revision", 0))
        if old is None or new_rank >= old_rank:
            selected[key] = row
    return selected


def aggregate_actuals_hourly(actuals: dict[datetime, dict[str, Any]], month: str) -> dict[datetime, dict[str, Any]]:
    """Aggregate two FUELHH half-hour MW observations into one hourly average MW.

    A target hour is considered complete only when both half-hour actuals are
    present. Incomplete hours remain null rather than being imputed.
    """
    _, _, delivery_start, delivery_end, _ = decision_and_delivery_bounds(month)
    result: dict[datetime, dict[str, Any]] = {}
    hour = delivery_start
    while hour < delivery_end:
        first = actuals.get(hour)
        second = actuals.get(hour + timedelta(minutes=30))
        values = [
            first["generation"] if first else None,
            second["generation"] if second else None,
        ]
        present = [value for value in values if value is not None]
        complete = len(present) == 2
        result[hour] = {
            "target": hour,
            "generation": sum(present) / 2 if complete else None,
            "half_hour_count": len(present),
            "half_hour_values": values,
            "complete": complete,
        }
        hour += timedelta(hours=1)
    return result


def _parse_cutoff(cutoff_time: str) -> time:
    try:
        hh_text, mm_text = cutoff_time.split(":", 1)
        hh, mm = int(hh_text), int(mm_text)
        if hh not in range(24) or mm not in range(60):
            raise ValueError
        return time(hh, mm)
    except (ValueError, AttributeError):
        raise ValueError("cutoff_time must be HH:MM in UTC") from None


def select_forecasts(
    records: list[dict[str, Any]], month: str, cutoff_time: str
) -> dict[datetime, dict[str, Any]]:
    """Select the latest forecast revision available by the D-1 cutoff per target."""
    cutoff_clock = _parse_cutoff(cutoff_time)
    by_target: dict[datetime, list[dict[str, Any]]] = defaultdict(list)
    for row in _normalise_rows(records, month, is_forecast=True):
        if row["publish"] is not None:
            by_target[row["target"]].append(row)

    selected: dict[datetime, dict[str, Any]] = {}
    for target, versions in by_target.items():
        decision_day = target.date() - timedelta(days=1)
        cutoff = datetime.combine(decision_day, cutoff_clock, tzinfo=UTC)
        eligible = [row for row in versions if row["publish"] <= cutoff]
        if eligible:
            selected[target] = max(eligible, key=lambda row: (row["publish"], row["revision"]))
    return selected


def forecast_diagnostics(
    records: list[dict[str, Any]], month: str, cutoff_time: str,
    selected_forecasts: dict[datetime, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Diagnose raw forecasts against the intended hourly evaluation grid."""
    raw_rows = _normalise_rows(records, month, is_forecast=True)
    raw_targets = {row["target"] for row in raw_rows}
    selected = selected_forecasts if selected_forecasts is not None else select_forecasts(records, month, cutoff_time)
    eligible_targets = set(selected)
    decision_start, decision_end, delivery_start, delivery_end, day_count = decision_and_delivery_bounds(month)
    expected_hourly_targets: set[datetime] = set()
    ts = delivery_start
    while ts < delivery_end:
        expected_hourly_targets.add(ts)
        ts += timedelta(hours=1)
    raw_half_hour_examples = [
        {
            "start_time": row["target"].isoformat().replace("+00:00", "Z"),
            "publish_time": row["publish"].isoformat().replace("+00:00", "Z") if row["publish"] else None,
            "generation_mw": row["generation"],
        }
        for row in raw_rows if row["target"].minute == 30
    ][:5]
    raw_targets_without_eligible = sorted(raw_targets - eligible_targets)
    return {
        "forecast_resolution": "hourly",
        "raw_forecast_records_in_delivery_window": len(raw_rows),
        "distinct_raw_forecast_targets": len(raw_targets),
        "raw_targets_on_hour": sum(ts.minute == 0 for ts in raw_targets),
        "raw_targets_on_half_hour": sum(ts.minute == 30 for ts in raw_targets),
        "eligible_targets_on_hour": sum(ts.minute == 0 for ts in eligible_targets),
        "eligible_targets_on_half_hour": sum(ts.minute == 30 for ts in eligible_targets),
        "raw_targets_without_eligible_revision": len(raw_targets_without_eligible),
        "expected_delivery_intervals": day_count * 24,
        "expected_targets_with_no_raw_record": len(expected_hourly_targets - raw_targets),
        "raw_hourly_targets_without_eligible_revision": len((expected_hourly_targets & raw_targets) - eligible_targets),
        "raw_half_hour_examples": raw_half_hour_examples,
        "half_hour_targets_without_eligible_examples": [
            ts.isoformat().replace("+00:00", "Z") for ts in raw_targets_without_eligible if ts.minute == 30
        ][:5],
    }


def compute_metrics(pairs: list[tuple[float, float]]) -> dict[str, float | int | None]:
    """Compute error metrics from matched (forecast MW, hourly-average actual MW) pairs."""
    if not pairs:
        return {"matched_intervals": 0, "mae_mw": None, "rmse_mw": None, "wape_pct": None, "bias_mw": None}
    errors = [forecast - actual for forecast, actual in pairs]
    abs_errors = [abs(error) for error in errors]
    actual_total = sum(actual for _, actual in pairs)
    return {
        "matched_intervals": len(pairs),
        "mae_mw": sum(abs_errors) / len(abs_errors),
        "rmse_mw": math.sqrt(sum(error * error for error in errors) / len(errors)),
        "wape_pct": (sum(abs_errors) / actual_total * 100) if actual_total > 0 else None,
        "bias_mw": sum(errors) / len(errors),
    }


def build_dashboard(
    *, month: str, actual_records: list[dict[str, Any]], forecast_records: list[dict[str, Any]],
    cutoff_time: str, selected_date: str, source_mode: str, fetched_at: str | None = None,
) -> dict[str, Any]:
    """Build a decision-month dashboard comparing hourly forecasts with hourly actual averages."""
    decision_start, decision_end, delivery_start, delivery_end, day_count = decision_and_delivery_bounds(month)
    actuals = select_actuals(actual_records, month)  # raw actual map is half-hourly
    hourly_actuals = aggregate_actuals_hourly(actuals, month)
    all_selected_forecasts = select_forecasts(forecast_records, month, cutoff_time)
    # Official Elexon wind-generation forecasts are hourly. Ignore non-hour target rows
    # in the hourly comparison while retaining their count in the diagnostics.
    forecasts = {target: row for target, row in all_selected_forecasts.items() if target.minute == 0}

    joined: list[dict[str, Any]] = []
    pairs: list[tuple[float, float]] = []
    ts = delivery_start
    while ts < delivery_end:
        actual = hourly_actuals.get(ts)
        forecast = forecasts.get(ts)
        actual_value = actual["generation"] if actual and actual["complete"] else None
        forecast_value = forecast["generation"] if forecast else None
        error = forecast_value - actual_value if forecast_value is not None and actual_value is not None else None
        if forecast_value is not None and actual_value is not None:
            pairs.append((forecast_value, actual_value))
        joined.append({
            "timestamp": ts.isoformat().replace("+00:00", "Z"),
            "hour_period": ts.hour + 1,
            "forecast_mw": round(forecast_value, 2) if forecast_value is not None else None,
            "actual_mw": round(actual_value, 2) if actual_value is not None else None,
            "actual_half_hour_count": actual["half_hour_count"] if actual else 0,
            "actual_half_hour_values_mw": [round(x, 2) if x is not None else None for x in actual["half_hour_values"]] if actual else [None, None],
            "error_mw": round(error, 2) if error is not None else None,
            "abs_error_mw": round(abs(error), 2) if error is not None else None,
            "publish_time": forecast["publish"].isoformat().replace("+00:00", "Z") if forecast and forecast.get("publish") else None,
        })
        ts += timedelta(hours=1)

    grouped: dict[str, dict[str, list[float]]] = defaultdict(lambda: {"actual": [], "forecast": [], "errors": []})
    for item in joined:
        day = item["timestamp"][:10]
        if item["actual_mw"] is not None and item["forecast_mw"] is not None and item["error_mw"] is not None:
            grouped[day]["actual"].append(item["actual_mw"])
            grouped[day]["forecast"].append(item["forecast_mw"])
            grouped[day]["errors"].append(item["error_mw"])

    daily_series: list[dict[str, Any]] = []
    for offset in range(day_count):
        day_date = (delivery_start + timedelta(days=offset)).date().isoformat()
        values = grouped[day_date]
        matched = values["errors"]
        daily_series.append({
            "date": day_date,
            "actual_avg_mw": round(sum(values["actual"]) / len(values["actual"]), 2) if values["actual"] else None,
            "forecast_avg_mw": round(sum(values["forecast"]) / len(values["forecast"]), 2) if values["forecast"] else None,
            "matched_intervals": len(matched),
            "mae_mw": round(sum(abs(error) for error in matched) / len(matched), 2) if matched else None,
        })

    decision_date = date.fromisoformat(selected_date)
    selected_delivery_date = (decision_date + timedelta(days=1)).isoformat()
    selected_rows = [row for row in joined if row["timestamp"][:10] == selected_delivery_date]
    latest_publish = max((row["publish_time"] for row in selected_rows if row["publish_time"]), default=None)
    expected_count = day_count * 24
    matched_count = len(pairs)
    complete_hourly_actual_count = sum(1 for row in hourly_actuals.values() if row["complete"])
    diagnostics = forecast_diagnostics(forecast_records, month, cutoff_time, all_selected_forecasts)
    diagnostics["comparison_resolution"] = "hourly"
    diagnostics["hourly_forecasts_selected_for_metrics"] = len(forecasts)
    diagnostics["selected_non_hourly_forecast_targets_excluded"] = sum(1 for target in all_selected_forecasts if target.minute != 0)
    diagnostics["raw_actual_half_hour_observations"] = len(actuals)
    diagnostics["expected_actual_half_hour_observations"] = day_count * 48
    diagnostics["complete_hourly_actual_buckets"] = complete_hourly_actual_count
    diagnostics["hourly_actual_buckets_missing_one_or_more_half_hours"] = expected_count - complete_hourly_actual_count
    coverage_pct = round(matched_count / expected_count * 100, 1) if expected_count else 0

    return {
        "month": month,
        "selected_date": selected_date,
        "selected_decision_date": selected_date,
        "selected_delivery_date": selected_delivery_date,
        "decision_window": {
            "decision_start": decision_start.date().isoformat(),
            "decision_end": (decision_end - timedelta(days=1)).date().isoformat(),
            "delivery_start": delivery_start.date().isoformat(),
            "delivery_end": (delivery_end - timedelta(days=1)).date().isoformat(),
        },
        "cutoff_time_utc": cutoff_time,
        "source_mode": source_mode,
        "fetched_at": fetched_at or datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "source_name": "Elexon BMRS / Insights API" if source_mode == "live" else "Synthetic demo dataset (not Elexon data)",
        "source_urls": {
            "actuals": "https://bmrs.elexon.co.uk/api-documentation/endpoint/datasets/FUELHH/stream",
            "forecasts": "https://bmrs.elexon.co.uk/api-documentation/endpoint/datasets/WINDFOR/stream",
            "forecast_resolution_reference": "https://bmrs.elexon.co.uk/wind-generation",
        },
        "assumptions": [
            "The reporting window is the selected month's decision dates; each decision date is matched to next-day delivery intervals.",
            "WINDFOR wind generation forecasts are evaluated hourly; FUELHH half-hourly actual MW observations are averaged in pairs for each UTC hour.",
            "A comparison hour requires both underlying half-hour actual observations; incomplete hours are not imputed.",
            "For each hourly target, the latest WINDFOR publication no later than the configured D-1 UTC cutoff is selected.",
            "Forecast error is forecast minus hourly-average actual; positive bias means wind generation was over-forecast.",
            "Metrics use matched hourly forecast/actual pairs only; missing data is not imputed.",
            "No demand or non-wind generation datasets are used, so this app does not calculate total procurement shortfall.",
        ],
        "metrics": compute_metrics(pairs),
        "coverage": {
            "comparison_resolution": "hourly",
            "expected_intervals": expected_count,
            "actual_intervals": complete_hourly_actual_count,
            "actual_half_hour_intervals": len(actuals),
            "expected_actual_half_hour_intervals": day_count * 48,
            "forecast_intervals_at_cutoff": len(forecasts),
            "matched_intervals": matched_count,
            "matched_pct_of_month": coverage_pct,
            "is_complete": matched_count == expected_count,
            "missing_actual_intervals": max(0, expected_count - complete_hourly_actual_count),
            "missing_forecast_intervals": max(0, expected_count - len(forecasts)),
            "missing_matched_intervals": max(0, expected_count - matched_count),
        },
        "forecast_diagnostics": diagnostics,
        "latest_selected_publish_time": latest_publish,
        "daily_series": daily_series,
        "intraday": selected_rows,
    }
