# Stage 5, Component 6b: the live execution loop

## 1. What this component does

Component 6a built the execution loop and proved its guarantees against a
fake tool session and a scripted fake LLM — no network, no subprocess, no
money spent. **6b is where that loop meets reality**: a real MCP server
running as a separate OS process over stdio, and real Claude on Bedrock
deciding what to call next.

The graph itself did not change. Both the tool session and the LLM were
injected parameters from the start, so 6b swaps what gets passed to
`build_graph` and nothing about the machine that receives them. That was
the entire point of splitting the component in two, and it held.

What 6b adds beyond the swap is **bounded retry-with-feedback** in
`decide_next_action` — a real addition to the safety model, not a patch,
and the resolution of a decision `llm_client` had explicitly deferred since
Stage 3.

**What exists now that did not before:** `scripts/run_study.py`, the live
runner; a retry loop that survives malformed model output and charges every
attempt against the budget; a `decision_failed` graph node that converts a
crash class into a recorded outcome; a `Rejection` record that captures when
the model genuinely attempted a forbidden action; prompt compaction that
keeps a study from blowing its context; and — for the first time in this
project — **real observed data about what an agentic study actually costs**.

**Scope boundaries.** 6b still does not synthesize a verdict, apply the
falsification condition, or set a hypothesis to confirmed/rejected. Every
live run in this document ends with the hypothesis still `testing`, exactly
as designed. It also does not add crash resilience in general: the retry
handles *one* failure class (the model producing output that will not
validate), and an MCP subprocess dying or a database error still crashes
and still leaves an unresumable `running` row.

New: `scripts/run_study.py`. Modified: `src/agentic_core/loop_graph.py`,
`src/agentic_core/loop_state.py`, `src/llm_client/__init__.py`, and both
test modules.

---

## 2. Every meaningful line explained

### `scripts/run_study.py` — launching a real MCP server

```python
params = StdioServerParameters(
    command=os.path.abspath(".venv/bin/python3"),
    args=["-m", "mcp_tools.server"],
    cwd=os.getcwd(),
)
async with stdio_client(params) as (read, write):
    async with ClientSession(read, write) as session:
        await session.initialize()
        graph = build_graph(lambda: session, llm, design_id=..., hypothesis_id=...)
        final = await graph.ainvoke(initial_state(charter, hypothesis, design))
```

This is the same launch path Stage 4's gate script proved in
[step-09](../stage-4/step-09-manual-mcp-verification.md): a genuinely
separate OS process, real bytes over stdio pipes, and a real MCP
initialization handshake before any tool may be called.

The session is opened **once for the whole study**, not per tool call, and
passed in as `lambda: session`. Opening it per call would re-launch a
Python process that imports pandas, pandas-ta, and torch every time — on
the observed runs, tool execution totalled 6.9 seconds across four calls;
per-call subprocess spawning would have dwarfed that many times over. The
callable indirection (`lambda: session` rather than the session itself)
exists so `build_graph`'s signature does not require a live object at graph
construction time, which is what lets the tests pass a fake.

`await session.initialize()` is not optional politeness — it is the MCP
handshake, and a client that skips it is not permitted to list or call
anything.

### `TracingLLM` — observation that lives outside the graph

```python
class TracingLLM:
    def __init__(self):
        self.decisions: list[tuple[str, str, float]] = []

    def __call__(self, prompt: str, response_model):
        started = time.monotonic()
        try:
            result = structured_output(prompt, response_model=response_model)
        except StructuredOutputError as e:
            print(f"  step {len(self.decisions) + 1:>2}  LLM OUTPUT REJECTED -- raw tool_use input:")
            for block in e.raw_response.content:
                if block.type == "tool_use":
                    print(f"    {block.input!r}")
            raise
        ...
```

A wrapper around `structured_output`, deliberately **not** an edit to
`decide_next_action`. The loop's behavior must be identical whether or not
anyone is watching it; if instrumentation lived inside the node, the
observed run and the unobserved run would be different code paths, and the
thing being measured would not be the thing that runs in production.

The `except` block earns its place through a specific experience described
in section 5: when the first live runs failed, Pydantic reported only a
*truncated repr* of the offending value, which was not enough to tell a
malformed payload from a mis-specified schema. Printing the raw `tool_use`
input before re-raising is what turned an opaque failure into a diagnosable
one. It re-raises rather than swallowing, because this class only observes.

