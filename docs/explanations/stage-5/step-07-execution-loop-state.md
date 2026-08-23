# Stage 5, Component 6a: the execution loop — state, gating, and graph

## 1. What this component does

This is the machine that actually *spends* a `StudyDesign`. Component 5
produced a pre-registered plan — which calendar windows are in-sample,
which are out-of-sample, and in what order. Component 6 walks that plan:
at each step an LLM chooses one action, deterministic code executes it
against the current window, and the result is appended to an
append-only trace. It repeats until every window has been tested, the
agent concludes, or the step budget runs out.

Component 6 is split in two. **6a — this document — is the entire
deterministic half**: the state object, the tool tiers, the gating rules,
the dynamically-built response schema, and the LangGraph node graph. It is
verified end to end with **no network, no MCP subprocess, and no Bedrock
spend**, driven by a fake tool session and a deliberately hostile fake
LLM. **6b** wires in the real MCP client and real Claude, and performs the
live run.

Splitting this way is the whole point: every guarantee this component
makes is proven in 6a, where proof is cheap, fast, and repeatable. Nothing
load-bearing waits on a live model whose output is non-deterministic and
costs money to sample.

**What exists now that did not before:** a compiled LangGraph state machine
with five real nodes; a state object whose evidence list can only ever be
appended to; three structural guarantees enforced by *schema omission*
rather than by validation; and 41 tests, every one of which has been
confirmed to fail when the guarantee it protects is deliberately broken.

**Scope boundaries — what 6a does NOT do.** It does not synthesize a
verdict, does not apply the pre-registered falsification condition, and
does not set a hypothesis to confirmed, rejected, or inconclusive. It sets
`hypothesis.status = 'testing'` on entry and leaves it there; every outcome
decision belongs to Component 7. It also does not talk to a real LLM or a
real MCP server — both are injected as arguments, and 6a only ever passes
in fakes.

New files: `src/agentic_core/loop_state.py`,
`src/agentic_core/loop_graph.py`,
`tests/agentic_core/test_loop_state.py` (31 tests),
`tests/agentic_core/test_loop_graph.py` (10 tests), and migration
`ac225385b472`. Modified: `src/agentic_core/db/models.py` (one new column),
`src/backtester/strategies/rule_strategy.py` (one new helper),
`tests/agentic_core/conftest.py` (one new fixture).

---

## 2. Every meaningful line explained

### The three guarantees, stated up front

`loop_state.py`'s module docstring names three guarantees, and the whole
module exists to make each one true *structurally* rather than by checking:

1. **No lookahead.** The action schema has no date field.
2. **Evidence before diagnosis.** Diagnostic tools are absent from the tool
   enum until the current window holds a successful evidence result.
3. **No window left untested.** Advance and Conclude are absent from the
   action union until the current window's backtest *and* control have
   both succeeded.

Every one of these is enforced the same way: **by what the LLM's response
schema does not contain.** That sentence is the single most important idea
in this component, and section 3 explains why it is categorically stronger
than the alternative.

### The tool tiers

```python
EVIDENCE_TOOLS   = ("run_backtest", "test_significance", "confidence_interval")
DIAGNOSTIC_TOOLS = ("classify_regime", "compute_indicator", "get_price_data")
RULE_TOOLS       = frozenset({"run_backtest", "test_significance", "confidence_interval"})
INDICATOR_TOOLS  = frozenset({"compute_indicator"})
```

Six tools, not the nine that `mcp_tools/server.py` exposes. `EVIDENCE_TOOLS`
are the ones that produce numbers a verdict can rest on; `DIAGNOSTIC_TOOLS`
explain a number that already exists. That split is what guarantee 2
operates on. `RULE_TOOLS` is the set whose MCP signature takes a `rule`
argument, so `execute_tool` knows when to inject the frozen rule;
`INDICATOR_TOOLS` is the one tool that needs an indicator name.

Three MCP tools are deliberately excluded from the loop's vocabulary
entirely — a disclosed deviation from `docs/architecture.md` §5 Step 4's
tool list, raised and confirmed before it was built. The reasoning is
recorded in the module itself so it is discoverable from the code:
`correct_p_values` takes `p_values: list[float]`, i.e. numbers the LLM
types by hand, which is the model transcribing statistics and able to
transcribe selectively; `screen_universe` would let the agent re-resolve
the universe mid-study, changing the experiment's population after seeing
results; `list_indicators` is pointless when the rule is frozen and the
agent cannot change indicators anyway.

### `ToolResult` and `LoopState`

```python
class ToolResult(BaseModel):
    step_index: int
    window_index: int
    tool_name: str
    arguments: dict[str, Any]
    result: dict[str, Any]
    is_error: bool
```

