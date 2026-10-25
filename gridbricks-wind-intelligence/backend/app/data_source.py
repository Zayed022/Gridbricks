from __future__ import annotations

import asyncio
import hashlib
import math
import os
import random
import time as monotonic_time
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from .analytics import month_bounds, unwrap_records

API_BASE = os.getenv("ELEXON_API_BASE", "https://data.elexon.co.uk/bmrs/api/v1")
CACHE_TTL_SECONDS = int(os.getenv("CACHE_TTL_SECONDS", "900"))
_CACHE: dict[tuple[str, str], tuple[float, tuple[list[dict[str, Any]], list[dict[str, Any]]]]] = {}
UTC = timezone.utc


class ElexonFetchError(RuntimeError):
    pass


def _iso(dt: datetime) -> str:
    return dt.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


async def _get_json(client: httpx.AsyncClient, path: str, params: dict[str, str]) -> Any:
    url = f"{API_BASE.rstrip('/')}/{path.lstrip('/')}"
    try:
        response = await client.get(url, params=params)
        # A few dataset deployments expose a narrower query schema on stream endpoints.
        # The retry below in fetch_month removes optional publication-time filters if needed.
        response.raise_for_status()
        return response.json()
    except httpx.HTTPStatusError as exc:
        raise ElexonFetchError(f"Elexon returned HTTP {exc.response.status_code} for {path}: {exc.response.text[:240]}") from exc
    except (httpx.TimeoutException, httpx.RequestError) as exc:
        raise ElexonFetchError(f"Could not reach Elexon {path}: {exc}") from exc
    except ValueError as exc:
        raise ElexonFetchError(f"Elexon returned a non-JSON response for {path}") from exc


async def fetch_month(month: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Fetch actuals and forecast revisions for a decision month and its next-day deliveries."""
    key = (month, "live")
    cached = _CACHE.get(key)
    now = monotonic_time.monotonic()
    if cached and now - cached[0] < CACHE_TTL_SECONDS:
        return cached[1]

    start, end, _ = month_bounds(month)
    # Scope is the selected month's purchase-decision dates. A decision on D serves
    # delivery on D+1, so January decisions require Jan 2 through Feb 1 actuals.
    delivery_start = start + timedelta(days=1)
    delivery_end = end + timedelta(days=1)  # exclusive; includes next month's day 1
    actual_params = {
        "settlementDateFrom": delivery_start.date().isoformat(),
        "settlementDateTo": (delivery_end - timedelta(days=1)).date().isoformat(),
    }
    # WINDFOR must be queried by publication time. The analytics layer applies the
    # target interval's D-1 cutoff and excludes later revisions to avoid look-ahead.
    # Keep earlier issues in the fetch window in case no newer eligible revision exists.
    forecast_params = {
        "publishDateTimeFrom": _iso(start - timedelta(days=2)),
        "publishDateTimeTo": _iso(delivery_end),
    }
    timeout = httpx.Timeout(60.0, connect=12.0)
    async with httpx.AsyncClient(timeout=timeout, headers={"Accept": "application/json", "User-Agent": "GridBricks-Wind-Intelligence/1.0"}) as client:
        actual_payload, forecast_payload = await asyncio.gather(
            _get_json(client, "datasets/FUELHH/stream", actual_params),
            _get_json(client, "datasets/WINDFOR/stream", forecast_params),
        )
    forecast_records = unwrap_records(forecast_payload)
    actual_records = unwrap_records(actual_payload)
    if not actual_records:
        raise ElexonFetchError("Elexon FUELHH returned no actual-generation records for this period. Check the API filters or retry later.")
    if not forecast_records:
        raise ElexonFetchError("Elexon WINDFOR returned no forecast records for this period. Check the API filters or retry later.")
    result = (actual_records, forecast_records)
    _CACHE[key] = (now, result)
    return result


def _seed(*parts: str) -> int:
    digest = hashlib.sha256("|".join(parts).encode()).hexdigest()[:16]
    return int(digest, 16)


def generate_demo_month(month: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Generate synthetic half-hour actuals and hourly forecast revisions for offline UI tests."""
    start, end, _ = month_bounds(month)
    actuals: list[dict[str, Any]] = []
    forecasts: list[dict[str, Any]] = []
    timestamp = start + timedelta(days=1)
    delivery_end = end + timedelta(days=1)
    issue_times = ("03:30", "05:30", "08:30", "10:30", "12:30", "16:30", "19:30", "23:30")

    while timestamp < delivery_end:
        day_phase = (timestamp.hour * 60 + timestamp.minute) / (24 * 60) * 2 * math.pi
        seasonal_phase = (timestamp.timetuple().tm_yday / 20) * 2 * math.pi
        base = 9200 + 2200 * math.sin(day_phase - 0.75) + 1700 * math.sin(seasonal_phase)
        actual_rng = random.Random(_seed(month, timestamp.isoformat(), "actual"))
        actual = max(900.0, base + actual_rng.gauss(0, 720))
        actuals.append({
            "fuelType": "WIND",
            "startTime": _iso(timestamp),
            "settlementDate": timestamp.date().isoformat(),
            "settlementPeriod": timestamp.hour * 2 + (2 if timestamp.minute else 1),
            "generation": round(actual, 2),
            "publishTime": _iso(timestamp + timedelta(hours=1)),
        })

        # WINDFOR's national wind forecast series is hourly. Each hourly target
        # receives revisions at the documented issue times; cutoff selection picks
        # the newest publication that was available by D-1 cutoff.
        if timestamp.minute == 0:
            for issue_index, issue_time in enumerate(issue_times):
                delivery_day = timestamp.date()
                hh, mm = (int(x) for x in issue_time.split(":"))
                publication = datetime.combine(delivery_day - timedelta(days=1), datetime.min.time(), tzinfo=UTC).replace(hour=hh, minute=mm)
                issue_rng = random.Random(_seed(month, timestamp.isoformat(), str(issue_index)))
                noise = issue_rng.gauss(0, 1450 - issue_index * 95)
                forecast_value = max(500.0, base + noise + 160)
                forecasts.append({
                    "startTime": _iso(timestamp),
                    "publishTime": _iso(publication),
                    "settlementDate": delivery_day.isoformat(),
                    "settlementPeriod": timestamp.hour + 1,
                    "generation": round(forecast_value, 2),
                })
        timestamp += timedelta(minutes=30)
    return actuals, forecasts