### `_compact` — the prompt-size bug, caught before it cost anything

```python
_MAX_LIST_PREVIEW = 3

def _compact(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _compact(v) for k, v in value.items()}
    if isinstance(value, list) and len(value) > _MAX_LIST_PREVIEW:
        head = ", ".join(repr(_compact(v)) for v in value[:_MAX_LIST_PREVIEW])
        return f"[{len(value)} items: {head}, ... last={_compact(value[-1])!r}]"
    if isinstance(value, list):
        return [_compact(v) for v in value]
    return value
```

Every diagnostic tool returns a full time series. `get_price_data`,
`compute_indicator`, and `classify_regime` each return one record per
trading day — **3,331 rows** for this project's real AAPL in-sample window.
Rendered raw into the prompt, that is roughly **320,000 characters per
call**, compounding at every subsequent step, which would exhaust the
context window and bill real money for text no model needs to read row by
row. `BacktestResult` has the same shape problem in miniature, via
`trade_returns` and `exit_bars`.

The recursion order matters: dicts are walked first so nested series get
caught wherever they sit, and the `len(value) > _MAX_LIST_PREVIEW` check
precedes the plain-list branch so short lists survive intact rather than
being needlessly stringified.

**Why this is safe, and why the same reasoning would not license
truncating the trace:** Component 7 validates every verdict claim against
the `tool_call_traces` **table**, which stores the full untruncated payload.
The prompt is not what the fabrication check reads. Shrinking the prompt
therefore cannot weaken that check — but shrinking the *stored trace* would
destroy it outright, which is why `_compact` is applied only in
`_window_summary` and never on the path into the database. A dedicated test
asserts the stored payload still contains all 500 elements after a run.

### The retry loop in `decide_next_action`

```python
model = build_decision_model(state)
base = _decide_prompt(state)
prompt = base
rejections: list[Rejection] = []

for attempt in range(1, MAX_DECISION_ATTEMPTS + 1):
    try:
        decision = llm(prompt, response_model=model)
    except StructuredOutputError as e:
        raw = _raw_tool_input(e)
        kind, detail = classify_rejection(raw, state)
        rejections.append(Rejection(...))
        prompt = _retry_prompt(base, attempt, raw, detail, str(e), offered_actions(state))
        continue

    return {
        "pending_action": decision.decision,
        "step_count": state["step_count"] + attempt,
        "rejections": rejections,
    }
```

The load-bearing detail is on the first line: **`model` is built once,
before the loop, and reused unchanged for every attempt.** That is the
entire safety argument for retrying a guarantee violation. A locked
diagnostic tool is still absent from the enum on attempt three; `Conclude`
is still absent from the union. A retry is a second chance to pick
something *legal*, never a second chance at the forbidden thing. If the
model were rebuilt from a mutated state between attempts, that argument
would collapse — and a test asserts two consecutive builds produce
byte-identical schemas.

`step_count + attempt` rather than `step_count + 1` is the budget decision
made concrete; section 3 covers why.

The `rejections` list accumulates across attempts and is returned in the
state update, where `operator.add` appends it — the same append-only
reducer `results` uses, so no later step can erase the record that the
model misbehaved.

```python
return {
    "pending_action": None,
    "step_count": state["step_count"] + MAX_DECISION_ATTEMPTS,
    "rejections": rejections,
    "failure_reason": (...),
}
```

When all attempts fail, returning `pending_action=None` routes to a clean
recorded failure. Before this existed, `StructuredOutputError` propagated
and crashed the graph, leaving `StudyRun.status='running'` with orphaned
traces and no recorded reason — which is exactly what happened on this
component's first two live runs, twice requiring manual database cleanup.

### `classify_rejection` — why it inspects intent, not error text

```python
decision = raw_input.get("decision") if isinstance(raw_input, dict) else None

if isinstance(decision, str):
    try:
        decision = json.loads(decision)
    except json.JSONDecodeError:
        return "encoding", ("`decision` was a JSON-encoded STRING, and a malformed one. ...")
...
action = decision.get("action")
available = offered_actions(state)

if action not in available:
    if action == "conclude":
        return "guarantee_violation", ("`conclude` is not available: ...")
```

