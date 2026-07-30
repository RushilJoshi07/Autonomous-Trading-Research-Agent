# Step 5 — BacktestResult Provenance Fields (Stage 3)

## 1. What this does

Component 5 (`rule_strategy.py`) already computed which indicators a compiled
`RuleStrategy` depends on, split by tier, and exposed that as
`indicators_used`/`extended_indicators_used` class attributes. Nothing
consumed it. A study's actual `BacktestResult` — the thing `run_backtest`
returns, the thing a verdict or the frontend would eventually read — had no
field to carry that information at all. This component closes exactly that
gap: two new fields on `BacktestResult`, and a small change to `engine.py` so
`run_backtest` reads the already-computed provenance off `strategy_cls` and
passes it through.

This is a small component — two files, a handful of lines — but it's the
piece that makes Component 5's provenance data reach anywhere outside
`RuleStrategy` itself. Without it, the data existed and was correct, but
nothing downstream could ever see it.

---

## 2. Every meaningful line explained

### `result.py`

```python
indicators_used: list[str] = Field(default_factory=list)
extended_indicators_used: list[str] = Field(default_factory=list)
```

`Field(default_factory=list)` rather than a bare `indicators_used: list[str]
= []`. Worth being precise about why, since Pydantic v2 actually handles a
bare list default safely on its own — unlike a plain dataclass or an ordinary
class attribute, Pydantic validates and effectively copies field defaults
per-instance, so the classic "one shared mutable object across every
instance" bug that `default_factory` exists to prevent doesn't actually apply
here the way it would in vanilla Python. The explicit factory is used anyway,
for one reason: every other list/dict default already in this codebase
(`IndicatorSpec.params` in `indicators.py`, `IndicatorTerm.params` in
`schema.py`) uses `Field(default_factory=...)`, and a reader scanning this
file for "is this one safe" shouldn't need to know that Pydantic v2 happens to
make the bare form safe too. Visual consistency here removes a question a
future reader would otherwise have to go verify.

```python
@classmethod
def from_stats(
    cls,
    stats: "pd.Series",
    ticker: str,
    commission: float,
    indicators_used: list[str],
    extended_indicators_used: list[str],
) -> "BacktestResult":
```

The two new parameters are **required** — no `= []` default. `from_stats` has
exactly one caller anywhere in the codebase: `engine.py`'s `run_backtest`.
Giving it a default here would be the textbook Python mutable-default-argument
trap — a single list object created once, at function-definition time, and
implicitly shared by every call that doesn't override it. It happens to be
harmless in this exact case, since the method body only ever reads the
parameter and passes it straight into `cls(...)`, never mutates it in place.
But the discipline chosen was to not write the trap and then justify why this
particular instance is safe — it's simpler and more honest to just not have a
default on a method with one caller that always has a real (possibly empty)
value ready to pass.

### `engine.py`

```python
indicators_used=getattr(strategy_cls, "indicators_used", []),
extended_indicators_used=getattr(strategy_cls, "extended_indicators_used", []),
```

This is the one real decision in this component, covered in full in section 3.
In short: `run_backtest` is called with both `RuleStrategy` (which always has
real provenance) and Stage 2's hardcoded `SMACrossover` (which has no concept
of it at all). `getattr` with a fallback lets both coexist without either side
needing to know about the other.

---

## 3. Design decisions and rejected alternatives

### `getattr` with a fallback, instead of requiring every `Strategy` subclass to define these attributes

