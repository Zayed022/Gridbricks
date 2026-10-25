# Wind Intelligence — Grid Bricks Full Stack SWE Challenge

A decision-time wind forecast analysis dashboard for the UK electricity market. The app compares Elexon wind generation forecasts available before a configurable day-ahead decision cutoff with observed wind generation, then quantifies forecast error.

> **Scope note:** the brief permits only national wind actuals from `FUELHH` and wind forecasts from `WINDFOR`. This pilot therefore measures wind forecast uncertainty; it does **not** calculate total electricity demand, net system position, or the amount of additional power a utility must buy. Those require datasets explicitly excluded from the assignment.

## Stack

- **Frontend:** React 19, Vite, Recharts, Lucide icons
- **Backend:** Python 3.12, FastAPI, HTTPX
- **Data:** Elexon BMRS / Insights API — `FUELHH` and `WINDFOR`
- **Testing:** pytest
- **Containerisation:** Docker and Docker Compose
- **Deployment target:** Vercel for the React static frontend and Render for the FastAPI API (or deploy the API on another Python-capable host)

AI tools were used: OpenAI ChatGPT assisted with application scaffolding, implementation, and test/documentation drafting. The Elexon API is the intended source of live data. Synthetic demo values are generated locally only to preview the interface when live API access is unavailable; they are labelled in the UI and must not be interpreted as market observations.

## Features

- January 2026 historical replay by purchase-decision date, mapped to the following delivery day
- Configurable decision cutoff time in UTC (default `10:00` on D−1; an explicit modelling assumption because the brief does not define the exact cutoff)
- As-of forecast selection: for each hourly target, select the latest `WINDFOR` version whose `publishTime` is at or before the selected D−1 cutoff
- `FUELHH` filtered to `fuelType = WIND`; latest published actual revision selected for each half-hour `startTime`
- Forecast/actual month profile and selected-day hourly comparison
- MAE, RMSE, WAPE, signed forecast bias, and interval coverage
- Hourly forecast audit table and CSV export, including the two source actual values used for each hourly average
- Data provenance, methodology, assumption notes, and visibly labelled synthetic demo mode
- API response and upstream failures surfaced rather than silently treating missing data as zero

## Repository structure

```text
.
├── backend/
│   ├── app/
│   │   ├── analytics.py       # timestamp parsing, as-of joins, metrics, dashboard response
│   │   ├── data_source.py     # Elexon client, in-memory cache, synthetic demo data
│   │   └── main.py            # FastAPI endpoints, validation, error handling, CORS
│   ├── tests/
│   │   └── test_analytics.py  # known metric values, cutoff logic, actual revision logic, demo contract
│   ├── Dockerfile
│   └── requirements.txt
├── frontend/
│   ├── src/
│   │   ├── App.jsx            # dashboard and interactions
│   │   ├── main.jsx           # React entrypoint
│   │   └── styles.css         # responsive dashboard styling
│   ├── Dockerfile
│   ├── index.html
│   ├── package.json
│   └── vite.config.js
├── docker-compose.yml
├── render.yaml
├── vercel.json
└── README.md
```

## Run locally with Docker (recommended)

Prerequisites: Docker Desktop / Docker Engine with the Compose plugin.

From the repository root:

```bash
docker compose up --build
```

Then open:

- Frontend: <http://localhost:5173>
- API health: <http://localhost:8000/api/health>
- Interactive API docs: <http://localhost:8000/docs>

The UI attempts to load live Elexon data first. If the API is unreachable or returns an error, the page displays a warning and loads clearly labelled synthetic demo data so that the rest of the UX remains reviewable. Use **Retry live data** to attempt the real source again.

## Run without Docker

### Backend

```bash
cd backend
python -m venv .venv
# macOS/Linux
source .venv/bin/activate
# Windows PowerShell: .venv\Scripts\Activate.ps1
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```

### Frontend (new terminal)

