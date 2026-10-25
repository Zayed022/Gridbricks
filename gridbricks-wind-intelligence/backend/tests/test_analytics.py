from datetime import datetime, timezone
import math

from app.analytics import (
    aggregate_actuals_hourly,
    build_dashboard,
    compute_metrics,
    forecast_diagnostics,
    parse_dt,
    select_actuals,
    select_forecasts,
)
from app.data_source import generate_demo_month


def test_metrics_known_values():
    metrics = compute_metrics([(110.0, 100.0), (80.0, 100.0)])
    assert metrics["matched_intervals"] == 2
    assert metrics["mae_mw"] == 15
    assert metrics["rmse_mw"] == math.sqrt(250)
    assert metrics["wape_pct"] == 15
    assert metrics["bias_mw"] == -5


def test_forecast_snapshot_selects_latest_publication_before_cutoff():
    rows = [
        {"startTime": "2026-01-02T00:00:00Z", "publishTime": "2026-01-01T05:30:00Z", "generation": 80},
        {"startTime": "2026-01-02T00:00:00Z", "publishTime": "2026-01-01T08:30:00Z", "generation": 90},
        {"startTime": "2026-01-02T00:00:00Z", "publishTime": "2026-01-01T10:30:00Z", "generation": 95},
    ]
    selected = select_forecasts(rows, "2026-01", "10:00")
    target = datetime(2026, 1, 2, tzinfo=timezone.utc)
    assert selected[target]["generation"] == 90


def test_actual_filters_non_wind_and_uses_latest_revision():
    rows = [
        {"fuelType": "WIND", "startTime": "2026-01-02T00:00:00Z", "publishTime": "2026-01-02T01:00:00Z", "generation": 100},
        {"fuelType": "WIND", "startTime": "2026-01-02T00:00:00Z", "publishTime": "2026-01-02T02:00:00Z", "generation": 110},
        {"fuelType": "GAS", "startTime": "2026-01-02T00:00:00Z", "publishTime": "2026-01-02T02:30:00Z", "generation": 999},
    ]
    selected = select_actuals(rows, "2026-01")
    assert len(selected) == 1
    assert next(iter(selected.values()))["generation"] == 110


def test_hourly_actual_is_average_of_two_half_hour_records():
    rows = [
        {"fuelType": "WIND", "startTime": "2026-01-02T00:00:00Z", "generation": 100},
        {"fuelType": "WIND", "startTime": "2026-01-02T00:30:00Z", "generation": 120},
    ]
    actuals = select_actuals(rows, "2026-01")
    hourly = aggregate_actuals_hourly(actuals, "2026-01")
    target = datetime(2026, 1, 2, tzinfo=timezone.utc)
    assert hourly[target]["generation"] == 110
    assert hourly[target]["half_hour_count"] == 2
    assert hourly[target]["complete"] is True


def test_incomplete_hourly_actual_is_not_imputed():
    rows = [{"fuelType": "WIND", "startTime": "2026-01-02T00:00:00Z", "generation": 100}]
    actuals = select_actuals(rows, "2026-01")
    hourly = aggregate_actuals_hourly(actuals, "2026-01")
    target = datetime(2026, 1, 2, tzinfo=timezone.utc)
    assert hourly[target]["generation"] is None
    assert hourly[target]["half_hour_count"] == 1
    assert hourly[target]["complete"] is False


def test_demo_payload_uses_half_hour_actuals_hourly_forecasts_and_full_hourly_coverage():
    actuals, forecasts = generate_demo_month("2026-01")
    data = build_dashboard(
        month="2026-01", actual_records=actuals, forecast_records=forecasts,
        cutoff_time="10:00", selected_date="2026-01-15", source_mode="demo",
    )
    assert len(actuals) == 31 * 48
    # 31 days x 24 hourly targets x 8 revisions per target
    assert len(forecasts) == 31 * 24 * 8
    assert data["source_mode"] == "demo"
    assert "Synthetic" in data["source_name"]
    assert data["metrics"]["matched_intervals"] == 31 * 24
    assert data["coverage"]["expected_intervals"] == 31 * 24
    assert data["coverage"]["actual_half_hour_intervals"] == 31 * 48
    assert data["coverage"]["matched_pct_of_month"] == 100.0
    assert data["coverage"]["is_complete"] is True
    assert len(data["intraday"]) == 24


