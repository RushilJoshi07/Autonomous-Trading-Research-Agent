# Step 1 — Component 1, FastAPI Read Endpoints

## 1. What this does

`src/api/` is Stage 7's first piece: the first FastAPI transport over the
backend Stages 1–6 already built and gated. `docs/plans/stage-7-plan.md`
names it explicitly — `charters`, `hypotheses`, `study_runs` (+ `/traces`),
`verdicts`, `scoreboard` — and says what it must be: "every GET endpoint is
a direct, thin read over the existing SQLAlchemy models... no new business
logic." Before this component, the only way to see a charter, a hypothesis,
a verdict, or a trace was to query Postgres directly or run
`scripts/set_charter.py`, which its own docstring already calls a stand-in
for "Stage 7's not-yet-built FastAPI/React confirmation flow." That
stand-in is still true after this component for *writes* — charter
creation and confirmation are Component 2, deliberately not touched here —
but every *read* the eventual frontend needs now has a real HTTP endpoint
behind it.

Five routers, one FastAPI dependency, seven new response models, 18 tests.
New: `src/api/app.py`, `src/api/deps.py`, `src/api/schemas.py`,
`src/api/routers/{charters,hypotheses,study_runs,verdicts,scoreboard}.py`,
`tests/api/` (`conftest.py` plus one test file per router). `pyproject.toml`
gained `fastapi`, `uvicorn[standard]`, and (dev-only) `httpx`, which
`fastapi.testclient.TestClient` needs as its transport.

**What this is not.** It is not charter creation or confirmation — no
`POST` route exists anywhere in this component; the only two write
operations this whole system has (`agentic_core.charter.create_charter`/
`confirm_charter`) are still only reachable from `scripts/set_charter.py`.
It is not the frontend — nothing renders any of this yet; Component 3
(the Vite/React scaffold) hasn't been written. And it is not a claim that
the scoreboard's `decayed` section works — it structurally cannot yet,
because nothing writes `ScoreboardEntry` rows, and Section 3 explains why
that's disclosed in the response itself rather than papered over.

---

## 2. Every meaningful line explained

### `deps.py` — the one new pattern this component introduces

```python
def get_db() -> Iterator[Session]:
    session = SessionFactory()
    try:
        yield session
    finally:
        session.close()
```

Every existing caller in this codebase — `agentic_core/charter.py`,
`hypothesis.py`, `verdict.py`, `mcp_tools/server.py` — opens its own
`with SessionFactory() as session:` block per function call. That's the
right pattern for a plain function with no framework managing its
lifecycle. A FastAPI route is different: the framework itself calls this
generator, hands the route whatever gets `yield`ed, and resumes the
generator (running the `finally`) once the route returns or raises. The
concrete payoff shows up in `tests/api/conftest.py`, not here —
`app.dependency_overrides[get_db]` swaps in a test session with a plain
dict assignment, no `monkeypatch.setattr` needed. Contrast
`tests/agentic_core/conftest.py`'s `loop_db_session`, which has to
`monkeypatch.setattr("agentic_core.loop_graph.SessionFactory", Session)` —
necessary there specifically because `loop_graph.py` imports
`SessionFactory` directly into its own module namespace, so patching
`data_pipeline.db.session.SessionFactory` wouldn't reach the name
`loop_graph` already bound at import time. Routes never import
`SessionFactory` at all; they only ever see whatever `Depends(get_db)`
hands them, so there is no import-time binding to work around.

### `schemas.py` — reuse decided per row, not by blanket rule

```python
class CharterOut(BaseModel):
    id: str
    mandate_text: str
    charter: Charter
    confirmed: bool
    created_at: datetime
    confirmed_at: datetime | None
```

`charter: Charter` nests `agentic_core.schemas.Charter` — the exact object
`agentic_core/charter.py`'s `create_charter` writes into the row:
`charter=charter.model_dump(mode="json")`. That's not an assumption; it's
what reading `create_charter` shows. Because the JSONB column's real shape
already matches `schemas.Charter` field for field, `CharterOut(charter=
row.charter, ...)` — a dict passed to a field typed as a `BaseModel` — gets
validated and coerced into a `Charter` instance automatically by Pydantic,
with no manual `Charter.model_validate()` call needed.

```python
class HypothesisOut(BaseModel):
    id: str
    charter_id: str
    rule: StrategyRule
    prediction: str
    falsification_condition: FalsificationCondition
    rationale: str
    citations: list[GroundingChunk]
    grounding_tier: str
    status: str
    created_at: datetime
    study_run_id: str | None
```

