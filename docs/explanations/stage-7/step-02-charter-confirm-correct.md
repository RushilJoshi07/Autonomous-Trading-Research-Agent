# Step 2 — Component 2, Charter Create/Confirm/Correct

## 1. What this does

`docs/plans/stage-7-plan.md` names Component 2 as two write endpoints:
`POST /charters` and `POST /charters/{id}/confirm`, wrapping
`agentic_core.charter`'s existing `create_charter`/`confirm_charter`
exactly as `scripts/set_charter.py` already does. That much shipped. But
before any code was written, a real gap in the plan surfaced in
conversation: the confirmation screen `docs/architecture.md`'s Step 1
describes ("she confirms it") says nothing about what happens when the
parsed charter is *wrong* — when the LLM picked the wrong industry, the
wrong cut, the wrong hypothesis family. Before this component, the only
recourse was closing the terminal and typing a completely fresh mandate
from scratch, discarding everything the system got right along with the
one thing it got wrong.

This component adds a third capability the plan doesn't name at all:
`POST /charters/{id}/correct` — a way to hand back a plain-language
correction ("that's too narrow, include all of tech") and get a
re-interpreted charter for confirmation, capped at two correction rounds,
with the full history of what she said and how the interpretation changed
kept as real rows rather than overwritten.

**What this is not.** It is not a general-purpose "edit any field"
mechanism — there is no endpoint anywhere that lets a caller directly set
`resolved_universe` or any other resolved value; Section 3 explains why
that door is deliberately never opened. It is not unbounded — a chain that
has already used both its correction rounds must be confirmed as-is or
restarted from a fresh mandate, not corrected a third time. And it is not
new grounding or hypothesis logic; nothing here touches `hypothesis.py` or
anything downstream of `confirm_charter`.

New: `migrations/versions/1497a09ac6e8_charter_correction_chain.py`,
`tests/agentic_core/test_charter.py`, `tests/api/test_charters_write.py`.
Changed: `src/agentic_core/db/models.py` (three new `Charter` columns),
`src/agentic_core/charter.py` (four new exceptions, `correct_charter`, and
two real fixes to the existing `confirm_charter`), `src/api/schemas.py`,
`src/api/routers/charters.py`, `tests/agentic_core/conftest.py`,
`tests/api/conftest.py`.

---

## 2. Every meaningful line explained

### The schema change

```python
parent_charter_id: Mapped[str | None] = mapped_column(ForeignKey("charters.id"), nullable=True, index=True)
correction_round: Mapped[int] = mapped_column(Integer, default=0)
correction_text: Mapped[str | None] = mapped_column(Text, nullable=True)
```

A self-referencing foreign key. `parent_charter_id` is `None` exactly for
a round-0 (original) charter; every correction's row points at the charter
it corrected. This was checked live, not just assumed to work: attempting
to delete a parent row while a child still points at it raised a real
`psycopg2.errors.ForeignKeyViolation` during this component's own
verification — the database itself refuses to let a correction chain be
silently orphaned by deleting its root out from under it. That's a genuine
property of the schema, discovered by trying to break it, not something
explicitly coded for.

### `_combined_mandate_for_correction` — the actual mechanism

```python
def _combined_mandate_for_correction(root_mandate_text: str, previous_charter: Charter, correction_text: str) -> str:
    p = previous_charter.parsed
    return (
        f'Original request: "{root_mandate_text}"\n\n'
        "This was interpreted as: "
        f"sector={p.universe.sector!r}, industry={p.universe.industry!r}, ...\n\n"
        f'The user says: "{correction_text}"\n\n'
        "Re-interpret the original request in light of this correction, "
        "producing an updated charter that addresses what she said."
    )
```

Three things concatenated, not two. Her raw correction text alone — "no,
not just consumer electronics" — carries no information about what it's
correcting; it only means something read against a specific prior
interpretation. Restating that interpretation explicitly, in the same
prompt, is what turns her correction into a legible delta the model can
actually act on, rather than a second, free-floating instruction it has to
guess how to reconcile with the first sentence. This function's whole
output is then handed to `parse_charter` completely unchanged from how it
already works for a first-time request — no new LLM code path exists.

### `correct_charter` — reading before writing, in the same order every other function in this file uses

