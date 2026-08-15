# Step 3 — Backtester Tool (Stage 4)

## 1. What this does

`run_backtest(rule, ticker, start=None, end=None, commission=None, cash=None)
-> BacktestResult` is the second tool registered on the Stage 4 MCP server,
and the more consequential one: it makes the actual payoff of Stage 3 —
"any `StrategyRule` becomes a real, cost-aware, no-lookahead backtest,
without a single line of new Python" — reachable from outside a direct
Python call for the first time. It wraps three already-built, already-tested
functions in sequence: `load_price_data` (Stage 2), `make_rule_strategy`
(Stage 3), and `run_backtest` (Stage 2, the engine function this tool is
named after and calls internally under an alias).

What this component is *not*: it introduces no new backtesting logic, no
new validation logic, and no new indicator logic. Every number this tool can
ever return was already computable before this component existed — the only
thing that changed is that it's now reachable through MCP instead of only
through a direct Python import.

---

## 2. Every meaningful line explained

```python
from backtester.engine import run_backtest as _run_backtest
```

Aliased on import specifically because the tool function below needs the
name `run_backtest` for itself — that's the tool name the approved Stage 4
plan commits to, and it collides exactly with the name of the function this
tool calls. Without the alias, `def run_backtest(...)` defined later in the
same module would silently shadow this import the moment Python finished
executing the file, and any reference to the engine function from inside the
tool body would actually be calling the tool function itself — infinite
recursion, not a backtest. The alias's leading underscore is the same
"internal, not meant to be imported elsewhere" convention already used by
`engine.py`'s own `_DEFAULT_COMMISSION`/`_DEFAULT_CASH`.

```python
@mcp.tool()
def run_backtest(
    rule: StrategyRule,
    ticker: str,
    start: date | None = None,
    end: date | None = None,
    commission: float | None = None,
    cash: float | None = None,
) -> BacktestResult:
```

`rule: StrategyRule` is the one parameter type this component actually had
to prove out before trusting it — see section 3's first decision for what
was checked and why it mattered. `commission`/`cash` are typed `float |
None` rather than `float` with a literal default — the reasoning for that
specific choice, and the confirmation that it actually works the way
intended, is section 3's second decision.

```python
    with SessionFactory() as session:
        df = load_price_data(ticker, session, start=start, end=end)
```

Identical to Component 2's own session-per-call pattern — no new decision
here, same reasoning as `docs/explanations/stage-4/step-02-market-data-tool.md`
section 3 already covers for why a fresh session per call, not a long-lived
one.

```python
    strategy_cls = make_rule_strategy(rule)
```

The Stage 3 payoff line, unchanged. `make_rule_strategy` compiles the
validated `rule` into a real `backtesting.py` `Strategy` subclass — this
tool doesn't need to know anything about how that compilation works, only
that it produces something `run_backtest` (the engine function) already
knows how to run.

```python
    kwargs = {}
    if commission is not None:
        kwargs["commission"] = commission
    if cash is not None:
        kwargs["cash"] = cash
    return _run_backtest(df, strategy_cls, ticker=ticker, **kwargs)
```

Builds the keyword-argument dict conditionally rather than always passing
`commission=commission, cash=cash` directly. If either were always passed,
a caller who omitted them would send an explicit `None` straight into
`_run_backtest`'s `commission: float = _DEFAULT_COMMISSION` parameter — and
an explicit `None` argument overrides a Python default value; it does not
fall back to it. That would silently break `_DEFAULT_COMMISSION`/
`_DEFAULT_CASH` entirely the first time this tool was called without
specifying either. Building `kwargs` conditionally and only including a key
when a real value was given is what actually preserves "omit it, get
`engine.py`'s real default" — verified, not assumed; section 5 covers the
live check that confirmed it.

---

## 3. Design decisions and rejected alternatives

### Trusting `StrategyRule` as a direct MCP parameter type — proven, not assumed