```bash
cd frontend
npm install
npm run dev
```

The Vite development server proxies `/api` calls to `http://localhost:8000`. To use another API URL in a production build, set `VITE_API_BASE_URL` before running `npm run build`.

## Tests

From the backend directory:

```bash
pip install -r requirements-dev.txt
pytest -q
```

The tests are deterministic and do not need outbound network access. They cover metric calculations, selecting the latest forecast revision available by the cutoff, latest actual revision selection, the January decision-to-delivery boundary (Jan 2–Feb 1), hourly forecast selection, conversion of paired half-hour actuals to hourly averages, incomplete-hour handling, and full 744-hour coverage in deterministic fixtures. A live integration check should also be run in the deployment environment before submission.

## API endpoints

### `GET /api/health`

Health check for local tooling and hosting platform probes.

### `GET /api/metadata`

Describes the datasets and metric definitions.

### `GET /api/dashboard`

The `month` and `selected_date` parameters refer to the **decision month/date**. For a decision on 2026-01-31, the dashboard compares eligible forecasts and actual generation for delivery on 2026-02-01. January decisions therefore cover delivery dates January 2 through February 1 (31 days × 24 hourly forecast periods = 744 expected comparison intervals). FUELHH provides 1,488 half-hour actual observations over that delivery window; each hourly actual is the arithmetic mean of the two half-hour MW observations in the hour. An hour is incomplete if either half-hour actual is missing.

Query parameters:

| Parameter | Default | Description |
|---|---|---|
| `month` | `2026-01` | Purchase-decision month (`YYYY-MM`) |
| `selected_date` | `2026-01-15` | A decision date within the month; delivery is the following day |
| `cutoff_time` | `10:00` | Forecast publication cutoff in UTC (`HH:MM`) on D−1 |
| `mode` | `live` | `live` uses Elexon; `demo` uses synthetic data |

Example (demo mode):

```text
http://localhost:8000/api/dashboard?month=2026-01&selected_date=2026-01-15&cutoff_time=10:00&mode=demo
```

## Data source and methodology

