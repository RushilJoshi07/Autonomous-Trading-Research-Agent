# Stage 7 — commit log

## 311adf7 — Stage 7 component 1: FastAPI read endpoints — 18/18 live pass

`src/api/` — five GET-only routers (charters, hypotheses, study_runs
+/traces, verdicts, scoreboard) over the existing SQLAlchemy models, plus
a `get_db` FastAPI dependency and thin response models. Reuse vs. new was
decided per row by reading the actual persistence code, not assumed.
18 new tests (358/358 full suite), plus live verification against real
dev-database data. Full design writeup: `docs/explanations/stage-7/
step-01-fastapi-read-endpoints.md`.

## Stage 7 component 2: charter create/confirm/correct — real LLM verified live

`POST /charters`, `POST /charters/{id}/confirm`, and a new
`POST /charters/{id}/correct` -- plain-language corrections re-invoke
`parse_charter` against a combined prompt (original + restated
interpretation + correction), capped at 2 rounds, one immutable DB row per
attempt (new `parent_charter_id`/`correction_round`/`correction_text`
columns + migration). Also fixed two real, pre-existing gaps in
`confirm_charter`: no not-found guard, no guard against confirming an
empty-universe charter -- both previously only enforced by
`scripts/set_charter.py`'s own control flow, never by the function itself.
19 new tests (377/377 full suite), plus live verification with the real
LLM: a real correction genuinely widened a resolved universe from
`[AAPL]` to `[NVDA, MSFT]`. Full design writeup: `docs/explanations/
stage-7/step-02-charter-confirm-correct.md`.

## Stage 7 component 3: frontend app shell — CORS guess confirmed live

New `frontend/` (Vite + React + TypeScript). Fathom's real tokens ported
from the published design-language artifact (read in full, not just the
plan's summary -- its frame-runtime JS was deliberately left out, only
the design-system CSS was ported). A `useTheme` hook supplies the
dark-first/`prefers-color-scheme`/explicit-override cascade the artifact
itself doesn't need to (its host manages that for it; a standalone app
has no such host). The API client is *generated* (`openapi-typescript` +
`openapi-fetch`) from the backend's live `/openapi.json`, not
hand-typed, so it cannot silently drift from the real Pydantic shapes --
committed like a lockfile, per your call. Found and fixed a real
`openapi-typescript`/TypeScript-6 peer-dependency conflict by pinning to
`5.9.3` rather than forcing past it. Live verification against the real
backend confirmed the Components 1-2 CORS guess (`localhost:5173`) was
correct on the first real cross-origin request, plus dynamic routing and
both theme-override and system-preference paths. Full design writeup:
`docs/explanations/stage-7/step-03-frontend-app-shell.md`.