Component 2 confirmed the MCP SDK coerces a raw JSON value into a typed
Python parameter for one specific, simple case: a JSON string into a scalar
`datetime.date`. Whether that same mechanism extends to `StrategyRule` — a
deeply nested Pydantic model with a self-referential recursive `Condition`
tree and a `Term` field that's a seven-way discriminated union
(`IndicatorTerm | PriceTerm | ConstantTerm | BodyTerm | MidpointTerm |
RangeTerm | ScaledTerm`, selected by a `kind` discriminator) — was an open
question flagged explicitly at the end of Component 2's own step
explainer, not something safe to assume just because the simpler case
worked.

The rejected alternative was building `StrategyRule` explicitly inside the
tool body — accepting `rule: dict` as the parameter type and calling
`StrategyRule(**rule)` (or `StrategyRule.model_validate(rule)`) by hand
before passing it to `make_rule_strategy`. This would have worked, but it
would have meant re-deriving, inside this tool, a piece of behavior the SDK
might already provide automatically — worth checking before writing it, not
after.

It was checked directly: a throwaway tool typed `rule: StrategyRule` was
registered and called through `mcp._handle_call_tool` with
`json.loads(SMA_CROSSOVER.model_dump_json())` — one of Stage 3's own four
`KNOWN_STRATEGIES`, serialized to plain JSON exactly the way a real MCP
client would send it. It coerced cleanly into a real, validated
`StrategyRule` instance, discriminated union and recursive tree included,
with zero custom construction code. That settled it: `rule: StrategyRule`
is the tool's actual parameter type, and the "build it by hand" alternative
was never written, because the check that would have justified writing it
came back negative.

**A second, more valuable finding came from testing the failure case at the
same time.** A deliberately malformed payload — an `IndicatorTerm`
referencing `"NOTAREALINDICATOR"`, three levels deep in the tree at
`rule.entry.comparison.left` — was rejected *before the tool function body
ever ran*, at the SDK's own argument-construction step. The resulting error
preserved the exact field path (`rule.entry.comparison.left.indicator`) and
`IndicatorTerm._check_indicator`'s own validator text (`"unknown indicator
'NOTAREALINDICATOR'"`) — the *entire* precision of Stage 3's existing
validation, reached with no code written in this component to make that
happen. Had the "build it by hand" alternative been chosen instead, this
same validation would still fire (it's `StrategyRule`'s own validator, not
anything this tool adds), but only after this tool's body had already
started running — a strictly worse place for a malformed rule to be caught,
and one more reason the tested-first approach was the right one, not just a
faster one.

**Reversibility:** this decision is load-bearing for how little code this
tool needed, but not load-bearing for correctness — if it ever needs
revisiting, `StrategyRule.model_validate(rule)` inside the tool body remains
available as a fallback, at the cost of the validation now catching problems
one step later.

### `commission`/`cash` default to `None`, verified to actually reach `engine.py`'s real defaults

The rejected alternative was copying `engine.py`'s literal default values —
`0.001` and `10_000` — directly into this tool's own signature. That would
have worked today, but it creates a second place those two numbers live: if
`_DEFAULT_COMMISSION` or `_DEFAULT_CASH` in `engine.py` were ever changed,
this tool's copies would keep the old values unless someone remembered to
update both places in lockstep — exactly the kind of quiet drift a
single-source-of-truth constant is supposed to prevent in the first place.

Typing them `float | None = None` and only including a key in `kwargs` when
the caller actually supplied a value was chosen instead, specifically so
"the caller didn't specify a value" and "the caller specified `engine.py`'s
current default value" stay indistinguishable from the engine's point of
view — there is exactly one place either default is ever spelled out as a
literal.

This wasn't left as an untested claim about how Python default arguments
behave. The real tool was called with `commission` and `cash` both omitted
entirely, and the returned `BacktestResult` came back with `commission_pct:
0.001` — `engine.py`'s actual current default, reached through the
conditional-`kwargs` path, not a coincidence and not a value hardcoded
anywhere in this tool.

### `trade_on_close` deliberately excluded from the tool's interface