The alternative: make `indicators_used`/`extended_indicators_used` a formal
requirement — either by modifying `backtesting.py`'s own `Strategy` base
class to declare them (not this project's code to change), or by introducing
a project-specific base class that every strategy, hardcoded or rule-compiled,
would have to subclass instead of `Strategy` directly. Both were rejected for
the same underlying reason: `SMACrossover` predates the entire rule-schema
concept and has no natural notion of "which indicators back this result" —
it's hand-written Python, not compiled from a `StrategyRule`, so the question
doesn't even make sense for it. Forcing it (and, by extension, all of Stage
2's sacred-gate tests built on it) to retroactively grow an attribute purely
so a Stage 3 concern could read it uniformly would be solving a problem that
doesn't exist by creating a real one — coupling code that has no reason to
know about each other. `getattr` with a default costs nothing today and
imposes no contract on code that doesn't need one.

### Passing provenance as new required arguments to `run_backtest`, instead of introspecting `strategy_cls`

The alternative: add `indicators_used`/`extended_indicators_used` as new
parameters directly on `run_backtest`'s own signature, required at every call
site. This was rejected because `run_backtest`'s signature is shared,
generic Stage 2 infrastructure — every existing caller, including every one
of Stage 2's sacred-gate tests, would need to be updated to pass a value they
have no natural answer for. It also pushes knowledge that is intrinsically a
property of the *strategy class itself* — which indicators does this specific
strategy depend on — out to every *call site* of `run_backtest` instead. That's
strictly worse on two counts: it's more error-prone (every caller now has to
remember to compute and pass the right value, or silently pass a wrong one),
and it violates locality — the answer to "what does this strategy use" should
be read off the strategy that actually knows the answer, not threaded in by
whoever happens to be calling `run_backtest` at the time.

### Why not an `isinstance(strategy_cls, RuleStrategy)` check instead of `getattr`

A third option, briefly worth naming and rejecting explicitly: `engine.py`
could check whether `strategy_cls` is a `RuleStrategy` and only read
provenance in that case. This was never seriously in the running, because it
would require `engine.py` — Stage 2 code, generic and rule-schema-agnostic —
to `import` from `strategies/rule_strategy.py`, a Stage 3 addition built
specifically to consume Stage 2's `run_backtest`. That's the dependency
pointing the wrong way: Stage 3 already depends on Stage 2, and Stage 2 has
no reason to know Stage 3 exists at all. `getattr` is purely structural —
duck-typed, no import, no coupling — and gets the same practical result
without inverting that direction.

---

## 4. Concepts introduced

### Provenance, in this project's specific sense

"Provenance" here isn't a generic debugging convenience — it's a direct,
structural instance of one of this project's non-negotiable design rules,
stated in `CLAUDE.md`: **every quantitative claim must reference the tool
output that produced it.** A `BacktestResult` that can't say which indicators
it depended on is a result that can't fully back its own numbers. It also
connects directly to `docs/architecture.md`'s multiple-comparisons defense
(section 5): grounding functions as a prior, and an ungrounded or
extended-tier indicator is treated as closer to random search, facing a
*stricter* significance bar precisely because of that. `extended_indicators_
used` existing as a real, structured, disclosed field — even though it's
empty for every study that can currently be run, since no extended indicator
has ever been marked `verified=True` — is what will let a future verdict or
evaluation step answer "did this result depend on anything from the
less-trusted tier" by reading one field, mechanically, rather than by
re-deriving the answer from the rule by hand every time.

---

## 5. How the verification gate was satisfied

- A `RuleStrategy` compiled from `sma_10_30_crossover` and run through the
  real `run_backtest` correctly produced `indicators_used=['SMA']`,
  `extended_indicators_used=[]` — matching the rule exactly.
