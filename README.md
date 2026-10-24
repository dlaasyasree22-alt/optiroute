---
title: OptiRoute
sdk: docker
app_port: 7860
---

# OptiRoute

Risk-aware weekly distribution planner for FMCG networks (TechTards, Problem Statement 2).

## Run locally

```
pip install -r requirements.txt
uvicorn app:app --port 8000
```

Open http://localhost:8000. The first plan takes about 10 seconds (forecast models train once per process).

## Deploy (Hugging Face Spaces)

Create a new Space with SDK "Docker", push this repository to it. The front matter above sets the port.

## Layout

- `app.py` FastAPI API and static web app server
- `frontend/` web app (plain JavaScript modules + anime.js, no build step)
- `frontend/recorded/` a saved real engine run, replayed if the engine is unreachable (shown with a banner)
- `ml/` data generator, LightGBM forecaster, Monte Carlo stress test
- `solver/` HiGHS MILP optimiser, FEFO repair, why-not engine, A to D backtest (`python solver/backtest.py` refreshes `data/backtest.json`)

## API

- `POST /api/v1/plan/generate` and `/plan/generate/stream` (server-sent stage events)
- `POST /api/v1/plan/counterfactual`
- `GET /api/v1/plan/{id}/risk`, `POST /api/v1/plan/{id}/approve`
- `GET /api/v1/backtest`, `GET /api/v1/meta`, `GET /api/v1/health`