This one is *not* a single nested `agentic_core.schemas.Hypothesis`, and
that's a real finding, not a stylistic choice — reading
`agentic_core/hypothesis.py`'s `propose_hypothesis` shows `HypothesisRow`
stores `rule`, `prediction`, `falsification_condition`, `rationale`,
`citations`, and `grounding_tier` as **separate columns**, not one nested
blob the way `CharterRow.charter` is. `schemas.Hypothesis` (the object
`propose_hypothesis` builds in memory) has a `parsed: ParsedHypothesis`
wrapper that doesn't exist in the row shape at all. Building `HypothesisOut`
as if it matched `schemas.Hypothesis` would have looked reasonable and
failed the moment it hit a real row — instead, it's composed from the
individual pieces (`StrategyRule`, `FalsificationCondition`,
`GroundingChunk`) that genuinely do match column-for-column, plus the
row's own `id`/`status`/`created_at`/`charter_id`. This was verified live,
not just reasoned through: `GET /hypotheses/{id}` against the real
`LowVol_AAPL_ATR_MeanReversion` hypothesis (Stage 5's own Sacred Gate 2
fixture, still sitting in the dev database) round-tripped its full nested
`StrategyRule` tree with no validation error.

```python
    study_run_id: str | None
```

No column on `HypothesisRow` stores this. The foreign key runs the other
way — `StudyRun.hypothesis_id` — so this field is computed by the route,
not read off the row. See Section 3 for why it's a single nullable id
rather than a list, and why that's a deliberate, disclosed simplification
rather than an oversight.

```python
class StudyRunOut(BaseModel):
    ...
    verdict_id: str | None
```

Same shape of problem, opposite answer on cardinality. `Verdict.study_run_id`
points at `StudyRun`, not the reverse, so this is also computed, not
stored — but here a single nullable id is actually correct, not a
simplification, because `agentic_core/verdict.py`'s `render_verdict`
writes at most one `Verdict` row per `study_run_id` (guarded by `if
run.status != "completed": raise ValueError(...)` before it ever writes
anything, and there's exactly one `VerdictRow(...)` construction in the
whole function). Confirmed by reading that function, not assumed from the
column name.

### `routers/scoreboard.py` — the honest gap

```python
_DECAYED_NOTE = (
    "Always empty today: 'decayed' comes from Stage 8's scheduled "
    "decay-recheck job re-verifying confirmed strategies against new data, "
    "which doesn't exist yet. This is a disclosed scope boundary, not a "
    "query returning nothing by mistake."
)
...
return ScoreboardOut(confirmed=confirmed, decayed=[], decayed_note=_DECAYED_NOTE, testing=testing)
```

`agentic_core.db.models.ScoreboardEntry` exists as a table but has no
writer anywhere in this project — `docs/plans/stage-7-plan.md` names this
directly as a gap confirmed before the plan was written, not discovered
later. An endpoint that just returned `decayed: []` with no explanation
would be indistinguishable, from the response alone, from "nothing has
decayed yet" — which is a real, meaningful claim this system cannot
actually make today, since nothing has ever checked. Putting the reason in
the response body itself, not only in a code comment, means any client —
including one nobody on this project wrote — sees the distinction without
having to read the source.

```python
    for hyp in db.execute(select(HypothesisRow).where(HypothesisRow.status == "confirmed")).scalars():
        run_id = _latest_study_run_id(db, hyp.id)
        verdict_row = (
            db.execute(select(VerdictRow).where(VerdictRow.study_run_id == run_id)).scalar_one_or_none()
            if run_id is not None
            else None
        )
        if verdict_row is not None:
            confirmed.append(...)
```

One query per hypothesis rather than a single join. At today's real data
volume (one hypothesis in the entire live database) this is not a
meaningful cost, and it keeps the route reading as a direct composition of
the same two lookups (`_latest_study_run_id`, then the verdict for that
run) that `hypotheses.py` and `study_runs.py` already use — the N+1 here is
the honest price of staying consistent with the FK-following design
(Section 3) rather than writing scoreboard-specific join logic that exists
nowhere else in this component.

### `app.py` — CORS as a named, checkable guess

```python
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)
```