def test_january_decision_window_maps_to_jan_2_through_feb_1_delivery():
    actuals, forecasts = generate_demo_month("2026-01")
    data = build_dashboard(
        month="2026-01", actual_records=actuals, forecast_records=forecasts,
        cutoff_time="10:00", selected_date="2026-01-31", source_mode="demo",
    )
    assert data["decision_window"] == {
        "decision_start": "2026-01-01",
        "decision_end": "2026-01-31",
        "delivery_start": "2026-01-02",
        "delivery_end": "2026-02-01",
    }
    assert data["selected_decision_date"] == "2026-01-31"
    assert data["selected_delivery_date"] == "2026-02-01"
    assert len(data["daily_series"]) == 31
    assert data["daily_series"][0]["date"] == "2026-01-02"
    assert data["daily_series"][-1]["date"] == "2026-02-01"
    assert len(data["intraday"]) == 24
    assert data["intraday"][0]["timestamp"] == "2026-02-01T00:00:00Z"


def test_decision_window_excludes_delivery_targets_outside_jan2_feb1():
    actuals = [
        {"fuelType": "WIND", "startTime": "2026-01-01T00:00:00Z", "generation": 10},
        {"fuelType": "WIND", "startTime": "2026-01-01T00:30:00Z", "generation": 10},
        {"fuelType": "WIND", "startTime": "2026-01-02T00:00:00Z", "generation": 20},
        {"fuelType": "WIND", "startTime": "2026-01-02T00:30:00Z", "generation": 22},
        {"fuelType": "WIND", "startTime": "2026-02-01T00:00:00Z", "generation": 30},
        {"fuelType": "WIND", "startTime": "2026-02-01T00:30:00Z", "generation": 32},
        {"fuelType": "WIND", "startTime": "2026-02-02T00:00:00Z", "generation": 40},
        {"fuelType": "WIND", "startTime": "2026-02-02T00:30:00Z", "generation": 42},
    ]
    forecasts = [
        {"startTime": "2026-01-01T00:00:00Z", "publishTime": "2025-12-31T09:00:00Z", "generation": 11},
        {"startTime": "2026-01-02T00:00:00Z", "publishTime": "2026-01-01T09:00:00Z", "generation": 21},
        {"startTime": "2026-02-01T00:00:00Z", "publishTime": "2026-01-31T09:00:00Z", "generation": 31},
        {"startTime": "2026-02-02T00:00:00Z", "publishTime": "2026-02-01T09:00:00Z", "generation": 41},
    ]
    data = build_dashboard(
        month="2026-01", actual_records=actuals, forecast_records=forecasts,
        cutoff_time="10:00", selected_date="2026-01-31", source_mode="demo",
    )
    assert data["coverage"]["actual_half_hour_intervals"] == 4
    assert data["coverage"]["forecast_intervals_at_cutoff"] == 2
    assert data["coverage"]["matched_intervals"] == 2
    feb1_midnight = next(row for row in data["intraday"] if row["timestamp"] == "2026-02-01T00:00:00Z")
    assert feb1_midnight["actual_mw"] == 31
    assert feb1_midnight["forecast_mw"] == 31
    assert all(row["timestamp"][:10] == "2026-02-01" for row in data["intraday"])