`engine.py`'s `run_backtest` (the engine function, not this tool) has a
`trade_on_close: bool = False` parameter, and its own docstring is explicit
about when it's needed: "Required when a signal is computed from the same
bar's close (e.g. a pre-shifted lookahead column)." That's a description of
one specific Stage 2 testing scenario — deliberately constructing a
same-bar-close signal to prove the sacred-gate lookahead tests actually
catch it — not a property any strategy `make_rule_strategy` compiles from a
`StrategyRule` ever has. Every rule this tool can be asked to run already
respects the schema's own lookahead discipline (`validate_offset`,
`MAX_LOOKBACK`, enforced at rule-construction time in `schema.py`) with
`trade_on_close` at its default `False` — there's no legitimate reason for
a `StrategyRule`-driven backtest to ever need it set otherwise.

The rejected alternative was exposing it anyway, on general "more
flexibility can't hurt" grounds. It was rejected because that reasoning is
backwards here: a parameter with no legitimate use case from any real caller
of this specific tool is not flexibility, it's unexplained surface area —
something Stage 5's agent could set without understanding what it does or
why, with no corresponding benefit to justify the risk. Leaving it off
entirely means there's nothing here for a future agent to misuse.

---

## 4. Concepts introduced

**Argument-coercion depth, and why it needed re-testing rather than
extrapolating.** Component 2 established that this SDK's tool-argument
layer performs real Pydantic-style validation on a function's parameters,
not just a type-hint-shaped suggestion. What wasn't yet established, until
this component tested it directly, was *how much* of Pydantic's own
capability that layer actually exercises — specifically, whether it
recurses correctly into nested models and resolves a `Field(discriminator=
...)`-based union the same way calling `StrategyRule(**payload)` directly
would. Confirming this mattered because the entire value of "typing the
parameter as `StrategyRule` instead of `dict`" depends on it — if the SDK's
coercion had turned out to be shallower (correctly handling a flat model
but not a nested, discriminated one), the "build it by hand" alternative
from section 3 would have been the only correct choice, not merely the more
cautious one.

---

## 5. How this component was tested

**Offline / synthetic, before writing the real tool** — the same
escalating pattern Components 1 and 2 both used. A throwaway tool typed
`rule: StrategyRule` confirmed the nested-model coercion question (section
3). A deliberately malformed payload confirmed the failure path preserves
full validation precision, and confirmed it fails at the argument-
construction boundary rather than inside a tool body that might handle it
inconsistently.

**Live, against real data, after writing the real tool.** Called the actual
`run_backtest` tool through `_handle_call_tool` — the real protocol path,
not a shortcut — with `SMA_CROSSOVER` (one of Stage 3's own
`KNOWN_STRATEGIES`), ticker `AAPL`, 2015-01-01 through 2024-12-31, against
the real `strategy_research` database. This is the same strategy and
approximately the same date range Stage 3's own literature-consistency gate
used (`docs/explanations/stage-3/stage-3-summary.md` section 5) — not an
arbitrary smoke test, a real rerun of a result this project has already
validated once. The result came back as a genuine `BacktestResult`:
`sharpe_ratio 0.678`, `num_trades 44`, `annual_return_pct 15.04`,
`max_drawdown_pct -32.43`, `commission_pct 0.001`, `indicators_used
['SMA']`. Then called it again with the same malformed-indicator payload
used in the synthetic pre-check, now against the real tool, and confirmed
the identical `is_error=True` precision held.

Full existing 170-test suite run and confirmed unchanged both before this
component's code was written and after.

**What this does not prove.** No automated, committed test exists for this
tool yet, for the same reason Component 2 doesn't have one — Component 8
(the formal test suite) is scoped to come after all six tools exist. It also
doesn't prove anything about a rule with an `exit=None`/`exit_after_bars`-
only shape, a rule using an *extended*-tier indicator rather than a core
one, or a rule that produces zero trades — all real, valid `StrategyRule`
shapes this tool will eventually be asked to run, none of which this
component's specific verification happened to exercise.

---

## 6. Interview defense

**Q: Why didn't you just build `StrategyRule` from the raw argument dict
yourself inside the tool, instead of trusting the SDK to do it via the type
hint?**