`window_index` is stamped at write time rather than derived later from the
dates. Component 7 has to attribute a claim ("out-of-sample Sharpe was
0.21") to the window that produced it; reconstructing that by matching
date arguments would work today but breaks the moment two windows share a
boundary date or a tool records its dates in a different form.

```python
class LoopState(TypedDict):
    study_run_id: str
    charter: Charter
    hypothesis: Hypothesis
    design: StudyDesign
    windows: list[DateRange]
    window_index: int
    step_count: int
    pending_action: Any | None
    results: Annotated[list[ToolResult], operator.add]
    status: Literal["running", "completed", "failed"]
    failure_reason: str | None
```

The load-bearing line is `results: Annotated[list[ToolResult],
operator.add]`. In LangGraph, a field's annotation can carry a *reducer* —
a function that combines the existing value with whatever a node returns.
`operator.add` on a list means **append**. There is deliberately no reducer
here that overwrites, which means no node can edit or delete evidence
already recorded. If a node returns `{"results": [r]}`, that result is
added; there is no return value it could produce that removes an earlier
one. This mirrors the append-only `tool_call_traces` table on the database
side — the same property enforced twice, in memory and on disk.

Every other mutable field is a plain scalar that a node replaces wholesale,
which is LangGraph's default behavior when no reducer is given.

### `flatten_windows`

```python
def flatten_windows(design: StudyDesign) -> list[DateRange]:
    if design.walk_forward_windows is not None:
        return list(design.walk_forward_windows)
    return [design.in_sample, design.out_of_sample]
```

Turns either design type into one ordered list, so the loop **never
branches on `design_type` anywhere**. A `simple_holdout` is just a
two-window study. This uniformity is the direct payoff from Component 5's
decision to keep `in_sample`/`out_of_sample` readable identically in both
branches — if that decision had gone the other way, every node in this
graph would need a design-type conditional.

The `walk_forward_windows` branch returns the list *whole* rather than
concatenating `in_sample` onto it, because `walk_forward_windows[0]` **is**
`in_sample` (see `StudyDesign`'s docstring in
[schemas.py](../../../src/agentic_core/schemas.py)). Concatenating would
test the in-sample period twice and shift every window index by one,
silently corrupting every `window_index` stamped into the trace table.
There is a dedicated test for exactly this.

### The gating functions

```python
def _successful_tools_in_window(state, window_index) -> set[str]:
    return {
        r.tool_name for r in state["results"]
        if r.window_index == window_index and not r.is_error
    }
```

`not r.is_error` is not defensive tidiness — it closes a real hole. If
errored calls counted, then "make the backtest fail" would become a way
through the evidence gate, inverting the guard's entire purpose. A tool
that errored has produced no evidence, so it unlocks nothing. There is a
mutation-verified test for this.

```python
def window_evidence_complete(state, window_index) -> bool:
    done = _successful_tools_in_window(state, window_index)
    return "run_backtest" in done and "test_significance" in done
```

This is the bar for **leaving** a window by either exit. It requires the
mandatory control — `test_significance`, the "did it beat randomized
entries at the same trade frequency" comparison that
`docs/architecture.md` calls mandatory — to have actually *run and
succeeded*, not merely to have been available.

This is the second half of the mandatory-control guarantee. Component 5
made *skipping* the control unrepresentable by giving `StudyDesign` no
`control_required` field. Component 6a makes it *unreachable* by giving the
loop no exit from a window that has not run it. Neither half alone is
sufficient: the first stops it being configured away, the second stops it
being simply never called.

```python
def available_tools(state) -> tuple[str, ...]:
    done = _successful_tools_in_window(state, state["window_index"])
    if done & set(EVIDENCE_TOOLS):
        return EVIDENCE_TOOLS + DIAGNOSTIC_TOOLS
    return EVIDENCE_TOOLS
```

Note the deliberate asymmetry with `window_evidence_complete`: unlocking
diagnostics needs **any** evidence tool to have succeeded, while *leaving*
the window needs `run_backtest` **and** `test_significance` specifically.
The two thresholds answer different questions. Diagnosis is about having a
real number to explain. Leaving is about having run the full pre-registered
comparison.

Evidence tools are returned unconditionally, and that is what guarantees
the loop can never deadlock — see the dedicated test in section 5.

```python
def can_advance(state) -> bool:
    if state["window_index"] >= len(state["windows"]) - 1:
        return False
    return window_evidence_complete(state, state["window_index"])


def can_conclude(state) -> bool:
    return (
        state["window_index"] == len(state["windows"]) - 1
        and window_evidence_complete(state, state["window_index"])
    )
```

`can_advance` returns False on the last window because there is nothing to
advance to. `can_conclude`'s **second condition is the subtle one**, and it
was a genuine gap in an earlier draft of this design: without it, an agent
could advance into the final fold and conclude immediately, leaving that
fold reachable but never actually tested. The consistent rule that closes
it is that *every window is left the same way* — by advancing or by
concluding, and both require that window's own evidence to be complete.

### `build_decision_model` — the mechanism

This function is where the three guarantees actually become true. It builds
the Pydantic model that `llm_client.structured_output` will hand to Claude
as a tool-use input schema, **and it is rebuilt from scratch every single
step**.

```python
tools = available_tools(state)
tickers = tuple(state["charter"].resolved_universe)
indicators = rule_indicator_names(state["hypothesis"].parsed.rule)

if not indicators:
    tools = tuple(t for t in tools if t not in INDICATOR_TOOLS)
```

A `StrategyRule` built only from price and constant terms references no
indicators at all. `typing.Literal[()]` is not constructible, so rather
than emit a broken schema, `compute_indicator` is dropped from the
vocabulary for such a rule — there would be nothing legal to ask it for
anyway. This is a real edge case, not a hypothetical: the schema in
[backtester/schema.py](../../../src/backtester/schema.py) genuinely permits
such rules.

```python
call_tool_fields: dict[str, Any] = {
    "action":    (Literal["call_tool"], ...),
    "tool":      (Literal[tools], ...),
    "ticker":    (Literal[tickers], ...),
    "reasoning": (str, Field(description="Why this call, given the results so far.")),
}
if indicators:
    call_tool_fields["indicator"] = (
        Literal[indicators] | None,
        Field(default=None, description="..."),
    )

CallTool = create_model("CallTool", __config__=ConfigDict(extra="forbid"), **call_tool_fields)
```

Three things here deserve individual attention.

**First — what is absent.** There is no `start`, no `end`, no `rule`, no
`commission`, `cash`, `n_resamples`, or `seed`. Every one of those is
supplied by `execute_tool` from state. The date window in particular *is*
the lookahead guarantee: there is no field for a date, so no date can be
chosen. This is not "the LLM is told not to pick dates" and not "a
validator rejects bad dates" — the concept of an LLM-chosen date does not
exist in this system.

**Second — `create_model` rather than a class statement.** Two of the field
*types* (`tool`, `ticker`) and the *presence* of a third (`indicator`) are
only known at runtime, which a normal `class` statement cannot express. The
first implementation of this declared `CallTool` as a class and then
mutated `CallTool.model_fields` and `CallTool.__annotations__` afterwards.
That was verified to work on Pydantic 2.13 — but `model_fields` mutation is
not a public Pydantic contract, and a minor-version upgrade could silently
change its behavior. Given that this schema is the single mechanism
enforcing all three guarantees, resting it on an unsupported API was the
wrong trade. `create_model` is Pydantic's documented API for exactly this
situation.

**Third — `extra="forbid"`.** Without it, a model that invented a `start`
field would have that field *silently dropped* by Pydantic, and the
resulting action object would look perfectly clean while the model's
actual intent — peek at a different date range — went entirely
unrecorded. Forbidding extras converts that silent drop into a loud
`ValidationError`. The distinction matters for the same reason a caught
exception beats a swallowed one: an attempted violation you can see is
infinitely more useful than one you cannot.

```python
variants: list[type[BaseModel]] = [CallTool]
if can_advance(state):
    variants.append(AdvancePhase)
if can_conclude(state):
    variants.append(Conclude)

if len(variants) == 1:
    decision_type: Any = CallTool
else:
    decision_type = Annotated[Union[tuple(variants)], Field(discriminator="action")]

class AgentDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    decision: decision_type
```

`AdvancePhase` and `Conclude` are added to the union **only when their
guard passes**. When a guard fails, that action is not rejected — it has no
representation in the schema at all, so there is no JSON the model could
emit that means "conclude now."

The `discriminator="action"` makes the union unambiguous: Pydantic (and the
tool-use schema Claude receives) resolves which variant applies by reading
the `action` literal, rather than trying each variant in turn. The
`len(variants) == 1` special case exists because `Union` of a single type
is degenerate and a discriminated union needs at least two members.

`AgentDecision` is a thin wrapper because `llm_client.structured_output`
needs a single `BaseModel` to call `model_json_schema()` on — a bare
`Union` is not a model.

### `loop_graph.py` — `_tool_arguments`, the lookahead boundary

```python
def _tool_arguments(state: LoopState, action: BaseModel) -> dict[str, Any]:
    window = state["windows"][state["window_index"]]
    args: dict[str, Any] = {
        "ticker": action.ticker,
        "start": window.start.isoformat(),
        "end": window.end.isoformat(),
    }
    if action.tool in RULE_TOOLS:
        args["rule"] = state["hypothesis"].parsed.rule.model_dump(mode="json")
    if action.tool in INDICATOR_TOOLS:
        args["name"] = action.indicator
    return args
```

This is the one function in the graph where the lookahead guarantee becomes
concrete. `start` and `end` come from `state["windows"][window_index]` and
from nowhere else. Critically, this is **not** "ignoring an LLM-supplied
date" — the `action` object physically has no date attribute to read, so
there is no such value in existence to ignore. The same holds for `rule`:
frozen at pre-registration in Component 4, injected here, never chosen.

### The nodes

`initialize` creates the `StudyRun` row with `status='running'` and flips
the hypothesis to `'testing'`. It deliberately does not set any outcome —
Component 7 owns every later status transition.

`decide_next_action` is the **only** node that calls an LLM. It builds the
response model from the current state, calls the injected `llm`, and
stores the result in `pending_action` while incrementing `step_count`.

`execute_tool` is async (MCP's client is async), builds the arguments,
awaits the tool call, and writes both a `ToolResult` into state and a
`ToolCallTrace` row into Postgres. The database write happens
**synchronously, before the next decision is made** — those rows, not
LangGraph's in-memory state, are the durable record Component 7 will
validate claims against.

`advance_phase` increments `window_index`. It is deterministic and gets its
own node rather than being a flag inside `execute_tool`, so that the phase
transition is *visible in the graph's own execution path* and the guard is
evaluated on the edge into it. A flag buried in another node's return value
would work identically but would be invisible when reading the graph.

`finalize` (used twice, via `make_finalize("completed")` and
`make_finalize("failed")`) writes the terminal status, `step_count`, and
`finished_at`.

### `route_after_decide` — where the budget lives

```python
def route_after_decide(state: LoopState) -> str:
    if state["step_count"] > MAX_STEPS:
        return "budget_exhausted"
    action = state["pending_action"]
    return {"call_tool": "execute", "advance_phase": "advance", "conclude": "conclude"}[action.action]
```

The budget is checked **in code, on the edge** — never by asking the LLM to
stop, which would make cost control depend on the goodwill of the thing
being controlled. Exhaustion routes to `budget_exhausted`, which finalizes
with `status='failed'`, and **never to `conclude`**. That routing is a
correctness decision, not a stylistic one: a study that ran out of budget
mid-walk-forward has untested windows, and a confident verdict drawn from
partial folds is precisely the dishonest output this project exists to
prevent.

`MAX_STEPS = 40`, counted on `decide_next_action` rather than on
`execute_tool`, because the LLM call is what costs money — a step that ends
in `advance_phase` or in a validation failure still spent one. A six-window
walk-forward at roughly four calls per window is about 24 steps, so 40
leaves real headroom for diagnostics without letting a stuck loop run
indefinitely.

### `build_graph` — and why there is no checkpointer

The graph wires `START → initialize → decide`, then conditional edges out
of `decide` to `execute` / `advance` / `conclude` / `budget_exhausted`,
with `execute` and `advance` both looping back to `decide`, and both
terminal nodes going to `END`.

Both the MCP session and the LLM are **injected** into `build_graph` rather
than imported at module level. That is what makes 6a testable with no
network, no subprocess, and no Bedrock spend — and it is the reason the
6a/6b split is possible at all.

No checkpointer is configured, deliberately. LangGraph offers persistence
that can resume a graph mid-execution after a crash. It is not used because
`study_runs` and `tool_call_traces` are written synchronously as the loop
runs, so the durable record already exists in Postgres. Adding LangGraph's
own checkpointer would create a **second, overlapping source of truth for
the same state**, able to disagree with the first after a partial failure.

The cost of that choice, stated plainly: **a crashed run is not resumable
mid-step.** It is re-run from scratch or left `failed`. That is acceptable
for Stage 5, whose gate is about honesty and willingness to kill
hypotheses rather than durability under crash. Stage 8's scheduled
overnight runs are where crash-resume becomes a real requirement, and a
checkpointer can be added there without restructuring this graph.

### The `window_index` migration

`ToolCallTrace` gained one column. Alembic's autogenerate produced:

```python
op.add_column('tool_call_traces', sa.Column('window_index', sa.Integer(), nullable=False))
```

This was **edited before being applied**, to add `server_default='0'`. The
autogenerated version works only because `tool_call_traces` happens to be
empty right now — Component 6 is the first code that ever writes to it.
Against any populated database that statement fails outright, because
Postgres cannot backfill a `NOT NULL` column with no default. Keeping the
default means the migration is correct regardless of when it runs, which
matters because migrations are replayed on fresh databases (including the
test database) long after the circumstances that made the original safe.

### `rule_indicator_names`

A small public helper added to
[rule_strategy.py](../../../src/backtester/strategies/rule_strategy.py),
reusing the private `_collect_indicator_terms` and public `unique_terms`
that Stage 3 already built. It returns a **tuple**, not a list, because it
feeds a `typing.Literal`, which requires hashable arguments. The
alternative — reaching into `_collect_indicator_terms` from
`agentic_core` — would have coupled Stage 5 to a private function in
another package, so the helper is exported properly instead.

### What was skipped

Genuine boilerplate only: imports, the `ToolSession` `Protocol` (which
simply names the one method the loop calls, so a test fake satisfies it
structurally without importing anything from the MCP SDK), and
`initial_state`, which is a plain constructor with no logic.

---

## 3. Design decisions and rejected alternatives

### Guarantees enforced by schema omission, not by validation

**Chosen:** every forbidden action is made *inexpressible* — absent from
the `Literal` enum, or absent from the union — rather than being emitted by
the model and then rejected.

**Alternative considered:** give the LLM the full six-tool vocabulary plus
`advance_phase` and `conclude` at all times, and have `execute_tool` (or a
validation layer before it) reject any action that violates a rule,
returning an error message the model can react to.

**Why rejected:** the two are not equally strong, and the gap between them
is exactly the gap Stage 2's sacred gate exists to close in the
backtester. A validation layer proves *the model's illegal action did not
take effect on this run*. Schema omission proves *the illegal action was
never representable*. The first depends on the completeness of the
validator — every rule needs its own check, a missed case is a silent hole,
and the checks live in a different place from the schema so the two can
drift. The second cannot have a missed case, because the thing being
prevented has no encoding.

There is also a subtler benefit. Under the validation approach, a model
that repeatedly attempts a forbidden action would burn steps producing
rejected outputs, and the loop would need retry-with-feedback logic to make
progress. Under schema omission the model simply never sees the option, so
no step is wasted and no retry machinery is needed.

**Cost to reverse:** high, and deliberately so. This is the load-bearing
decision of the entire component; the tests in section 5 are written to
make quietly abandoning it impossible.

### `create_model` over post-hoc `model_fields` mutation

**Chosen:** build `CallTool` with `pydantic.create_model`.

**Alternative considered (and actually implemented first):** declare
`CallTool` as a normal class, then mutate `CallTool.model_fields` and
`CallTool.__annotations__` to inject the runtime-dependent `indicator`
field, and call `model_rebuild(force=True)`.

**Why rejected:** it was empirically verified to work on the installed
Pydantic 2.13 — the schema came out correct. But `model_fields` mutation is
not part of Pydantic's public contract, and a minor-version upgrade could
change its behavior with no deprecation warning. The failure mode is what
makes this unacceptable: if that mutation silently stopped taking effect,
the `indicator` field would vanish from the schema and `compute_indicator`
calls would start failing — but more dangerously, if a *different* internal
change caused `extra="forbid"` or a `Literal` constraint to not apply, the
guarantees would weaken silently with every test still passing. A mechanism
carrying this much weight should not rest on an unsupported API.

**Cost to reverse:** trivial in code, but there is no reason to.

### Budget counted on decisions, not on tool calls

**Chosen:** `step_count` increments in `decide_next_action`.

**Alternative considered:** increment in `execute_tool`, so the budget
counts actual tool executions.

**Why rejected:** the LLM call is what costs money and what can run away.
Counting executions would let a loop that repeatedly chooses
`advance_phase`, or repeatedly produces actions that fail validation, spin
indefinitely while the counter stayed still — the exact runaway the budget
exists to stop. Counting decisions caps LLM spend directly, which is what
`docs/architecture.md` §8 actually cares about.

**Cost to reverse:** trivial; one line. Not load-bearing.

### Budget exhaustion produces `failed`, never a verdict

**Chosen:** `route_after_decide` sends an over-budget run to a node that
finalizes with `status='failed'`.

**Alternative considered:** route to `conclude`, letting the agent
synthesize a verdict from whatever evidence it managed to gather, perhaps
with a caveat noting the truncation.

**Why rejected:** a study that exhausted its budget mid-walk-forward has
windows that were never tested. A verdict drawn from folds 1 and 2 of a
five-fold decay study is not a weaker verdict — it is a *different claim
than the one that was pre-registered*, dressed as the original. Allowing it
would mean the honesty of the output depended on a caveat being written
correctly, which is precisely the "hope the prompt holds" pattern this
project rejects everywhere else. `StudyRun.status='failed'` already exists
for exactly this, and failing loudly is the honest outcome.

**Cost to reverse:** trivial in code, but reversing it would reintroduce a
real dishonesty vector. Load-bearing.

### No LangGraph checkpointer

Covered in section 2. Summarizing the trade for completeness: chosen to
avoid two disagreeing sources of truth for the same state; rejected the
alternative (LangGraph's `PostgresSaver`) partly because it would need a
new dependency the project does not have — the installed
`langgraph-checkpoint` 4.2.0 has no Postgres saver, and the project uses
`psycopg2-binary` while the Postgres saver expects psycopg3 — but mainly on
the two-sources-of-truth argument, which would hold even if the dependency
were free. **Reversible**, and expected to be revisited at Stage 8.

### The loop's tool vocabulary is narrower than the MCP surface

Covered in section 2. This is worth restating as a decision because it is a
**disclosed deviation from `docs/architecture.md`**, which lists six tools
including the screener. It was raised explicitly rather than made
quietly, per the project's working agreement, and confirmed before
implementation. **Reversible**, but each of the three exclusions closes a
specific hole (LLM-typed statistics, mid-study universe changes, and dead
vocabulary), so reversing any of them should be a deliberate act with an
argument attached.

### Testing against a hostile fake rather than a mocked-happy-path fake

**Chosen:** the graph-level tests are driven by `LazyAgent`, which always
takes the earliest exit available to it — conclude if allowed, else
advance if allowed, else do the bare minimum work that unlocks an exit.

**Alternative considered:** a scripted fake that plays a sensible,
cooperative sequence of actions and asserts the loop produces the expected
trace.

**Why rejected:** a cooperative fake tests that the machinery *works*,
which is worth something but is not what this component's claims are about.
The claims are all of the form "the agent cannot do X." An agent that never
attempts X provides no evidence about them. `LazyAgent` models the actual
failure mode `.claude/rules/agent-honesty.md` names — an agreeable model
looking for the shortest path to "done" — and the useful result is that it
*still* runs the control in every window, not because it chose well but
because the alternatives were never in its schema.

**Cost to reverse:** n/a — this is additive test design, and the
cooperative case is covered implicitly (the lazy agent completes a normal
two-window study successfully).

---

## 4. Concepts introduced

**A state machine with a reducer (LangGraph's core idea).** LangGraph runs
a graph whose nodes are functions and whose shared memory is one typed
state object. A node does not mutate that object; it *returns a partial
update*, and LangGraph merges it. How the merge happens is controlled per
field by a **reducer**. With no reducer, a returned value replaces the old
one. With `operator.add` on a list field, returned items are appended. This
matters here because it converts "evidence should be append-only" from a
convention someone must remember into a property of the type: a node that
wanted to delete a prior result has no return value that would accomplish
it. What goes wrong without this: a node that recomputed and re-returned
the whole results list could quietly drop an inconvenient failed backtest,
and nothing in the type system would notice.

**Structural impossibility versus validation.** These are two genuinely
different security postures, and confusing them is how guarantees rot. A
*validated* system accepts a request, evaluates it against rules, and
rejects it if it violates them — the guarantee is only as good as the
completeness of the rule set, and rules live separately from the thing they
constrain so the two can drift apart. A *structurally impossible* action
has no representation: no field to put it in, no enum member to name it. In
this component, "the agent must not choose its own dates" is enforced by
`CallTool` having no date field, so the guarantee cannot be weakened by
forgetting a check — only by adding a field, which is a visible, deliberate
act that the test suite specifically watches for. Component 5 established
the same pattern by giving `StudyDesign` no `control_required` field.

**Mutation testing.** A passing test proves the code behaves correctly on
the tested input; it does *not* prove the test would notice if the code
became wrong. Mutation testing closes that gap: deliberately break the
implementation, re-run the suite, and confirm the specific tests that
should fail actually do. A test that still passes against a broken
implementation is not protecting anything — it is decoration. This is the
technique used in section 5, and it is why the verification here is
stronger than a green test run. The concrete failure it catches: a test
that asserts `available_tools(state)` returns a non-empty tuple would pass
whether or not gating worked at all.

**Discriminated unions.** When a field can hold one of several shapes,
Pydantic can either try each in turn (slow, and error messages become
useless because it reports every failure) or read a designated *tag* field
to know immediately which shape applies. `Field(discriminator="action")`
does the latter, keyed on the `action` literal that every variant carries.
Beyond speed and error quality, it is what lets the union be *built from a
variable list of members* while still producing a clean JSON schema for
Claude's tool-use API — which is the whole mechanism for guarantee 3.

**Walk-forward folds and why every one must be tested.** Explained fully in
[step-06](step-06-study-design.md) §4. The relevant consequence here: a
five-fold decay study whose later folds were skipped does not produce a
weaker version of the intended finding, it produces a *different* finding
(early-period performance) wearing the label of the intended one. That is
why `can_conclude` refuses while unvisited windows remain, and why budget
exhaustion is a failure rather than an early conclusion.

---

## 5. How this component was verified

41 new tests, all passing; full suite **276 passing** (up from 235). But the
count is not the verification — the mutation testing is.

### Structural proofs (`test_loop_state.py`, 31 tests)

These assert against the **JSON schema the model actually receives**, not
against observed behavior. A schema-level assertion is categorically
stronger here: behavior proves the model did not do X on this run; the
schema proves X was not expressible.

- Seven parametrized cases confirm `start`, `end`, `rule`, `commission`,
  `cash`, `seed`, and `n_resamples` are all absent from `CallTool`'s
  properties.
- A model that tries to smuggle in `start` fails validation (`extra="forbid"`
  working as intended, rather than silently dropping the field).
- `ticker` outside the charter universe, and `indicator` outside the rule's
  own indicators, both rejected.
- Diagnostic tools absent from the enum before evidence, present after.
- An **errored** evidence result does not unlock diagnostics.
- Evidence in a *previous* window does not unlock the next one.
- `can_advance` false without evidence, false with a backtest but no
  control, true with both, false on the last window.
- `can_conclude` false while unvisited windows remain, false on the last
  window without its own evidence, true only when both hold.
- A hostile payload attempting each forbidden action raises
  `ValidationError` — the action has no schema representation.
- A parametrized deadlock test confirms a legal action always exists in
  every reachable state.

### Behavioral proofs (`test_loop_graph.py`, 10 tests)

Driven by `LazyAgent` against a `FakeSession` that records every
`(name, arguments)` pair. Asserting on what the fake session *received* is
the strongest available form of the lookahead check — it is the exact
payload a real MCP tool would have been handed.

- Every tool call carries the current window's dates; the set of
  date-pairs seen equals exactly the set of design windows.
- Calls appear in non-decreasing window order — never reaching forward.
- The frozen rule is injected on every rule-taking call.
- **The headline test:** the lazy agent still runs `run_backtest` *and*
  `test_significance` in every window, including the last.
- It visits every window (4 advances across a 5-window design).
- Completed runs are recorded; the hypothesis is left `testing` for
  Component 7.
- Every call is traced with the correct `window_index` and matching dates.
- Budget exhaustion yields `status='failed'`, with `step_count > MAX_STEPS`.
- A permanently failing control can **never** reach `completed`.

### Mutation testing — what actually makes the above meaningful

Each guarantee was deliberately broken and the suite re-run:

| Mutation | Tests that caught it |
|---|---|
| Diagnostics always unlocked | 4 |
| `can_conclude` drops the final-window evidence requirement | 1 |
| `window_evidence_complete` stops requiring the control | 2 |
| Errored results count as successes | 1 |
| `execute_tool` uses the final window's dates (deliberate lookahead) | 3 |
| Budget check removed entirely | 2 |
| Budget exhaustion routes to `conclude` instead of failing | 2 |

Every mutation was caught by the specific tests that should catch it, and
both mutated files were afterwards verified **byte-identical** to their
pre-mutation backups via `diff`.

### What this does NOT prove

State this plainly, because it matters:

- **No live LLM has driven this loop.** Every behavioral test uses
  `LazyAgent`. That is 6a's design, not an oversight — but it means the
  real prompt has not been shown to produce sensible tool sequences, only
  that *no* sequence can violate the guarantees. 6b closes this.
- **One adversarial personality, not all of them.** `LazyAgent` models the
  agreeable-shortcut failure. A model that hallucinates malformed actions,
  or that loops on a single tool forever, is covered only at the schema
  level and by the budget respectively.
- **This is not Sacred Gate 2.** Gate 2 requires proving the agent never
  fabricates *and* that it kills hypotheses when evidence says to. 6a
  proves neither. It proves the loop cannot skip the evidence that a
  kill decision would rest on — a precondition for Gate 2, not the gate.
  Fabrication is Component 7's validator; willingness to kill is
  Component 7's mechanical falsification check.
- **The database write path is exercised only against the test database.**
  Confirmed after the run: the dev database's real hypothesis is still
  `proposed`, with `study_runs` and `tool_call_traces` both at zero.

### One claim retracted during verification

While checking budget behavior, this component's author raised a concern
that LangGraph's default `recursion_limit` (believed to be 25) would trip
long before `MAX_STEPS = 40` was reached, since each step visits two nodes.
That concern was **wrong**. Verified empirically by running a full
budget-exhausting study with no explicit limit: 82 node visits completed
cleanly. The installed LangGraph 1.2.11 defaults to **10007**
(`langgraph/_internal/_config.py:32`); 25 is an older version's default. No
fix was needed and none was made.

The explicit `recursion_limit: 200` that remains in the test helper is kept
for a different and real reason, now documented at the call site: it is a
*test* backstop, so a regression that breaks the budget check fails the
suite in seconds rather than grinding through ten thousand supersteps. The
budget-removal mutation above is exactly that scenario, and it failed fast
because of it.

---

## 6. Interview defense

**"Walk me through how you stop the agent from peeking at out-of-sample
data."** The action schema the model receives has no date field. Not a
validated field, not a field that gets overwritten — no field. The date
window is supplied by `execute_tool` from the study design's
pre-registered windows, indexed by the loop's current position. So there is
no value the model could emit that would reach a tool as a date. The test
that protects this asserts against the generated JSON schema itself, and it
is mutation-verified: I changed `execute_tool` to use the final window's
dates and three tests failed immediately.

**"Why didn't you just validate the model's chosen dates against the
allowed window instead?"** Because validation and structural impossibility
are different strength guarantees, and the difference is the whole point.
Validation proves the illegal action did not take effect *on this run*, and
its completeness depends on me having written a check for every rule — a
missed case is a silent hole, and the checks live in a different file from
the schema so they can drift apart. Omission cannot have a missed case,
because the thing being prevented has no encoding. There is a practical
benefit too: under validation, a model that keeps attempting a forbidden
action burns steps on rejected outputs and needs retry-with-feedback
machinery; under omission it never sees the option.

**Hard question: "Your headline evidence is that a fake agent you wrote
couldn't cheat. Isn't that circular — you designed both the guard and the
adversary, so of course they agree?"** It is a fair challenge and the
honest answer has three parts. First, the strongest tests are not
behavioral at all — they assert against the generated JSON schema, which is
the actual artifact sent to Claude. Those hold regardless of what any agent
does, fake or real, because they are statements about what is
*representable*. Second, the fake agent's power comes from what it is
allowed to see: it reads only the schema it is offered, exactly as a real
model does, and it has no access to loop internals. Third, and most
importantly, the mutation testing is what makes this non-circular. I broke
each guarantee deliberately and confirmed the tests fail — so the tests are
demonstrably sensitive to the property they claim to protect, rather than
passing for incidental reasons. The residual weakness I would name
unprompted: `LazyAgent` models one failure personality, the agreeable
shortcut-taker. It does not model a model that hallucinates malformed
actions or fixates on one tool, and those are covered only by the schema
layer and the budget respectively.

**"You skipped LangGraph's persistence. Isn't that the main reason to use
LangGraph?"** It is one reason, not the main one — I am using its typed
state with reducers, its conditional edges, and its node structure, and it
is the orchestrator the architecture document names. I deferred the
checkpointer specifically because this loop already writes `study_runs` and
`tool_call_traces` synchronously, so a second persistence layer would be a
second source of truth for the same state, free to disagree with the first
after a partial failure. The honest cost is that a crashed run is not
resumable mid-step — it re-runs or stays `failed`. That is acceptable for
a stage whose gate is about honesty rather than durability, and Stage 8's
scheduled overnight runs are where I would add it.

**Honest weaknesses, stated plainly.** No live model has driven this loop
yet — 6a proves no sequence *can* violate the guarantees, not that the real
prompt produces sensible sequences. `MAX_STEPS = 40` is a reasoned estimate
(six windows at roughly four calls each, plus headroom), not a figure
calibrated against observed real runs; 6b is where it gets its first real
data. And the loop's tool vocabulary is narrower than the architecture
document specifies — a deliberate, argued deviation, raised before
implementation rather than made quietly, but a deviation nonetheless.

---

## 7. What comes next and why

**Component 6b** injects the real MCP `ClientSession` (via `stdio_client`,
the same subprocess-and-stdio path Stage 4's gate script proved in
[step-09](../stage-4/step-09-manual-mcp-verification.md)) and the real
`llm_client.structured_output`, then performs a live run against the real
AAPL `simple_holdout` design already sitting in `study_designs`. Because
both are injected parameters, 6b changes what is passed to `build_graph`
and nothing about the graph itself.

**Component 7** is the verdict: it reads the `tool_call_traces` rows this
loop wrote, applies the pre-registered falsification condition
mechanically, validates that every quantitative claim resolves to a real
trace, applies the multiple-comparisons correction, and sets the
hypothesis's final status. That is where Sacred Gate 2 is actually
satisfied.

**If 6a were wrong, here is how it would surface.** A broken window
injection would not fail loudly — it would produce backtests over the wrong
dates that still return perfectly plausible numbers, and Component 7 would
validate those numbers correctly against traces that are themselves wrong.
The verdict would be internally consistent and externally false. That is
precisely why the lookahead property is asserted against the arguments the
tool layer actually received, rather than trusted to code review. A broken
evidence gate would surface differently and more subtly: verdicts that
never cite a control, or walk-forward studies whose later folds are
missing from the trace — visible in the data, but only to someone who
thought to look. Both are the kind of failure that is far cheaper to
prevent structurally than to detect afterwards.
