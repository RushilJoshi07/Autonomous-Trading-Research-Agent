# Stage 7 — The Frontend (top-level plan)

## Context

Stages 1–6 built a complete, gated backend: data pipeline, backtester,
strategy schema, MCP tools, the agentic core (Sacred Gate 2, proven live),
and the evaluation harness (Sacred Gate 2's own gate, proven live). None
of it has ever been touched through anything but the terminal —
`scripts/set_charter.py`'s own docstring says so directly: *"the terminal
stands in for Stage 7's not-yet-built FastAPI/React confirmation flow."*
Confirmed by direct search: there is no FastAPI app anywhere in `src/`,
`fastapi`/`uvicorn` aren't even in `pyproject.toml`, and there is no
frontend directory. Stage 7 builds both, from zero.

Stage 7's own gate, per `docs/architecture.md`: **a stranger can use it
unexplained.** That means a real entry point (setting a mandate,
confirming a charter), not just a read-only dashboard over what the
backend already produced — architecture.md's own Step 1 makes the charter
confirmation moment the literal front door of the whole system.

The design language (**Fathom** — true black/white surfaces, a single
Bloomberg-terminal amber accent, a CVD-validated verdict-status palette,
three type voices: Fraunces/Archivo/IBM Plex Mono) is already approved
and published. This plan is the component build order that applies it.

**One real gap confirmed before writing this plan, not discovered later:**
`agentic_core.db.models.ScoreboardEntry` is defined but no code anywhere
in this project writes one yet (confirmed by direct search — the
scheduled decay-recheck job that would populate it is Stage 8's own,
unbuilt work). The scoreboard endpoint (Build order, step 7) has to be
designed around that honestly: it derives its view from `Hypothesis.status`
and each hypothesis's own `Verdict` directly for now, not from a table
that would otherwise silently return empty.

---

## Scope for this pass (v1), and what's deliberately deferred

**In scope** — enough for the gate to mean something:
- Set and confirm a research mandate (replaces `set_charter.py`)
- Browse the research log (hypotheses under a charter, their status)
- Read a verdict: narrative, claims, caveats, the gates that decided it
- Drill into a study run's tool-call traces (what the agent actually did)
- The scoreboard (confirmed / decayed / testing), derived from
  `Hypothesis.status`/`Verdict` today, ready to switch to reading
  `ScoreboardEntry` directly once Stage 8 starts writing it
- **Lightweight status polling with an animated completion reveal.** A
  real study takes roughly 1–3 minutes end to end (Stage 5/6's own
  measured data). A hypothesis sitting at *testing* with zero feedback
  until a manual refresh reads as dead, not premium, specifically during
  the one window most worth watching. The fix is a plain interval poll
  of `GET /study-runs/{id}` (no websocket, no call-by-call detail) that
  flips a UI state from *testing* to *complete* and plays Fathom's own
  trace-draw-on / count-up treatment on that transition. This is a
  motion/feedback concern, not a streaming-infrastructure one, and it's
  cheap enough to build alongside the research log rather than as its
  own phase.

**Deferred, named rather than silently dropped:**
- **Call-by-call live trace streaming** — watching each individual
  `decide_next_action` decision arrive as it happens, matching
  architecture.md Step 4's "live trace of tool calls" framing literally.
  This is a genuinely different, heavier feature than the polling above:
  it needs websockets or fine-grained polling infrastructure to show
  *sub-run* granularity, and nothing in this project's history has
  demonstrated a real need for that resolution — the coarser "still
  running → done" signal above already closes the actual gap (dead air
  during the wait), without building infrastructure ahead of a proven
  need.
- Chat/interrogation on a verdict (architecture.md Step 7) — a second,
  genuinely different feature (new questions vs. re-reading state) that
  deserves its own pass once the read surface exists to interrogate.
- Scheduled autonomous runs and decay re-testing (Step 8) — that's Stage
  8's own job; v1 displays whatever state already exists, it doesn't
  create decay verdicts on a schedule.

---

## Design decisions

**FastAPI is new work, not a thin wrapper — but it stays read-mostly.**
Every GET endpoint is a direct, thin read over the existing SQLAlchemy
models (`agentic_core/db/models.py`) — no new business logic, since all
the real logic (charter parsing, hypothesis generation, the execution
loop, verdict rendering) already exists and is already gated. The only
two endpoints that *do* anything are charter creation and confirmation,
and both call straight into `agentic_core.charter.create_charter`/
`confirm_charter` — the exact functions `set_charter.py` already calls.
The API is a new transport for existing, already-proven logic, not a
reimplementation of it.