```python
with SessionFactory() as session:
    row = session.get(CharterRow, charter_id)
    if row is None:
        raise CharterNotFoundError(...)
    if row.confirmed:
        raise CharterAlreadyConfirmedError(...)
    if row.correction_round >= MAX_CORRECTION_ROUNDS:
        raise CorrectionLimitExceededError(...)
    root_mandate_text = row.mandate_text
    previous_charter = Charter.model_validate(row.charter)
    next_round = row.correction_round + 1
```

Three checks in a fixed order: does it exist, is it still correctable,
has it already used its rounds. Each raises a distinct, named exception
rather than a shared generic one — see Section 3 for why that specific
choice matters at the API boundary. This block runs inside its own short
session that only reads, closed before the (potentially slow, real-money)
LLM call happens — `correct_charter` doesn't hold a database connection
open for the duration of a network call to Bedrock, the same session-
scoping discipline `create_charter` already established for its own
`parse_charter`/`resolve_universe` calls.

```python
combined = _combined_mandate_for_correction(root_mandate_text, previous_charter, correction_text)
parsed = parse_charter(combined)
charter = resolve_universe(parsed)
blocked = len(charter.resolved_universe) == 0
```

Identical to `create_charter`'s own body from this point on — parse, then
resolve, then check for an empty universe — because a correction round
*is* exactly the same pipeline, just with richer input. This is not
duplicated logic dressed up as new code; it is deliberately the same two
function calls `create_charter` already makes, which is the concrete
payoff of routing corrections through `parse_charter` instead of
inventing a second pipeline (Section 3).

```python
session.add(CharterRow(
    id=new_charter_id,
    mandate_text=root_mandate_text,
    charter=charter.model_dump(mode="json"),
    confirmed=False,
    created_at=datetime.now(),
    parent_charter_id=charter_id,
    correction_round=next_round,
    correction_text=correction_text,
))
```

A brand new row, never an `UPDATE` against `charter_id`'s own row. This is
the load-bearing choice of the whole component (Section 3) — every prior
interpretation she was shown stays queryable exactly as it was shown to
her, forever, rather than being overwritten the moment she asks for
something different.

### `confirm_charter` — two real fixes to code that already existed

```python
def confirm_charter(charter_id: str) -> None:
    with SessionFactory() as session:
        row = session.get(CharterRow, charter_id)
        if row is None:
            raise CharterNotFoundError(f"no charter with id {charter_id!r}")
        charter = Charter.model_validate(row.charter)
        if not charter.resolved_universe:
            raise CharterBlockedError(...)
        row.confirmed = True
        row.confirmed_at = datetime.now()
        session.commit()
```

Before this component, this function was three lines: fetch, set two
fields, commit — no `is None` check, no universe check. Confirmed by grep
before touching it: zero test files anywhere in this project reference
`confirm_charter` or `create_charter` by name. It had never been unit
tested; its only exercise was live, through `scripts/set_charter.py`.
That script never actually needed the missing guards, not because the
underlying invariants didn't matter, but because the CLI's own control
flow happened to enforce them from *outside* the function: it prints
`BLOCKED` and calls `sys.exit(1)` before ever reaching the "confirm?"
prompt when `resolved_universe` is empty, and it always calls this
function with an id it just received back from `create_charter`, so it
never had a reason to pass a bad one. The moment this component adds an
HTTP endpoint, that protection disappears — an HTTP client can call
`POST /charters/{id}/confirm` with literally any string, and nothing
about the endpoint or the function stopped it from confirming an
empty-universe charter, or from crashing with a raw, unhelpful
`AttributeError: 'NoneType' object has no attribute 'confirmed'` on a
nonexistent id. Both gaps are closed here, in the function itself, so
every present and future caller gets the guarantee — not by trusting a
new caller to reimplement the CLI's own discipline.

### Exception types, not string-matched messages

```python
class CharterNotFoundError(Exception): ...
class CharterAlreadyConfirmedError(Exception): ...
class CharterBlockedError(Exception): ...
class CorrectionLimitExceededError(Exception): ...
```