Component 3 (the Vite/React scaffold) doesn't exist yet, so there is no
real frontend origin to test this against. `5173` is Vite's own default
dev-server port — a reasonable, disclosed guess, not a verified value. The
comment directly above this block in the source says explicitly what to do
about it: check the browser's actual origin the first time Component 3
makes a real request here, and fix it immediately if it's wrong, rather
than letting a mismatch surface later as an unexplained blocked request. A
wrong CORS origin fails silently from the frontend's point of view — the
browser blocks the response and the failure looks like "the API is down,"
not "the origin list is wrong" — which is exactly the kind of bug worth
naming in advance rather than discovering cold.

**Boilerplate skipped:** the individual `_to_out` mapping functions in
`charters.py`, `study_runs.py`, and `verdicts.py` (`verdicts.py`'s is
reused directly by `scoreboard.py` rather than duplicated), the router
`prefix`/`tags` arguments, and the straightforward `if row is None: raise
HTTPException(status_code=404, ...)` guard repeated at the top of every
single-resource route — each one is a direct field-by-field copy from an
ORM row to its `Out` model, with nothing conditional or decision-bearing
in the mapping itself.

---

## 3. Design decisions and rejected alternatives

### Routers follow the DB's own foreign-key chain; nothing embeds a nested object

**Chosen:** every response is a thin, single-table-ish view (`CharterOut`,
`HypothesisOut`, `StudyRunOut`, `ToolCallTraceOut`, `VerdictOut`) that
carries the *id* of a related resource — `HypothesisOut.study_run_id`,
`StudyRunOut.verdict_id` — rather than the related object itself.

**Alternative considered:** a `GET /hypotheses/{id}` that embeds its full
study run and, if one exists, its full verdict inline — fewer round trips
for a frontend rendering one hypothesis card.

**Why rejected:** this schema already commits to FK-following as its
answer to exactly this tradeoff, and it does so for a reason bigger than
this component — `Claim.tool_call_trace_id` (`agentic_core/schemas.py`)
links a verdict claim to the trace that produced it *by id*, not by
embedding the trace, and Component 6's own not-yet-built trace drill-down
is designed around walking that link, per `docs/plans/stage-7-plan.md`:
"each claim in a rendered verdict deep-linkable to the trace that produced
it." An embedding endpoint here would be a second, inconsistent answer to
the same design question one component later in the same stage.
`docs/plans/stage-7-plan.md` also already rejected a generic
PostgREST-style layer for a related reason — raw table shapes need real
shaping — and an endpoint that joins three tables into one nested blob is
a different kind of raw shape needing the same kind of shaping work,
just done once instead of left to the frontend.

**Cost to reverse:** low per-endpoint (each embedding would be a small,
local change to one `_to_out` function), but reversing it project-wide
would mean picking a different answer than Component 6 already assumes.

### `get_db()` as a FastAPI dependency, not inline `with SessionFactory()`

**Chosen:** a generator dependency, injected via `Depends(get_db)`.

**Alternative considered:** copy the pattern every other module in this
codebase already uses — `with SessionFactory() as session:` inline at the
top of each route function.

**Why rejected:** it would work, but every test would then need the same
`monkeypatch.setattr("api.routers.charters.SessionFactory", ...)` dance
`tests/agentic_core/conftest.py` needs for `loop_graph`/`corpus` — repeated
five times, once per router module, since each would bind its own
module-level name at import time. FastAPI's dependency-override mechanism
exists specifically to avoid that: one override, `app.dependency_overrides
[get_db]`, covers every router that depends on it, because none of them
import `SessionFactory` directly — they only ever receive whatever the
dependency yields.

**Cost to reverse:** moderate — every route signature and every test's
override mechanism would need to change together, not just one file.

### `HypothesisOut.study_run_id`: a single nullable id, not a list

**Chosen:** the route picks the most recent `StudyRun` by `started_at` and
returns just its id (or `None`).

**Alternative considered:** return a list of every `StudyRun` under a
hypothesis, since `StudyRun`'s own docstring in `agentic_core/db/models.py`
says a re-test is "a new StudyDesign + StudyRun under the same Hypothesis"
— meaning the schema genuinely permits more than one, even though nothing
in this project has ever created more than one yet.