**Alternative considered:** have the frontend call the database directly
via a lightweight ORM-over-HTTP layer (e.g., PostgREST) instead of
hand-written FastAPI routes. **Rejected:** `docs/architecture.md`'s own
stack names FastAPI explicitly, and a generic DB-over-HTTP layer would
expose raw table shapes (including JSONB blobs like `Hypothesis.rule`)
that need real shaping into a frontend-friendly response — that shaping
is exactly what a thin, explicit endpoint layer is for.

**React + TypeScript + Vite, plain CSS (no Tailwind, no component
library).** Fathom's own tokens are already real, working CSS custom
properties (`fathom-design-language.html`) — porting them directly into
the app's global stylesheet is less work than translating them into a
utility-class or component-library vocabulary, and a bespoke, validated
token system is exactly what a utility framework's own defaults would
fight. TypeScript because every backend response has a real, already-
defined shape (the Pydantic schemas), and typing the API client against
that shape is what catches a frontend/backend drift at build time instead
of at runtime.

**Charter creation is in v1, not deferred.** Considered making v1
read-only (research log + scoreboard + traces) and leaving charter
creation on the CLI a while longer. Rejected: a stranger who opens the
app and can only ever look at data some other process created isn't
using the *product* architecture.md defines — "she sets a research
mandate... then she's done" is the literal first sentence of the user
journey Stage 7 exists to build.

---

## Build order

1. **FastAPI backend, read endpoints.** `src/api/` — `charters`,
   `hypotheses`, `study_runs` (+ `/traces`), `verdicts`, `scoreboard`.
   Pydantic response models reuse `agentic_core.schemas` where the shape
   already matches; thin DB-row-to-response mapping otherwise.
2. **FastAPI backend, charter creation + confirmation.** `POST /charters`,
   `POST /charters/{id}/confirm`, wrapping `agentic_core.charter`'s
   existing functions exactly as `set_charter.py` does today.
3. **App shell.** Vite + React + TypeScript scaffold; Fathom's tokens
   ported to a global stylesheet; theme handling (the same dark-first,
   `prefers-color-scheme`-aware pattern the artifact already proves);
   routing skeleton; a typed API client generated from the backend's own
   response shapes.
4. **Charter creation flow.** The mandate textarea, the confirmation
   screen showing the parsed universe/hypothesis families exactly as
   `set_charter.py` prints them today, confirm/reject.
5. **Research log, with the status-poll reveal.** Hypothesis list under a
   charter, status pills, expandable into a verdict card (narrative,
   claims, caveats) — the `.card` component from Fathom, populated with
   real data. Any hypothesis still `testing` polls `GET /study-runs/{id}`
   on an interval; the transition to `complete` triggers the trace-draw-on
   reveal rather than a silent re-render, so the wait after confirming a
   mandate is the one place this app visibly feels alive in real time.
6. **Trace drill-down.** A study run's `tool_call_traces`, in order, each
   claim in a rendered verdict deep-linkable to the trace that produced
   it — the concrete payoff of Sacred Gate 2's own "every claim
   references a real trace" guarantee, made visible.
7. **Scoreboard.** Three sections (confirmed / decayed / testing), derived
   from `Hypothesis.status` and each hypothesis's `Verdict` (see the
   confirmed gap above — `ScoreboardEntry` itself is unpopulated today),
   each entry linking back to its verdict.
8. Manual, end-to-end verification: create a real charter, watch it
   through to a verdict, confirm the trace drill-down matches what
   `tool_call_traces` actually holds, confirm the scoreboard reflects
   real state — the same "a stranger can use it unexplained" gate stated
   as a literal walkthrough.

Each component gets the same explain-then-write cycle as every prior
stage — this is the top-level roadmap, not a substitute for that.

---

## Verification

- Backend: `pytest tests/api/` for response-shape/status-code coverage
  per endpoint, using the existing `test_engine`/db-truncation fixture
  pattern already established in `tests/conftest.py` and
  `tests/agentic_core/conftest.py`.
- Frontend: manual verification in the browser preview (`preview_start`)
  against the real running FastAPI backend — no mocked backend, since the
  whole point of this stage is proving the real system is usable, the
  same live-verification standard every prior stage has held to.
- The stage gate itself: walk through the full journey — set a mandate,
  confirm it, watch a hypothesis reach a verdict, drill into its traces,
  see it land on the scoreboard — with nothing explained beforehand.
