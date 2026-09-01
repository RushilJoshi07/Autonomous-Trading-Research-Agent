"""FastAPI app -- Stage 7, Component 1. The first transport over the backend
built in Stages 1-6 (agentic_core, backtester, data_pipeline). Every route
mounted here is a read; charter creation/confirmation, the only writes, are
Component 2, not this file.

Run locally: PYTHONPATH=src .venv/bin/uvicorn api.app:app --reload
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.routers import charters, hypotheses, scoreboard, study_runs, verdicts

app = FastAPI(title="Agentic Finance Platform API")

# Component 3 (Vite + React scaffold, docs/plans/stage-7-plan.md) doesn't
# exist yet, so there is no real frontend origin to confirm this against --
# these are Vite's own default dev-server addresses, an educated guess, not
# a verified value. The first time Component 3's frontend makes a real
# request here, check the browser's actual origin against this list and fix
# it immediately if it's wrong, rather than letting a mismatch surface later
# as an unexplained blocked request.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(charters.router)
app.include_router(hypotheses.router)
app.include_router(study_runs.router)
app.include_router(verdicts.router)
app.include_router(scoreboard.router)