A: I checked whether trusting it was justified before deciding either way,
rather than assuming the safer-looking manual approach was necessary. It
was checked with the actual `StrategyRule` class, a real known strategy's
serialized payload, and — more importantly — a deliberately broken payload,
which confirmed not just that valid input works but that invalid input
fails at the right layer, with the right precision. Building it by hand
would have added code that turned out to duplicate something already
working correctly, and would have moved validation one step later in the
call path for no benefit.

**Q: This tool doesn't validate anything about the *combination* of `rule`,
`ticker`, and date range — what if the rule references an extended
indicator that isn't actually verified, or the date range predates when
Stage 1 started ingesting that ticker?**

A: Both of those are still caught, just not by this tool — `StrategyRule`'s
own validator already rejects any indicator with `verified=False`
(`schema.py`'s `IndicatorTerm._check_indicator`, unconditionally, proven
again by this component's own malformed-payload test), and
`load_price_data` already raises a clear `ValueError` for a ticker/date
range with no cached rows (proven in Component 2's own verification). This
tool doesn't need to re-implement either check; it needs to not break the
path that already reaches them, which is exactly what letting both
exceptions propagate unhandled — rather than catching and reformatting them
— accomplishes.

**Q (hard): You verified this tool against exactly one known strategy
(`SMA_CROSSOVER`) and exactly one kind of malformed input (a bad indicator
name). Stage 3 built four `KNOWN_STRATEGIES`, including one — the morning
star pattern — that exercises schema features (`BodyTerm`, `MidpointTerm`,
`RangeTerm`, `ScaledTerm`) `SMA_CROSSOVER` never touches at all. Doesn't
that leave a real gap in what this component actually proved?**

A: Yes, and it's worth stating plainly rather than implying broader coverage
than what actually happened. This component's verification proves the
argument-coercion mechanism handles *the specific schema features
`SMA_CROSSOVER` uses* — a discriminated union resolving to `IndicatorTerm`
and `ConstantTerm`, inside a single-level `leaf` condition. It does not
directly prove the same mechanism handles `ScaledTerm`'s self-nesting
restriction, or a multi-child `and` condition tree, the way morning star
would exercise. The reason this gap is tolerable *for now* rather than a
real defect: the coercion mechanism being tested is the SDK's generic
Pydantic-based validation, not anything specific to which `StrategyRule`
happens to be sent through it — and Stage 3's own test suite already proved
`StrategyRule` itself parses and validates morning star correctly through
direct Python construction. The specific, narrower thing left unverified is
whether the SDK's coercion layer handles that same complexity identically
to direct construction, and that gap should close in Component 8's formal
test suite, not stay open indefinitely on the assumption that "it worked for
the simple case" generalizes.

**Honest weakness:** as with Component 2, there is no committed, automated
test for this tool — everything verified here was real but interactive.
And as the hard question above makes explicit, verification so far has only
exercised the simplest of Stage 3's four known strategies; the more
structurally complex ones remain an open, disclosed gap until Component 8.

---

## 7. What comes next and why

Component 4 (indicators tool) is a different shape of problem again: unlike
this component and Component 2, which both wrapped an existing function
completely unchanged, Component 4 needs one small genuinely new function —
nothing in the codebase today computes a single indicator standalone,
outside `make_rule_strategy`'s `self.I()` wiring into `backtesting.py`'s own
run loop. Whether that new function can cleanly reuse `IndicatorSpec.fn` and
`select_output_column` from `indicators.py`, the same two pieces this
component's own dependency (`make_rule_strategy`) already relies on, or
needs something structurally different, is the open question Component 4
has to resolve — this component's verification pattern (check the
mechanism directly, on real data, including the failure path, before
trusting it) is the template Component 4 will need to follow again, on new
ground this time rather than a mechanism already partially proven.

If this component's `StrategyRule`-coercion trust turns out wrong for a
schema shape not yet tested — the morning-star-style gap named above — the
most likely symptom is not a wrong number; `StrategyRule`'s own validators
would still run either way. It's more likely to surface as an outright
rejection of a valid, complex rule that direct Python construction would
have accepted — a false negative in the tool's argument layer, not a
silently wrong backtest result, consistent with this project's general
preference for loud failures over quiet wrong ones.