Four distinct classes rather than one `ValueError` with different
messages. This follows a convention this codebase already established —
`hypothesis.py`'s `DuplicateHypothesisError`, `verdict.py`'s
`VerdictValidationError` — and the reason matters concretely here: the API
layer (`src/api/routers/charters.py`) needs to map different failures to
different HTTP status codes (404 for not-found, 409 for every "wrong
state" case), and doing that by inspecting exception *type* is exact and
refactor-safe; doing it by matching substrings of an error message (the
way `is_rate_limited` does in Stage 6's own gate script, by its own
admission a disclosed heuristic) would silently break the moment anyone
reworded a message.

### The API layer — three routes, one already-established pattern

```python
@router.post("/{charter_id}/correct", response_model=CharterWriteOut, status_code=201)
def post_correct_charter(charter_id: str, body: CharterCorrectIn, db: Session = Depends(get_db)) -> CharterWriteOut:
    try:
        new_charter_id, _, blocked = correct_charter(charter_id, body.correction_text)
    except CharterNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except (CharterAlreadyConfirmedError, CorrectionLimitExceededError) as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    row = _get_row_or_404(db, new_charter_id)
    return CharterWriteOut(**_to_out(row).model_dump(), blocked=blocked)
```

`status_code=201`, matching `POST /charters` — not 200. The id in this
response is a *different resource* than the `charter_id` in the URL
(`new_charter_id`, a freshly inserted row), so this is a creation, not an
update, even though it's reached via a path that names the charter it
corrects. `db` here never touches the write at all — `correct_charter`
already committed the new row through its own `SessionFactory` session
before this route function does anything with `db`. The `get_db`-injected
session is used only to read that row back, reusing `_to_out` (Component
1's own mapping function, now returning three more fields), so the
response is built from one code path regardless of whether it's a plain
`GET` or the tail end of a write — this is the same reasoning
Component 1's step explainer already gives for why `HypothesisOut` reuses
row-mapping logic rather than reassembling a response by hand at each
call site.

**Boilerplate skipped:** `CharterCreateIn`/`CharterCorrectIn` (each a
single-field Pydantic model wrapping a request body — `mandate_text` and
`correction_text` respectively), `post_charter`'s own body (structurally
identical to `post_correct_charter`'s, minus the exception handling since
`create_charter` has nothing to look up and therefore nothing that can be
not-found), and `post_confirm_charter`'s body (the same two-exception
`try`/`except` shape, mapping `CharterNotFoundError`→404 and
`CharterBlockedError`→409).

---

## 3. Design decisions and rejected alternatives

### A correction re-invokes `parse_charter`; it does not get its own mechanism

**Chosen:** `correct_charter` calls the exact same `parse_charter`
function `create_charter` already calls, against a richer input string.

**Alternatives considered, and explicitly rejected in conversation before
any code existed:** (1) let her directly hand-edit resolved fields on the
confirmation screen — pick a different ticker, change the cut percentage
by hand. (2) build a second, narrower LLM call whose only job is
interpreting a correction and patching specific fields of the existing
charter.

**Why (1) is rejected:** `docs/architecture.md`'s own Step 1 states the
invariant this would break directly: *"there is no path by which a
hallucinated ticker symbol can reach this object."* A person hand-picking
tickers on a screen is exactly that path, just human-hallucinated instead
of model-hallucinated — and it would also silently bypass
`.claude/rules/data-pipeline.md`'s relative-threshold screening discipline
(quintile/tercile/decile *of the matched sector/industry group*, never a
hand-picked absolute value) the moment a human overrides what the screener
actually computed. **Why (2) is rejected:** `docs/architecture.md` §3
names exactly two moments the LLM is allowed to be fuzzy — translating her
sentence into a charter, and writing prose around numbers already locked
by computation. A correction *is* the first moment, invoked a second time;
a dedicated "edit" LLM call would be a third fuzzy moment, duplicating
`_charter_prompt`'s own already-validated prompt engineering (the real
sector/industry pairing constraint that fixed a confirmed bug — see
`charter.py`'s own comment about "Consumer Cyclical" + "Consumer
Electronics" never actually co-occurring) with none of its track record,
for no benefit over just calling the trusted function again.

**Cost to reverse:** high in a specific sense — reversing this would mean
building one of the two rejected alternatives from scratch, and the
first one (hand-editing) would require reopening the exact hallucination-
path question `docs/architecture.md` already closed for the original
charter flow.

### Restated interpretation, not raw text concatenation

**Chosen:** `_combined_mandate_for_correction` includes a full restatement
of what the system understood the request as, between the original
sentence and her correction.

**Alternative considered:** just append her correction sentence to the
original mandate text (`f"{original}. {correction}"`) and re-parse that.