- Actuals: [Elexon FUELHH stream documentation](https://bmrs.elexon.co.uk/api-documentation/endpoint/datasets/FUELHH/stream). The request uses the delivery date range January 2 through February 1 for the January decision month, rows are filtered to `fuelType = WIND`, and the latest published record for each target `startTime` is used.
- Forecasts: [Elexon WINDFOR stream documentation](https://bmrs.elexon.co.uk/api-documentation/endpoint/datasets/WINDFOR/stream). The request retrieves forecast publications around the decision window and its following delivery days, including historical revisions needed for D−1 as-of selection. Elexon describes its national wind forecast as hourly ([Wind Generation overview](https://bmrs.elexon.co.uk/wind-generation)). `startTime` identifies the target hour; `publishTime` identifies when that forecast revision became available. Revisions published after the selected D−1 cutoff are excluded to prevent look-ahead bias.
- Monthly chart averages and accuracy metrics use only matched hourly pairs. Each hourly actual is the average of the corresponding `FUELHH` observations at HH:00 and HH:30, and both half-hours are required. Coverage is therefore measured against 744 expected hours, not 1,488 half-hours. `forecast_diagnostics` reports hourly target coverage, raw target timestamps on the hour and half-hour, publication-cutoff eligibility, and actual half-hour completeness.
- Elexon developer portal states the Insights APIs are public and do not require an API key: [Developer portal](https://developer.data.elexon.co.uk/).
- The source timestamps are normalised to UTC. The UI states UTC explicitly because the day-ahead cutoff is a configurable operational assumption, not a sourced trading rule.
- Forecast error is `forecast_mw - actual_mw`. Positive error means wind generation was over-forecast; negative error means it was under-forecast.
- MAE is the mean absolute error in MW. RMSE is the square root of mean squared error in MW. WAPE is `sum(abs(error)) / sum(actual) * 100`. Bias is the mean signed error.
- Metrics include only time intervals where both a selected forecast and actual are present. Coverage is reported separately; missing intervals are not silently imputed.

## Deployment guide

This repository includes deployment configuration, but deployment requires your own hosting accounts and project URLs/secrets. Do not submit a placeholder URL as if it were deployed.

### 1. Deploy the FastAPI backend to Render

1. Push the repository to a GitHub repository.
2. In Render, create a **Blueprint** from that repository or create a Docker web service using `backend/Dockerfile` and `backend` as the Docker context.
3. Confirm `/api/health` is the health-check path. The `render.yaml` file provides the baseline service configuration.
4. Once deployed, open `https://<your-api-service>.onrender.com/api/health` and `/docs`.
5. Set `FRONTEND_ORIGINS` to the exact Vercel origin once the frontend URL exists. For a short public read-only demo the wildcard default is convenient, but narrowing this origin is preferable before sharing widely.

### 2. Deploy the React frontend to Vercel

1. Import the same GitHub repository in Vercel.
2. Set the **Root Directory** to `frontend`.
3. Use build command `npm run build` and output directory `dist` (also in `vercel.json`).
4. Add environment variable `VITE_API_BASE_URL=https://<your-api-service>.onrender.com` and redeploy.
5. Open the Vercel URL in a private/incognito window. Verify the page, API requests, live/demo indicator, selected-date interaction, cutoff change, CSV export, and console/network panel.
6. Update the backend's `FRONTEND_ORIGINS` to the Vercel URL and redeploy the backend if you have narrowed CORS.

### 3. Final submission checklist

- Public GitHub repository; use an anonymous/random repository name as requested by the brief.
- README links to the **real** deployed application URL; add it once deployment succeeds.
- Test `/api/health` and the dashboard in an incognito window.
- Verify that the demo label appears when demo mode is used and that live mode only shows Elexon-derived values.
- Record and upload one **unlisted YouTube video** no longer than 10 minutes: a customer product walkthrough (maximum 5 minutes) followed by a technical review walkthrough (maximum 5 minutes).
- Submit the repository and deployed application links using the form named in the assignment.

## Suggested 10-minute video outline

### Part 1 — Customer overview (0:00–5:00)

- **0:00–0:30:** The decision problem and why forecast publication time matters.
- **0:30–1:30:** Show January 2026 and explain the configurable D−1 cutoff.
- **1:30–2:30:** Explain MAE, RMSE, WAPE, and signed bias in operational terms.
- **2:30–3:45:** Select a delivery day, inspect the 24 hourly periods, and export the ledger.
- **3:45–4:30:** Explain missing-period coverage and the source indicator.
- **4:30–5:00:** State the limitation: wind error is not total procurement shortfall without demand/system data.

### Part 2 — Technical lead handoff (5:00–10:00)

- **5:00–6:00:** Architecture: React/Vite → FastAPI → Elexon; Docker Compose for local parity.
- **6:00–7:00:** Walk through the Elexon client, timestamp normalisation, and in-memory TTL cache.
- **7:00–8:00:** Explain as-of selection by `publishTime` relative to D−1 cutoff and latest actual revision selection.
- **8:00–9:00:** Explain metric definitions, missing-data coverage, and test cases.
- **9:00–9:40:** Show API docs, Docker run, and deployment configuration.
- **9:40–10:00:** Call out assumptions, data limitations, and production hardening steps.

## Production hardening opportunities

- Persist raw API responses and normalised interval data in a database/object store for reproducibility and faster re-analysis.
- Add schema contract tests against captured Elexon fixtures and scheduled upstream health checks.
- Add structured logs, request IDs, basic API rate limiting, and cache observability.
- Replace the in-process cache with Redis or persistent storage for multi-instance deployments.
- Agree and document the official market decision cutoff with the client before using results for operational decisions.