This decides whether a rejected output was an **encoding failure** (the
model's intent was legal, serialization broke) or a **guarantee violation**
(the model genuinely tried to do something forbidden) — and it produces the
feedback string in the same pass.

It classifies by inspecting **what the model actually named**, not by
pattern-matching Pydantic's error message. Error strings are formatting,
not API: they change between Pydantic versions with no deprecation cycle. A
classifier built on them would silently start mislabelling every rejection
after a routine upgrade — the exact category of quiet degradation that
makes a system's own self-reports untrustworthy. Comparing `action` against
`offered_actions(state)` is stable because both are this project's own
values.

### `_retry_prompt` — and why explicit feedback is safe

```python
return f"""{base}

--- YOUR PREVIOUS RESPONSE WAS REJECTED (attempt {attempt}) ---
You emitted: {str(raw)[:600]}

Rejected because: {detail}

Validation error: {error[:400]}

`decision` must be a nested JSON OBJECT, never a JSON-encoded string.
Actions available to you right now: {available}
Emit one valid decision now.
"""
```

Three pieces of feedback: what it emitted (showing a model its own
malformed output is the strongest available correction signal), why it was
rejected, and what is legal right now.

The question worth answering directly is whether telling the model
*"`advance_phase` is not available: this window still needs a successful
run_backtest AND test_significance"* teaches it to game the gate. It does
not, and the reason is structural rather than a matter of careful wording.
**The gate reads real `tool_call_traces` rows, not the model's claims.**
There is no perfunctory way to satisfy "test_significance must have
succeeded" — the tool either executed and returned `is_error=False` or it
did not. So the only behavior this feedback can produce is the model doing
the required work, which is the intended path. Feedback that restates the
schema is safe precisely because the schema is the enforcement.

The truncations (`[:600]`, `[:400]`) exist for the same reason `_compact`
does: a raw payload could itself be enormous.

### The `decision_failed` node and routing order

```python
def route_after_decide(state: LoopState) -> str:
    if state["pending_action"] is None:
        return "decision_failed"
    if state["step_count"] > MAX_STEPS:
        return "budget_exhausted"
    ...
```

The exhausted-retries check comes **first**, deliberately. Three failed
attempts can push `step_count` past `MAX_STEPS` on their own, so both
conditions can be true simultaneously — and "the model could not produce a
valid decision" is the more specific and more actionable diagnosis.
Reporting it as budget exhaustion would hide a schema problem behind a cost
message, sending a future reader to tune a number when the real fault was
structural.

Both `decision_failed` and `budget_exhausted` map to `make_finalize("failed")`.
Two nodes rather than one because the graph's own topology is the clearest
place to see that there are two distinct ways to fail, and neither reaches
`conclude`.

### The `llm_client` docstring correction

`llm_client`'s module docstring previously said the module "gains retries in
Stage 5." That promise is now resolved — in the opposite direction — and the
docstring was rewritten to record it rather than left to read as unfulfilled.

The reasoning, preserved in the file itself: three callers deliberately do
not retry, each for a documented reason. `charter.py` is human-mediated, so
re-running the script *is* the retry. `hypothesis.py` raises
`DuplicateHypothesisError` specifically so the caller decides.
`study_design.py` raises `InsufficientHistoryError` on the same reasoning. A
general retry underneath `structured_output` would silently override all
three. The docstring now also notes the load-bearing consequence: **a caller
cannot implement a budgeted retry on top of a function that has already
silently retried**, so the no-retry contract is what makes the loop's
version possible.

### What was skipped

Genuine boilerplate: imports, `_load` (a plain three-row database fetch),
`_raw_tool_input` (a loop over response blocks), and the summary `print`
statements at the end of the runner.

---

## 3. Design decisions and rejected alternatives

### Retry lives in the loop, not in `llm_client`

Covered in section 2. Stating the trade explicitly: **chosen** — retry in
`decide_next_action`, the one caller that runs unattended with a budget.
**Alternative** — a general retry inside `structured_output`, which is what
the original docstring anticipated. **Rejected because** it would have
changed behavior for three callers that deliberately raise instead, each
with reasoning already recorded in their own docstrings; a "helpful"
retry underneath them would have overridden three deliberate decisions
invisibly. **Load-bearing and low cost to maintain**, since the deviation is
now recorded in both places rather than only remembered.

### Every retry attempt costs a step

**Chosen:** `step_count + attempt`, so three attempts consume three steps.

**Alternative considered:** make retries free, on the reasoning that a
retry is not *progress* and a study should not fail because of the model's
serialization quirk.

**Why rejected:** the budget is a **cost** control, not a progress control.
Its natural unit is "LLM calls made", because that is what Bedrock bills.
Under a free-retry policy, a persistently malformed model would generate
unbounded billable calls while `step_count` stayed frozen — precisely the
runaway `MAX_STEPS` exists to stop, reintroduced through the mechanism
meant to add robustness. The counter-argument is real but not decisive, and
the observed data settles it: the live runs used 6 and 12 steps against a
budget of 40, so even two retries on every single step would have landed
comfortably inside it.

The property this buys is worth naming: **`MAX_STEPS` now means exactly
"maximum LLM calls per study"** — one defensible, billable unit — rather
than a number whose meaning drifts with how often the model misbehaved.

**Cost to reverse:** one line. But reversing it would silently uncap spend.

### Three attempts, and what to do if that is not enough

**Chosen:** `MAX_DECISION_ATTEMPTS = 3` (one initial call plus two retries).

**Alternatives:** two (cheaper, ~25% residual failure if attempts are
independent at ~50%), or five (more robust, more spend on a model that is
probably stuck).

**Why three:** it is where the *diagnosis* changes rather than merely the
odds. A model that fails validation three times against an unchanged schema
is probably not hitting transient noise — it is hitting a schema it cannot
satisfy, and a fourth call will not discover otherwise. Better to fail
cleanly with the raw output recorded than to keep paying.

The commitment recorded in the code comment matters as much as the number:
**if live runs start exhausting this, the fix is to restructure the schema
to remove the nested `decision` object that provokes stringification in the
first place — not to raise the attempt count.** Raising it would be treating
a symptom while the real defect (a nesting shape the model handles poorly)
stayed in place and kept costing money.

### `Rejection` recorded in state, not in a database table

**Chosen:** an append-only `rejections` list in `LoopState`.

**Alternative considered:** a `rejected_decisions` table, migrated now.

**Why deferred:** Component 7 owns verdict disclosure — including
architecture.md's requirement to disclose "this is hypothesis 34 under this
charter." What a rejection record needs to look like on disk depends on what
Component 7 decides to disclose about it, and guessing that schema now means
either migrating twice or living with a shape that does not fit.

**The disclosed cost is real and worth stating plainly:** because there is
also no checkpointer, these records die with the run. If the agent attempts
five forbidden actions and then completes successfully, that fact is
currently visible only in the live console output and is lost afterwards.
That is an acceptable gap for one component, not indefinitely — it is
Sacred Gate 2 evidence, and Gate 2 evidence should eventually be durable.

### Prompt compaction rather than a smaller tool surface

**Chosen:** keep all three diagnostic tools available and compact their
output in the prompt.

**Alternative considered:** drop `get_price_data` and `compute_indicator`
from the loop's vocabulary entirely, since both return bulk series the
agent arguably should not be reading row by row anyway.

**Why rejected:** that would have been the third and fourth exclusion from
the tool list `docs/architecture.md` §5 Step 4 specifies, on top of the
three already removed in Component 6a — narrowing the agent's investigative
range for what is fundamentally a formatting problem. Compaction solves the
cost issue without removing capability: the agent still learns that a series
was returned, its length, and its endpoints, which is enough to decide what
to do next. **Reversible** — if the agent is later observed making bad use
of raw series, removing them is still available.

---

## 4. Concepts introduced

**Structured output via forced tool use, and how it fails.** Getting a
language model to return machine-readable data can be done by asking for
JSON in prose and parsing the reply, or by giving the model a *tool* whose
input schema is the desired shape and forcing it to call that tool. This
project uses the second (see `llm_client`), because the schema constrains
generation rather than merely being checked afterwards. What 6b revealed is
that this is reliable but **not total**: the model can satisfy the tool call
while mis-encoding a nested field — emitting an inner object as a JSON
*string* rather than an object. The lesson generalizes beyond this project:
schema-constrained generation eliminates whole classes of malformed output
but does not eliminate serialization ambiguity at nesting boundaries, so
production code must still handle validation failure as an expected event
rather than an impossible one.

**Retry-with-feedback, and why it is different from plain retry.** Plain
retry re-sends the identical request and hopes for different luck — which
works for genuinely transient failures (a network blip) and does nothing at
all for a systematic one. Retry *with feedback* appends what went wrong to
the next request, so the model has new information rather than another
identical roll. The critical design constraint, and the thing that makes it
safe here, is that **the constraint being violated must not move between
attempts**. Feedback tells the model what is legal; it never expands what is
legal. A retry implementation that loosened validation after repeated
failure — a tempting "be permissive on attempt three" — would convert a
robustness feature into a guarantee bypass.

**Cost calibration versus cost estimation.** `MAX_STEPS = 40` was an
estimate: six windows times roughly four calls, plus headroom. The observed
figure is exactly 3.0 steps per window across two structurally different
designs. The general point is that an estimate and a measurement are
different kinds of claim, and conflating them is how systems acquire numbers
nobody can defend. This project has now done this twice — Stage 4 revised
`n_resamples` from 999 to 300 on a real timing measurement, and 6b measured
what a study actually costs — and in both cases the honest record is "the
number was chosen for this reason, then measured, and here is what the
measurement said," not a claim that the original guess was correct.

**Why an unresumable crash is a design consequence, not a bug.** Component
6a chose not to configure LangGraph's checkpointer, on the reasoning that
`study_runs` and `tool_call_traces` already are the durable record and a
second persistence layer could disagree with the first. The disclosed cost
was that a crashed run is not resumable mid-step. That cost arrived on the
very first live run: two crashes each left `status='running'` with orphaned
traces, requiring manual cleanup before re-running. This is what it looks
like when a documented tradeoff is genuinely a tradeoff rather than a
rationalization — the predicted cost showed up, on schedule, in the
predicted form.

---

## 5. How this component was verified

### The bug only a live model could find

Component 6a's 41 tests all passed. The very first live run failed at step
3 with:

```
AgentDecision failed validation: 1 validation error for AgentDecision
decision
  Input should be a valid dictionary or object to extract fields from
  [type=model_attributes_type, input_value='{"action": "advance_phas...
```

Claude had emitted the nested `decision` object as a **JSON string**. No
fake LLM would ever have produced this, because a fake constructs the model
directly; the failure lives entirely in the serialization boundary between a
real model and Pydantic.

Where it failed is itself informative: **step 3, `advance_phase`** — meaning
the gating had correctly unlocked advancement after `run_backtest` and
`test_significance` both succeeded in window 0. The loop's logic was right;
its tolerance for real-world output was not.

A first fix — a `field_validator(mode="before")` parsing stringified
decisions — was added, and the second live run **failed identically**. The
initial conclusion ("the fix did not take effect") was wrong, and the real
explanation came from the reprs: both ended `."}}'`, an **extra trailing
brace**. The string was not valid JSON, so `json.loads` raised, and the
validator correctly passed the value through to a real error rather than
masking it. The third live run then *succeeded* — not because the fix
handled the failure case, but because the model happened to emit a proper
object that time.

That sequence is worth recording precisely, because the tempting reading of
run three was "fixed." It was not. Three live runs produced **fail, fail,
succeed** on an intermittent bug, and treating the green run as proof would
have shipped a loop that crashes roughly two thirds of the time.

### What the automated tests prove

Suite: **287 passing** (up from 276). The new coverage:

- `test_retry_recovers_from_the_real_malformed_output` replays the exact
  malformed string, trailing brace included, and confirms the run completes.
- `test_every_retry_attempt_costs_a_step` compares a clean run against one
  with two forced failures and asserts the step delta is exactly 2.
- `test_exhausted_retries_fail_cleanly_instead_of_crashing` confirms
  `status='failed'`, a recorded `failure_reason`, and a finalized row —
  rather than the crash-and-orphan behavior observed live.
- `test_retry_feedback_names_what_was_wrong_and_what_is_available` asserts
  the retry prompt actually carries its three pieces.
- `test_a_guarantee_violation_is_recorded_as_such_not_as_encoding` confirms
  a real forbidden action is not filed as serialization noise.
- `test_retry_cannot_smuggle_a_forbidden_action_through` asserts two
  consecutive schema builds are identical — the safety argument for
  retrying violations at all.
- `test_compaction_does_not_touch_the_stored_trace` confirms a 500-element
  series survives intact in the database after compaction ran in the prompt.

Each was **mutation-verified**: making retries free, removing the
exhausted-retries route, and misclassifying violations as encoding each
broke exactly the test that should catch it, and the files were confirmed
byte-identical to their pre-mutation state afterwards.

### What the live runs prove — and the distinction that matters

Two live runs after the retry landed:

| Design | Windows | Steps | Per window | Budget used | Rejections |
|---|---|---|---|---|---|
| `simple_holdout` (the design that crashed twice) | 2 | 6 | 3.0 | 15% | 0 |
| `walk_forward` | 4 | 12 | 3.0 | 30% | 0 |

Five `advance_phase` decisions across the two runs — the exact decision that
previously failed — all succeeded.

**The honest distinction, stated because it is easy to overclaim here:**
these live runs prove **the loop now completes reliably**. They do *not*
prove **the retry mechanism recovers correctly**, because the retry path was
never exercised — the model simply did not misbehave. Recovery is proven by
the unit tests, which replay the real malformed output deterministically.
Two different claims, two different pieces of evidence, and conflating them
would mean claiming the retry works on the basis of runs where it never ran.

Given the bug's measured intermittency, five clean decisions is meaningful
but not conclusive. If it resurfaces, the committed response is schema
restructuring, not a higher attempt count.

### MAX_STEPS calibration

Exactly **3.0 steps per window** on both designs — two tool calls plus one
transition. The relationship is linear: `steps ≈ 3 × windows`. A six-window
study extrapolates to ~18 steps, 45% of budget; exhausting 40 would require
a 13-window design that no plausible `walk_forward_folds` produces.

**The budget was deliberately left at 40.** Tightening to 20 is defensible
on these two points, but both runs used **zero diagnostic tools** — the agent
never branched into investigation, because neither result invited it (an
in-sample Sharpe of 0.65 at p=0.53 is already dead). The branching path that
architecture.md calls "the agency" has therefore never been observed live,
and it is precisely the path that would consume extra steps. Calibrating a
budget on two runs that never exercised the expensive branch would be
fitting to the easy case.

### What this does NOT prove

- **The diagnostic tier has never run live.** Neither run called
  `classify_regime`, `compute_indicator`, or `get_price_data`. Their
  argument construction, their compaction under real payloads, and the
  agent's judgment about when to use them are all unexercised outside tests.
- **Crash resilience is still one class only.** An MCP subprocess dying or
  a database error still crashes and still leaves `status='running'`.
- **This is not Sacred Gate 2.** 6b proves the loop runs against a real
  model without fabricating its own dates or skipping the control. Gate 2
  requires proving the agent never fabricates *in a verdict* and kills
  hypotheses when evidence says to — both Component 7.
- **Rejection records are not durable.** No live rejections occurred, but if
  they had, they would have vanished with the run.

### An error made during verification, and its cost

While mutation-testing the retry, `git checkout src/agentic_core/loop_state.py`
was run to undo a mutation. That file held uncommitted work, so the command
discarded the whole session's additions to it — `Rejection`,
`classify_rejection`, `offered_actions`, the `rejections` state field, and
the stringified-decision validator. All five were rewritten and
re-verified: suite green at 287, the mutation re-run properly using a file
copy, and the restored file confirmed byte-identical to its backup. Nothing
was permanently lost, but the mistake was avoidable — `cp` backups had been
used for every other mutation, and `git checkout` was reached for on the one
file where it was destructive.

---

## 6. Interview defense

**"You had 41 passing tests and the first real run failed immediately. What
does that say about your testing?"** That fakes cannot test the boundary
between your code and someone else's system. Every guarantee I claimed —
no lookahead, evidence before diagnosis, no window left untested — held
perfectly on the live run; what broke was serialization at a nesting
boundary, which no fake LLM can produce because a fake constructs the model
object directly. The right conclusion is not that the fake tests were
wasted: they are why I could tell instantly that the failure was an
encoding problem and not a logic problem, because everything else was
already proven. The lesson I took is narrower and more useful than "test
with real systems more" — it is that **validation failure from a model is
an expected runtime event, not an impossible one**, and production code
needs a bounded, budgeted response to it.

**"Why didn't you just put the retry in `llm_client`, where your own
docstring said it would go?"** Because when I got there, the docstring's
assumption turned out to be wrong, and I would rather correct a stale plan
than follow it. Three callers deliberately do not retry — the charter parser
is human-mediated so re-running the script *is* the retry, and the
hypothesis and study-design functions raise specifically so the caller
decides. A general retry underneath `structured_output` would have
overridden all three silently. There is also a structural argument: a caller
cannot implement a *budgeted* retry on top of a function that has already
retried invisibly, because the accounting is gone. So the no-retry contract
in `llm_client` is what makes the loop's version possible, and I rewrote
that docstring to record where retry actually landed and why.

**Hard question: "You retry when the model attempts a forbidden action.
Isn't that just giving it more chances to break your guarantees?"** It would
be, if the schema moved between attempts. It does not — the response model
is built once, before the retry loop, and reused unchanged, so a locked tool
is still absent from the enum on attempt three. A retry is a second chance
to pick something legal, never a second chance at the forbidden thing, and I
have a test asserting two consecutive builds produce identical schemas
specifically so that property cannot rot. The related worry is the feedback:
I tell the model *why* it was refused, including that advancing requires the
control to have run. That sounds like coaching it past the guard, but the
gate reads real trace rows rather than the model's claims — there is no
perfunctory way to satisfy "test_significance succeeded", because the tool
either executed and returned cleanly or it did not. So the only behavior
that feedback can produce is the model doing the required work.

**Hard question: "Your two live runs both completed with zero retries. How
do you know the retry works?"** I do not know it from those runs, and I
would not claim it from them. Those runs prove the loop completes reliably
now; the retry path was never exercised because the model did not misbehave.
What proves recovery is the unit test that replays the exact malformed
string from the real failure — trailing brace included — and asserts the run
completes with the rejection recorded. Those are two different claims backed
by two different kinds of evidence, and I keep them separate deliberately,
because "it worked twice" on an intermittent bug that previously failed two
of three times is not strong evidence of anything.

**Honest weaknesses.** The diagnostic tool tier has never run live, so the
most interesting branch of the loop — the agent choosing to investigate a
disappointing result, which is the behavior architecture.md calls "the
agency" — remains unobserved outside tests. `MAX_STEPS` is calibrated only on
runs that never took that branch, which is why I left it at 40 rather than
tightening it on data that does not cover the expensive case. Rejection
records are not durable, so evidence of the model attempting forbidden
actions currently dies with the run. And crash resilience covers exactly one
failure class; a dead subprocess still leaves an unresumable row.

---

## 7. What comes next and why

**Component 7** is the verdict, and it is where Sacred Gate 2 is actually
satisfied. It reads the `tool_call_traces` rows this loop wrote, applies the
pre-registered falsification condition mechanically, validates that every
quantitative claim resolves to a real trace, applies the
multiple-comparisons correction, and sets the hypothesis's final status. It
is also where two things deferred here get decided: whether `Rejection`
records need a table, and whether `failure_reason` needs a column on
`StudyRun`.

The walk-forward run produced exactly the kind of evidence Component 7 will
have to reason about honestly: Sharpe 0.77, then **−1.51**, then 0.55, then
0.94, with p-values 0.43, 1.00, 0.79, 0.31. That is not clean decay — it is
instability, no fold beat randomized entries, and one fold was
catastrophic. Against a pre-registered bar of `sharpe_ratio < 0.5` this is
an unambiguous kill, and an agreeable model looking at fold 3's 0.94 in
isolation could write a much friendlier story. That tension is the whole
reason Component 7 applies the condition in code rather than asking the
model to.

**If 6b were wrong, here is how it would surface.** A broken retry would
show up as studies failing intermittently with `decision rejected 3 times`
in `failure_reason` — noisy but honest, and self-announcing. The dangerous
failure is subtler: if `_compact` were ever applied on the path *into* the
database rather than only into the prompt, Component 7 would validate
claims against truncated evidence and would pass claims it should reject,
producing verdicts that look fully sourced while resting on partial data.
That is why the test asserting the stored trace stays complete exists, and
why it asserts on a length rather than on the presence of a field.
