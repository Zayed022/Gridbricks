from __future__ import annotations

from datetime import datetime, timezone
import os
import logging

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

from .analytics import build_dashboard, month_bounds
from .data_source import ElexonFetchError, fetch_month, generate_demo_month

APP_VERSION = "1.1.0"
logger = logging.getLogger("gridbricks.wind_intelligence")
app = FastAPI(
    title="Grid Bricks Wind Intelligence API",
    version=APP_VERSION,
    description="Decision-time wind forecast analysis using Elexon FUELHH actuals and WINDFOR forecast revisions.",
)
origins = [x.strip() for x in os.getenv("FRONTEND_ORIGINS", "*").split(",") if x.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=False,
    allow_methods=["GET", "OPTIONS"],
    allow_headers=["*"],
)


def validate_month(month: str) -> None:
    try:
        month_bounds(month)
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=422, detail="month must be a valid YYYY-MM value") from exc
    if len(month) != 7 or month[4] != "-":
        raise HTTPException(status_code=422, detail="month must be a valid YYYY-MM value")


@app.get("/api/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "service": "gridbricks-wind-intelligence", "version": APP_VERSION}


@app.get("/api/metadata")
async def metadata() -> dict:
    return {
        "name": "Grid Bricks Wind Intelligence",
        "version": APP_VERSION,
        "default_month": "2026-01",
        "default_cutoff_utc": "10:00",
        "window_semantics": "decision dates in the selected month; delivery dates are the following calendar days; comparison is hourly",
        "datasets": [
            {"code": "FUELHH", "role": "Actual half-hourly generation by fuel type", "filter": "fuelType = WIND; delivery dates are decision date + 1 day", "url": "https://bmrs.elexon.co.uk/api-documentation/endpoint/datasets/FUELHH/stream"},
            {"code": "WINDFOR", "role": "Hourly wind generation forecasts, including publication time revisions", "url": "https://bmrs.elexon.co.uk/api-documentation/endpoint/datasets/WINDFOR/stream"},
        ],
        "metrics": {
            "comparison_resolution": "Hourly WINDFOR forecasts compared with hourly averages of paired half-hour FUELHH actuals",
            "mae_mw": "Mean absolute error in MW",
            "rmse_mw": "Root mean squared error in MW; penalises larger misses more heavily",
            "wape_pct": "Sum of absolute errors / sum of actual generation × 100",
            "bias_mw": "Mean (forecast − actual); positive means over-forecast",
        },
    }


@app.get("/api/dashboard")
async def dashboard(
    month: str = Query(default="2026-01", pattern=r"^\d{4}-(0[1-9]|1[0-2])$"),
    selected_date: str = Query(default="2026-01-15", pattern=r"^\d{4}-\d{2}-\d{2}$"),
    cutoff_time: str = Query(default="10:00", pattern=r"^([01]\d|2[0-3]):[0-5]\d$"),
    mode: str = Query(default="live", pattern=r"^(live|demo)$"),
) -> dict:
    validate_month(month)
    try:
        chosen_date = datetime.strptime(selected_date, "%Y-%m-%d").date()
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="selected_date must be YYYY-MM-DD") from exc
    if chosen_date.strftime("%Y-%m") != month:
        raise HTTPException(status_code=422, detail="selected_date must fall inside the requested month")

    try:
        if mode == "demo":
            actuals, forecasts = generate_demo_month(month)
        else:
            actuals, forecasts = await fetch_month(month)
        result = build_dashboard(
            month=month,
            actual_records=actuals,
            forecast_records=forecasts,
            cutoff_time=cutoff_time,
            selected_date=selected_date,
            source_mode=mode,
            fetched_at=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        )
        if mode == "live" and result["coverage"]["matched_intervals"] == 0:
            raise ElexonFetchError("Elexon responded, but no actual/forecast pairs matched the selected month and D−1 cutoff. Review the returned field names and query filters before relying on the result.")
        logger.info(
            "dashboard_ready month=%s mode=%s matched=%s coverage_pct=%s",
            month, mode, result["coverage"]["matched_intervals"], result["coverage"]["matched_pct_of_month"],
        )
        return result
    except ElexonFetchError as exc:
        raise HTTPException(
            status_code=502,
            detail={
                "message": "Could not load live Elexon data.",
                "reason": str(exc),
                "next_step": "Retry in a moment or switch to the clearly labelled synthetic demo dataset. Do not treat demo values as market data.",
            },
        ) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:  # Keep unexpected upstream/data-shape errors visible to monitoring.
        raise HTTPException(status_code=500, detail="Unexpected error while preparing the dashboard. Check backend logs.") from exc