**Why rejected:** a correction is inherently a *reaction* to a specific
prior interpretation, and without restating that interpretation in the
prompt, the model has to reconstruct it from nothing or guess. "That's too
narrow, include all of tech" said in isolation, with no context about what
was narrow, is not obviously distinguishable from a second, unrelated
instruction about scope. Restating the interpretation explicitly is what
lets the correction function as an actual delta.

**Cost to reverse:** low mechanically (one function, one string template),
but reversing it would very likely degrade correction quality in a way
that's hard to detect without live testing against a real model — exactly
the category of regression `docs/architecture.md`'s own eval/drift
philosophy (Stage 6/9) warns can happen silently.

### A hard cap of 2 correction rounds

**Chosen:** `MAX_CORRECTION_ROUNDS = 2` — three total confirmation
screens possible (original, +1, +2), then a forced choice: confirm as-is,
or restart with a fresh mandate via `create_charter`.

**Alternative considered:** unbounded corrections, letting her iterate
until satisfied.

**Why rejected:** every correction round is a real, billed LLM call with
real hallucination risk, not a free retry. `.claude/rules/agent-honesty.md`
already treats "how many chances has the model had" as something this
project tracks and bounds rather than leaves open — the multiple-
comparisons correction on hypothesis testing exists for exactly this
reason, one layer downstream of charter parsing. Applying the same
discipline here, before the charter is even confirmed, is consistent
rather than an arbitrary new rule: an unbounded retry loop against an LLM
is a cost and drift risk regardless of which layer of this system it sits
in.

**Cost to reverse:** trivial — one constant. The harder question if this
number were ever revisited is whether 2 is actually the right number
versus, say, 1 or 3; no data exists yet to calibrate it against, so it's
disclosed here as a considered choice, not a measured one.

### One immutable row per correction, chained by `parent_charter_id`

**Chosen:** every correction inserts a new `Charter` row; `charter_id`'s
own row is never mutated.

**Alternative considered:** update the existing row in place — overwrite
its `charter` JSONB column with the newly parsed result, bump a counter.

**Why rejected:** this project already has a precedent for exactly this
tradeoff — `StudyDesign`'s own docstring states its immutability
explicitly: *"no `updated_at`, deliberately... pre-registration integrity
... depends on this row never changing."* A charter she's already been
shown and reacted to is analogous: overwriting it in place would mean the
history of what she originally saw, what she said, and how the
interpretation changed is gone the instant she moves forward, which makes
questions like "why does round 2 look this way" unanswerable except from
memory. The one-row-per-attempt design keeps that history queryable
indefinitely, at the storage cost of a few extra small rows per corrected
charter — a cost this project already accepts elsewhere for the same
kind of guarantee.

**Cost to reverse:** moderate — reversing it means losing whatever history
already exists unless it's migrated out first, and every reader of
`Charter` rows (the API's `CharterOut`, any future frontend) would need to
stop expecting a chain.

### `mandate_text` denormalized to the chain's root on every row

**Chosen:** every row in a correction chain — including round 1 and round
2 — stores the *original* sentence in `mandate_text`, not the combined
re-parse prompt that actually produced that row.

**Alternative considered:** leave `mandate_text` null (or store the
combined prompt) on correction rows, requiring a caller to walk
`parent_charter_id` back to round 0 to find out what she originally
asked for.

**Why rejected:** this project already has a named precedent for exactly
this choice — `ToolCallTrace.window_index`'s own column comment: *"stamped
at write time rather than derived later... breaks the moment two windows
share a boundary."* The general principle is the same here: any single
row, read in isolation, should be able to answer "what did she originally
ask for" without a recursive query or a join back to round 0. The combined
re-parse prompt itself is not persisted anywhere as its own field — it's
mechanically reconstructible at any time from `mandate_text` (root) +
the parent row's own `charter` JSONB (the restated interpretation) +
`correction_text`, so nothing is lost by not storing a fourth, redundant
blob.

**Cost to reverse:** low, but there's no real reason to.

### `confirm_charter` is idempotent; `correct_charter` on a confirmed charter is not

**Chosen:** confirming an already-confirmed charter a second time just
re-sets `confirmed_at` and succeeds; requesting a correction on an
already-confirmed charter raises `CharterAlreadyConfirmedError`.

**Why this asymmetry, not symmetric strictness or symmetric leniency:**
confirming twice changes nothing about the charter's actual content —
there's no correctness reason to forbid a harmless no-op. A correction
after confirmation is a different situation entirely:
`docs/architecture.md` Step 1 states plainly that *"the confirmation flag
is what allows the agent to start,"* and `hypothesis.py`'s
`propose_hypothesis` already enforces that flag as a hard gate downstream
(`if not charter_row.confirmed: raise ValueError(...)`). Letting a
correction keep mutating the chain after that gate has already opened
would mean a charter the agent may already be working against could
silently gain a sibling row representing a different interpretation,
with no defined relationship between the two. Blocking corrections at
that point protects a boundary the rest of the system already depends on;
allowing re-confirmation does not touch that boundary at all.

**Cost to reverse:** low for the idempotency half; reversing the
confirmed-charter block would require deciding what happens to hypotheses
and study runs already started against the charter being "corrected" — a
much bigger design question this component deliberately doesn't open.

---

## 4. Concepts introduced

**Idempotency versus a hard state-machine transition, in the same file.**
`confirm_charter` and `correct_charter` sit right next to each other and
handle "call this again with the same input" completely differently — one
absorbs it silently, the other refuses outright. Neither is a general
rule; each is a decision about whether repeating the action can ever
mean something different the second time. Confirming twice can't (the
row's content is unchanged); correcting after confirmation can (it would
mean altering a charter work may already be underway against).