def test_hourly_forecasts_are_compared_to_hourly_averaged_actuals_not_reported_as_50pct_coverage():
    actuals, forecasts = generate_demo_month("2026-01")
    data = build_dashboard(
        month="2026-01", actual_records=actuals, forecast_records=forecasts,
        cutoff_time="10:00", selected_date="2026-01-31", source_mode="demo",
    )
    assert data["coverage"]["expected_intervals"] == 31 * 24
    assert data["coverage"]["actual_intervals"] == 31 * 24
    assert data["coverage"]["actual_half_hour_intervals"] == 31 * 48
    assert data["coverage"]["forecast_intervals_at_cutoff"] == 31 * 24
    assert data["coverage"]["matched_intervals"] == 31 * 24
    assert data["coverage"]["matched_pct_of_month"] == 100.0
    assert data["coverage"]["is_complete"] is True
    assert data["forecast_diagnostics"]["raw_targets_on_hour"] == 31 * 24
    assert data["forecast_diagnostics"]["raw_targets_on_half_hour"] == 0
    assert len(data["intraday"]) == 24
    assert data["metrics"]["matched_intervals"] == 31 * 24


def test_forecast_diagnostics_distinguish_missing_raw_hourly_rows_from_late_publications():
    rows = [
        {"startTime": "2026-01-02T00:00:00Z", "publishTime": "2026-01-01T09:00:00Z", "generation": 100},
        {"startTime": "2026-01-02T01:00:00Z", "publishTime": "2026-01-01T10:30:00Z", "generation": 90},
    ]
    selected = select_forecasts(rows, "2026-01", "10:00")
    diagnostic = forecast_diagnostics(rows, "2026-01", "10:00", selected)
    assert diagnostic["distinct_raw_forecast_targets"] == 2
    assert diagnostic["raw_targets_on_hour"] == 2
    assert diagnostic["raw_targets_on_half_hour"] == 0
    assert diagnostic["eligible_targets_on_hour"] == 1
    assert diagnostic["raw_hourly_targets_without_eligible_revision"] == 1
    assert diagnostic["expected_targets_with_no_raw_record"] == 31 * 24 - 2


def test_forecasts_published_after_decision_cutoff_are_excluded():
    rows = [
        {"startTime": "2026-02-01T00:00:00Z", "publishTime": "2026-01-31T09:00:00Z", "generation": 100},
        {"startTime": "2026-02-01T00:00:00Z", "publishTime": "2026-01-31T10:30:00Z", "generation": 150},
    ]
    selected = select_forecasts(rows, "2026-01", "10:00")
    assert next(iter(selected.values()))["generation"] == 100
    assert next(iter(selected.values()))["publish"].isoformat() == "2026-01-31T09:00:00+00:00"


from fastapi.testclient import TestClient
from app.main import app


def test_health_endpoint():
    response = TestClient(app).get("/api/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_demo_dashboard_api_contract():
    response = TestClient(app).get("/api/dashboard", params={
        "month": "2026-01", "selected_date": "2026-01-15",
        "cutoff_time": "10:00", "mode": "demo"
    })
    assert response.status_code == 200
    body = response.json()
    assert body["source_mode"] == "demo"
    assert len(body["daily_series"]) == 31
    assert len(body["intraday"]) == 24
    assert body["metrics"]["matched_intervals"] == 31 * 24
    assert body["coverage"]["matched_pct_of_month"] == 100.0


def test_dashboard_rejects_selected_date_outside_month():
    response = TestClient(app).get("/api/dashboard", params={
        "month": "2026-02", "selected_date": "2026-01-15", "mode": "demo"
    })
    assert response.status_code == 422


def test_actual_revision_number_breaks_tie_when_publish_time_is_missing():
    rows = [
        {"fuelType": "WIND", "startTime": "2026-01-02T00:00:00Z", "generation": 100, "documentRevisionNumber": 1},
        {"fuelType": "WIND", "startTime": "2026-01-02T00:00:00Z", "generation": 115, "documentRevisionNumber": 3},
    ]
    selected = select_actuals(rows, "2026-01")
    assert next(iter(selected.values()))["generation"] == 115