**Why rejected:** re-testing (`docs/architecture.md` Step 7) is explicitly
deferred out of Stage 7 v1 by `docs/plans/stage-7-plan.md` itself, and
Component 5's own stated need (the frontend's poll target) is exactly one
id per hypothesis to poll, not a list to disambiguate. A list-typed field
would be more honest about the schema's ultimate cardinality, but it would
also hand every future caller a problem — "which one is the current one?"
— that doesn't exist yet anywhere in this system. This is named here
explicitly as a scope-driven simplification, not a claim that a hypothesis
can never have more than one study run: the day re-testing ships, this
field's meaning ("the run to show") stops being unambiguous and needs
revisiting.

**Cost to reverse:** low today (no caller depends on the single-id shape
except a component not yet written) — but this is exactly the kind of
decision that gets expensive to reverse *after* a frontend is built
against it, which is precisely why it's written down now rather than left
implicit.

### The scoreboard's `decayed` bucket carries a `decayed_note`, not just an empty list

Covered in Section 2 above — repeated here only to name the alternative
explicitly. **Alternative considered:** omit the explanation and let `[]`
speak for itself, since the plan document already discloses the gap.
**Why rejected:** the plan document is source material for people working
on this codebase; it says nothing to a frontend, a QA pass, or a future
engineer who only ever sees the JSON response. Putting the explanation in
the response itself means the honesty travels with the data, not just with
the docs.

---

## 4. Concepts introduced

**FastAPI's dependency-injection system, as distinct from a plain Python
function call.** `Depends(get_db)` doesn't call `get_db()` and pass its
return value — it registers `get_db` as something the framework itself
invokes per request, manages the lifecycle of (running past the `yield`,
then resuming after the route handler returns to run the `finally`), and —
critically for testing — allows swapping out entirely via
`app.dependency_overrides` without touching any route's own code. This is
a different relationship between caller and callee than every other
`SessionFactory` use in this codebase, where the calling function fully
owns the `with` block itself.

**Response-model shape as an empirical question, not a naming
convention.** `CharterOut` matching `schemas.Charter` and `HypothesisOut`
*not* matching `schemas.Hypothesis` look, from their names alone, like
they should behave the same way — both are "the resolved Pydantic object
for this row." Whether a DB row's stored JSONB actually matches an
existing schema is a fact about what a specific write path
(`create_charter` vs. `propose_hypothesis`) happened to persist, discoverable
only by reading that code, not inferable from the schema's name or from
the existence of a same-named Pydantic model elsewhere in the codebase.

---

## 5. Verification

`pytest tests/api/` — 18 tests, all passing against the real Postgres test
database via the `test_engine` fixture (`tests/conftest.py`), no mocking.
Coverage per router: found/not-found (404) for every single-resource
route, list filtering (`GET /hypotheses?charter_id=`), trace ordering by
`step_index` rather than insertion order, `HypothesisOut.study_run_id`
correctly resolving to the *most recent* of two seeded runs (not just "a"
run), `StudyRunOut.verdict_id` present only once a `Verdict` row exists,
and the scoreboard correctly excluding `rejected`/`proposed` hypotheses
from both the `confirmed` and `testing` buckets — a case that matters
because a route that just filtered by "has a verdict" rather than
"`status == 'confirmed'`" would have put the rejected fixture in the wrong
bucket, and nothing about the response's shape alone would reveal that
mistake without a test that specifically seeds a rejected case alongside
a confirmed one.

Full suite: `pytest` — 358 passed (up from 340 at the end of Stage 6),
confirming nothing in five existing stages broke.

Then live, against real data, not synthetic fixtures: ran
`uvicorn api.app:app` against the actual dev database and walked one real
charter through every route by hand — `GET /charters` → `GET /hypotheses?
charter_id=` → `GET /hypotheses/{id}` (real `study_run_id` resolved
correctly) → `GET /study-runs/{id}` (real `verdict_id` resolved correctly,
`status: "completed"`) → `GET /study-runs/{id}/traces` (8 real traces,
correctly ordered) → `GET /verdicts/{id}`. The hypothesis under test was
`LowVol_AAPL_ATR_MeanReversion` — the exact real, rejected walk-forward
study `tests/agentic_core/test_verdict.py` already documents as Sacred
Gate 2's own load-bearing evidence (Sharpe 0.77 in-sample collapsing to
-1.51/0.55/0.94 out-of-sample, all three gates failing). Its full verdict —
ten claims, a five-paragraph narrative, five caveats — round-tripped
through `VerdictOut` with no validation error, meaning the response models
built from reading the persistence code actually match what real
production data looks like, not just what synthetic test rows look like.
404s were checked live too, not only in `pytest`.