**A self-referencing foreign key as a real, checkable delete constraint,
not just a modeling convenience.** `parent_charter_id -> charters.id`
looks, on paper, like a matter of Python-level bookkeeping. It isn't
only that — Postgres itself enforces it, discovered live during this
component's own verification when deleting a parent row while a
correction still pointed at it failed with a genuine
`ForeignKeyViolation`. The database is doing real work here, not just
storing what the ORM tells it to.

---

## 5. Verification

`tests/agentic_core/test_charter.py` — 10 tests, `parse_charter` and
`resolve_universe` monkeypatched with a deliberately *responsive* fake
(it reads the text it's given and only widens the universe when it sees
the literal phrase "all of tech"), specifically so the tests prove the
combined prompt's actual *content* reaches `parse_charter` and changes
the outcome — not merely that calling `correct_charter` inserts a second
row. Covers round-0 creation, a chained correction actually changing
`industry` from `"Consumer Electronics"` to `None` and the resolved
universe from `["AAPL"]` to `["AAPL", "MSFT", "GOOGL"]`, two rounds
followed by a third correctly raising `CorrectionLimitExceededError`,
correcting a confirmed charter raising `CharterAlreadyConfirmedError`,
not-found for both new entry points, the new blocked-guard on
`confirm_charter`, and `confirm_charter`'s idempotency.

`tests/api/test_charters_write.py` — 9 tests, HTTP status codes and
response shapes for all three routes, including a real, deliberately
found bug fixed *before* it could do damage: `tests/api/conftest.py`'s
`api_db_session` fixture, from Component 1, only overrode the `get_db`
FastAPI dependency. This component's write routes call
`agentic_core.charter`'s functions directly, which own independent
`SessionFactory`-bound sessions entirely outside `get_db` — reasoning
through that session-ownership chain before running a single write-route
test surfaced that, unpatched, those tests would have written real rows
into the actual dev database rather than the test one. Fixed by adding
the same `monkeypatch.setattr("agentic_core.charter.SessionFactory", ...)`
this project already uses for `loop_graph`/`corpus` before any test ran
against it, not discovered by cleaning up contamination afterward.

Full suite: 377 passed (up from 358 at the end of Component 1).

Then live, with the **real** LLM — the first real (non-mocked) model call
this stage has made. `POST /charters` with "Investigate momentum
strategies on large-cap consumer electronics companies" correctly
resolved `sector=Technology, industry=Consumer Electronics,
resolved_universe=[AAPL]`. `POST .../correct` with "too narrow, widen it
to all of Technology sector, not just Consumer Electronics" made the real
model correctly drop `industry` to `null`, and the resolved universe
genuinely changed to `["NVDA", "MSFT"]` with `screening_group_size` going
from 1 to 9 — concrete, live proof the correction mechanism changes a
real interpretation against a real model, not only a scripted fake. The
round-limit and already-confirmed 409s were also checked live. The two
real rows this created were deleted afterward (child before parent,
verified by re-querying both ids came back `None`), the same
cleanup-and-verify discipline `eval/fixtures.py`'s `cleanup`/
`verify_cleanup` already established for Stage 6's golden set — a live
verification run should not leave synthetic data indistinguishable from
real research sitting in the dev database.