- `SMACrossover` (Stage 2's hardcoded strategy) run through the same
  `run_backtest` correctly defaulted to `indicators_used=[]`,
  `extended_indicators_used=[]`, with no exception — direct proof the
  `getattr` fallback protects backward compatibility in practice, not just in
  theory.
- The full 17-test regression suite stayed green throughout, specifically
  including Stage 2's `test_sacred_gate.py`, which exercises `SMACrossover`
  through this exact same `run_backtest`/`from_stats` path — direct evidence
  this change did not silently alter Sacred Gate 1's already-proven behavior.

**What this does not prove.** `extended_indicators_used` has never actually
been exercised with a non-empty value, because no extended indicator has ever
existed to produce one — Component 7/8 (extended indicator generation and
verification) hasn't been built yet, so a study using one is currently
impossible to construct, not merely untested. The plumbing is verified
end-to-end for the empty case; the case that actually matters most for the
multiple-comparisons defense — a real extended-tier indicator correctly
showing up in this field — remains unverified because the thing it would
verify doesn't exist yet.

---

## 6. Interview defense

**Q: Why not just pass `indicators_used`/`extended_indicators_used` as
required arguments to `run_backtest` directly, instead of introspecting
`strategy_cls`?**

A: Because that pushes a fact that's intrinsically about the strategy class
onto every caller of a shared, generic function instead. Every existing call
site — including every sacred-gate test from Stage 2 — would need to start
passing a value it has no natural answer for, and the correctness of that
value would depend on every caller remembering to compute it right, rather
than on reading it once, correctly, off the class that actually knows.

**Q: Why does `BacktestResult.from_stats` require these two parameters
instead of defaulting them to `[]` like the model fields themselves do?**

A: Because `from_stats` has exactly one caller in the whole codebase, and a
default there would be the classic Python mutable-default-argument trap — a
single list object built once and implicitly shared across calls. It's
harmless in this specific case since the method never mutates the parameter,
but the point was to not write the trap and then have to explain why this
particular instance is safe. A method with one caller and no natural default
value doesn't need one.

**Q (hard): `getattr(strategy_cls, "indicators_used", [])` silently returns
an empty list if that attribute is ever misspelled or missing on some future
kind of `Strategy` subclass that should have had real provenance. This whole
stage has been about refusing silent failure — the `bbands` dead parameter,
the dict-storage bug that produced `num_trades=0` with no exception. Isn't a
silent empty-list fallback exactly the pattern this project has spent all of
Stage 3 fighting?**

A: The risk is real in the abstract, and worth taking seriously rather than
waving off — but its actual severity today is narrower than the pattern
match suggests. There is currently exactly one producer of this attribute,
`make_rule_strategy`, so there's no fan-out of subclasses across which a typo
could hide. More importantly, the *kind* of wrongness is different from the
dict-storage bug. That bug silently produced an actively wrong trading
result — a real number, computed and reported, that was incorrect while
looking entirely plausible. A wrong or missing provenance value today would
silently produce an *incomplete disclosure*, not a false one — nothing in the
codebase currently reads or branches on these fields to make any decision, so
the failure mode is "a result under-reports what it depended on," not "a
result asserts something false as if it were true." Those aren't the same
severity, and treating them as identical would be its own kind of
imprecision. What I'd say plainly is when this stops being acceptable: the
moment a second producer of rule-compiled strategies exists, or the moment
Stage 5 or Stage 6's evaluation harness starts actually branching on
`extended_indicators_used` being non-empty — applying stricter scrutiny to a
study because of it, say — the silent fallback needs to become a loud failure
instead of a quiet default. This is a deliberately deferred hardening with a
named trigger condition, not a permanent blind spot, and the honest position
is to say exactly when it needs revisiting rather than defend it as
acceptable forever.

**Honest weakness:** the field that matters most for this project's stated
multiple-comparisons defense — `extended_indicators_used` actually being
non-empty and correctly disclosed — has literally never been observed,
because nothing in the codebase can produce that state yet. The plumbing is
real and tested for the case that exists today (empty, for every current
strategy); the case that will actually test whether the disclosure mechanism
works when it matters is still ahead.

---

## 7. What comes next and why

Plan-item 6 is the minimal `llm_client` abstraction
(`src/llm_client/__init__.py`) — the first LLM call anywhere in this project.
This is also where the Stage 3 plan's amended rule takes effect in actual code
for the first time: `CLAUDE.md` and `docs/architecture.md` both record that
"Stages 1–3 use no LLM" was deliberately amended to allow exactly one bounded
exception — an offline, build-time LLM call proposing parameter bounds for
the extended indicator tier (Component 7/8), never invoked at runtime, never
making a quantitative trading decision. `llm_client` is the thin module that
call will go through.

If this component's provenance fields were subtly wrong in a way nobody
caught — say, always empty even when a study genuinely depended on an
extended-tier indicator — the failure would not be a crash anywhere in this
codebase. It would surface much later, in Stage 5, as a verdict quietly
disclosing less than it should about how a result was actually produced.
That's precisely the kind of gap `CLAUDE.md`'s "every quantitative claim must
reference the tool output that produced it" rule exists to prevent, and
precisely why this small component — not the LLM client, not the extended
tier — is the piece that has to be right before either of those can be
trusted to disclose honestly what they depended on.
