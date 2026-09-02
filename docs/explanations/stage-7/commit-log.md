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

## Stage 7 component 4: charter creation flow -- real correction round-trip verified live

`MandatePage.tsx` rewritten from Component 3's stub into a real three-phase
flow (entry / reviewing / confirmed) driving Component 2's create/correct/
confirm endpoints; new `CharterSummary.tsx` (the parsed-charter display,
reused between the reviewing and confirmed states); new form/button
primitives in `fathom.css`. The two-round correction cap is surfaced
directly in the UI (a live "round N of 2" caption, and the correction
button replaced by explanatory text once exhausted, not just hidden), and
the confirmed-state panel explicitly names the boundary between
correction (pre-confirmation, just used) and Step 7's not-yet-built
redirection feature, per direct instruction not to leave that implicit.
Also corrected imprecise phrasing this session flagged in two pre-existing
docs (`agentic_core/charter.py`'s `MAX_CORRECTION_ROUNDS` comment and
`step-02`'s own interview Q&A): a correction round is not "the same call
invoked again" -- the prompt differs every round, what's actually
identical is the schema-validated pipeline it runs through. No backend
changes. Live verification with the real backend and LLM reproduced
step-02's own AAPL -> [NVDA, MSFT] correction result by clicking through
the UI rather than curl, plus confirm, reset, and both themes.
`npm run build`/`lint` clean. Full design writeup: `docs/explanations/
stage-7/step-04-charter-creation-flow.md`.

## Stage 7 component 4 follow-up: cap-exhausted correction path verified live

The first verification pass explicitly disclosed the correction-cap-
exhausted state (round 2 of 2) as inspected-only, not live-driven -- asked
to close that gap before the component is considered done. Drove a fresh
charter through two real corrections (energy/seasonality mandate ->
widen the universe -> switch scoring preference), confirming at each step
via the raw `POST .../correct` response body, not just the rendered UI,
that the server agreed: `correction_round: 2`, `blocked: false`,
`parent_charter_id` chained correctly, the scoring-preference change
genuinely applied. The UI correctly replaced the correction button with
"No corrections remain" text instead of hiding it, and confirming from
that exact state succeeded (`POST .../confirm` -> 200, targeting the
round-2 charter's own id). `docs/explanations/stage-7/
step-04-charter-creation-flow.md` updated to reflect this as verified
rather than disclosed-as-untested. No code changes -- verification only.

## Stage 7 component 5: research log with the status-poll reveal -- two live bugs found and fixed

New `useStudyRunPoll.ts` (a setTimeout-chained poll of `GET /study-runs/{id}`,
three outcomes not two -- running/awaiting-verdict/resolved-or-failed), new
`VerdictCard.tsx` (narrative/claims/caveats + a count-up on
`corrected_significance_threshold`), new `HypothesisRow.tsx`,
`CharterDetailPage.tsx` rewritten from Component 3's stub. Found two real,
undocumented backend gaps by reading `loop_graph.py`/`verdict.py`:
`render_verdict` is never called automatically when a loop finishes (a
currently-manual script), and a *failed* run leaves its hypothesis
permanently stuck at `status='testing'` with no code path off it -- designed
around both explicitly rather than silently assuming `completed` always means
"verdict ready."

Live testing found and fixed two real bugs, not just confirmed the happy
path: the count-up animation got permanently stuck at 0 because
`requestAnimationFrame` is fully suspended (not just throttled) for a hidden
tab -- proven with a bare rAF loop that timed out after 45 real seconds
without firing once -- fixed with a `setTimeout` backstop, reverified against
the real API value (0.0250). Separately, seeding a synthetic `testing`
hypothesis directly in Postgres and flipping its study run to `completed`
with a real verdict attached *while the page stayed open* surfaced a stale-
prop bug: the row's `isTesting` was derived from a hypothesis prop the parent
never refetches, so the "awaiting verdict" trace-card kept rendering
alongside the freshly-revealed verdict card. Fixed with
`effectiveStatus = verdict?.status ?? hypothesis.status` (verdict.status is
authoritative once known -- `render_verdict` writes the identical value to
both rows in one operation). Both fixes reverified live afterward; all
synthetic Postgres rows deleted and their absence reconfirmed.
`npm run build`/`lint` clean throughout. Full design writeup:
`docs/explanations/stage-7/step-05-research-log-status-poll.md`.