**What this does not prove.** It does not prove 2 is the *right* number
of correction rounds — that's a disclosed, uncalibrated choice (Section
3). It does not prove the restated-interpretation prompt format is
optimal, only that it works for the one live correction tried. And it
does not touch anything downstream of `confirm_charter` — whether a
corrected charter behaves identically to an equivalent charter that
never needed correction, once hypothesis generation runs against it, is
untested here because nothing about `Hypothesis`, `hypothesis.py`, or the
execution loop changed in this component.

---

## 6. Interview defense

**"Why does a correction call the same `parse_charter` function instead
of a purpose-built 'apply this edit' function that would obviously be
faster and cheaper?"** Because `docs/architecture.md` names exactly two
places this system is allowed to let an LLM be fuzzy, and a correction is
squarely the first one, invoked again — not a third mechanism. A
purpose-built editor would duplicate `_charter_prompt`'s own prompt
engineering (the real sector/industry pairing constraint that exists
because of a confirmed bug) with none of its track record, and it would
be a second surface area to keep in sync with the first every time the
charter schema changes.

**"Why cap corrections at 2 instead of letting her keep going until she's
happy?"** Because every round is a real LLM call with real hallucination
risk and real cost, not a free retry — this project already tracks and
bounds "how many chances has the model had" for hypothesis testing
(`.claude/rules/agent-honesty.md`'s multiple-comparisons correction), and
an unbounded loop one stage earlier, before a charter is even confirmed,
is the same category of risk. The honest caveat: 2 is a considered
starting point, not a number backed by usage data, since this is the
first time the feature has existed.

**Hard question: "You found and fixed two real bugs in `confirm_charter`
— code that predates this component and had shipped as part of Stage 5.
Doesn't that mean Stage 5's own verification was incomplete?"** Yes, and
worth saying plainly rather than softening: `confirm_charter` had zero
test coverage before this component (confirmed by grep, not assumed),
and its only real exercise was live, through a CLI script whose own
control flow happened to prevent both failure modes from ever being
triggered — it never confirms a blocked charter because it checks that
itself before offering the prompt, and it never passes a bad id because it
always uses one `create_charter` just returned. That's a real gap: a
function's correctness quietly depended on every future caller
reimplementing a specific script's own discipline, and nothing enforced
that assumption anywhere near the function itself. The reason it surfaced
now rather than earlier is exactly why this stage exists — Stage 7 is the
first time anything other than that one disciplined CLI script calls
these functions, and an HTTP endpoint has no equivalent built-in
discipline unless the function itself provides it.

**"Isn't storing a whole new database row for every single correction
kind of wasteful, compared to just tracking a version number on one
row?"** At the scale this system actually operates at — a handful of
charters, at most three rows per corrected chain — the storage cost is
irrelevant, and the alternative (mutate in place, track a counter) throws
away exactly the thing worth keeping: what she actually saw and reacted
to at each step. This project already made the identical tradeoff for
`StudyDesign` and defended it the same way — immutability costs a little
storage and buys a permanent, honest record instead of a value that
silently changed underneath whoever last looked at it.

---

## 7. What comes next and why

Component 3 (the Vite/React app shell) is next per `docs/plans/
stage-7-plan.md`, followed by Component 4 (the actual charter-creation
flow — the mandate textarea and confirmation screen this component's
endpoints exist to serve). The confirmation screen's design can now
assume real backend support for "here's what changed, try again" rather
than only "start over," which is what motivated building this component
before the frontend that will visibly use it.

**If this component were wrong** — if a correction's combined prompt
didn't actually reach the model correctly, or if the round cap didn't
enforce, or if `confirm_charter`'s new blocked-guard had a gap — the
failure would most plausibly show up as either a charter confirmed with
an empty universe silently reaching `propose_hypothesis` (which does
enforce confirmation, but has no reason to ever suspect the universe
inside a confirmed charter is empty, since confirmation is supposed to
mean it was checked), or as a correction chain that quietly loses the
connection between what she said and what changed, making a "why did
round 2 look like this" question unanswerable months later — exactly the
category of failure the immutable-row design in Section 3 exists to
prevent.