**What this does not prove.** It proves the shape of every read endpoint
is correct against both synthetic and real data, and that the computed
fields (`study_run_id`, `verdict_id`) resolve correctly including the
"most recent of several" case. It does not prove anything about
performance under load (the scoreboard's per-hypothesis query pattern is
untested past one real hypothesis), and it does not prove the CORS origin
guess is correct — that can't be proven until Component 3's real frontend
exists to test it against, which is why it's named as an open item rather
than closed here.

---

## 6. Interview defense

**"Why does `HypothesisOut` build seven fields by hand instead of just
nesting `agentic_core.schemas.Hypothesis`, when `CharterOut` gets to nest
`schemas.Charter` directly?"** Because they're not actually the same
situation, and I checked rather than assumed. `CharterRow.charter` is
written as `charter.model_dump(mode="json")` — the whole object, one
column. `HypothesisRow` stores `rule`, `prediction`,
`falsification_condition`, `rationale`, `citations`, and `grounding_tier`
as six separate columns, and `schemas.Hypothesis` itself has a `parsed:
ParsedHypothesis` wrapper layer that doesn't exist in the row at all.
Nesting `schemas.Hypothesis` directly would have looked like the more
consistent choice and failed the first time it validated a real row.

**"Why not just build the frontend against raw SQL views or a generic
ORM-over-HTTP layer, instead of hand-writing five router files?"** Because
`docs/plans/stage-7-plan.md` already made and justified that call before
this component started: a generic layer exposes raw table shapes,
including JSONB blobs like `Hypothesis.rule`'s full nested indicator tree,
that need real shaping into something a frontend can render — computing
`study_run_id`/`verdict_id` from foreign keys that point the *other*
direction is exactly the kind of shaping a generic reflection layer
couldn't do without becoming a second, ad hoc query language bolted onto
the first one.

**Hard question: "Your CORS origin is a guess. What if it's wrong and
nobody notices for weeks?"** That's a real, live risk, and it's the reason
it's written down in three places — the plan doc's note above, a code
comment directly on the middleware, and this document — rather than left
implicit. The honest failure mode is specific and diagnosable: the
frontend's requests would show up in the browser's network tab as
successful (the server does respond) but blocked by the browser itself
before JavaScript ever sees the response, which reads to an unprepared
developer as "the API doesn't work" rather than "the origin list is
stale." Naming that failure mode in advance, and naming the exact moment
to check it (Component 3's very first real request), is the actual
mitigation — there's no way to *prove* the guess right before the thing
it's guessing about exists.

**"Isn't computing `study_run_id` and `verdict_id` with extra queries,
instead of storing them as columns, slower than it needs to be?"** At
today's data volume — one confirmed hypothesis in the entire live database
— the honest answer is it doesn't matter yet, and I'd rather say that
plainly than defend a premature optimization. The real reason it's a
computed lookup and not a stored column is correctness, not performance:
storing `verdict_id` on `StudyRun` would mean writing to two tables
non-atomically whenever a verdict is rendered, and `render_verdict`
already deliberately doesn't do that (Section 2's read of the function
found exactly one `VerdictRow` write, keyed by the FK that already
exists). A denormalized column would be one more place that write could
drift out of sync with reality — the same class of problem
`.claude/rules/data-pipeline.md` already names for corporate actions on
price data, just at a different layer of this system.

---

## 7. What comes next and why

Component 2 (charter creation and confirmation — the only two write
routes this whole API will ever need) wraps `agentic_core.charter.
create_charter`/`confirm_charter` exactly as `scripts/set_charter.py`
already does today, per the plan. Everything built here — the router
structure, `get_db`, the response-model reuse pattern — is what Component
2 extends, not something it replaces.

**If this component were wrong** — if a response model silently mismatched
what a row actually contains, the way `HypothesisOut` would have if it had
nested `schemas.Hypothesis` without checking — the failure wouldn't show
up in `pytest tests/api/`'s synthetic fixtures if those fixtures happened
to be constructed the same wrong way the code assumed. It would show up
exactly where it was actually caught here: the first time a real,
already-existing, non-trivial row (the real `LowVol_AAPL_ATR_MeanReversion`
hypothesis, with its full real indicator tree) hit the endpoint. That's
the concrete reason the live verification against real dev-database data
in Section 5 isn't a formality on top of the test suite — it's the check
that actually exercises the gap a synthetic-only test suite could miss.
