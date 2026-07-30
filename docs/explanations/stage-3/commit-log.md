# Commit log — Stage 3

Lightweight notes after each commit — what changed, why, anything non-obvious.
Entries before this file existed (Stage 1, Stage 2, Stage 3 Component 1 & 2, the
post-commit hook tooling commit) live in the old flat `docs/explanations/commit-log.md`.

---

## docs: split commit-log.md into per-stage folders going forward

**Change:** Updated CLAUDE.md and the `explanation-writer` skill so Level-1 commit
notes append to `docs/explanations/stage-N/commit-log.md` instead of one flat
top-level file, matching how step/stage explainer files already organize per-stage.

What is non-obvious: this is a going-forward-only split — history through Stage 3
Component 2 stays in the old flat file untouched, by explicit choice, rather than
retroactively moved. Step explainer naming (`step-NN-name.md`) is unchanged; only
the commit-log path convention changed. This very commit's hook run confirmed the
automatic (non-manual) post-commit trigger genuinely fires on a real `git commit` —
it correctly skipped writing this entry itself because `CLAUDE_CODE_OAUTH_TOKEN`
wasn't set in that shell, which is why this entry is written interactively instead.

---

## Stage 3 component 3: strategy rule schema

**Change:** Added `schema.py` — seven `Term` kinds as a discriminated union
(`IndicatorTerm`, `PriceTerm`, `ConstantTerm`, `BodyTerm`, `MidpointTerm`,
`RangeTerm`, `ScaledTerm`), `Comparison`, a recursive `Condition` and/or/leaf tree,
`StrategyRule`, and four `KNOWN_STRATEGIES` (SMA crossover, RSI 14/30-70, RSI
2/10-90, morning star).

What is non-obvious: (1) offset validation here is Sacred Gate 1 extended one
layer up — a rule can never even be constructed with a positive (future-peeking)
offset, checked again later at evaluation time as defense in depth. (2) `ScaledTerm`
bans nesting (factors would just multiply — zero added expressiveness, real added
ambiguity for future rule dedup). (3) `and`/`or` require >= 2 children, not just
"not empty" — a single-child branch is identical to its child, a pointless
construct in the same spirit as rejecting bbands' dead `std` param. (4) Morning
star is the actual reason `BodyTerm`/`MidpointTerm`/`RangeTerm`/`ScaledTerm` exist
at all — the other three strategies only need `IndicatorTerm`+`ConstantTerm`.
Verification: all 4 KNOWN_STRATEGIES construct; all 13 planned ValidationError
cases raise correctly; 2 should-succeed edge cases (partial params, incomplete
cross-check) succeed; 16/16 existing tests still green. No formal test_schema.py
yet — that's Component 8.

---

## Stage 3 component 4: pure condition evaluator

**Change:** Added `evaluator.py` (`resolve_term`, `evaluate_comparison`,
`evaluate_condition` against a `BarContext` Protocol) plus a small preceding
touch-up moving `validate_offset` out of `schema.py` (private) into
`indicators.py` (public, next to `MAX_LOOKBACK`) so both modules share one
offset-bound function instead of duplicating it.

What is non-obvious: (1) the offset re-check in `resolve_term` is not pure
ceremony — proven directly by mutating a constructed term's `.offset` post-hoc
(these Pydantic models aren't frozen) and confirming `resolve_term` still
caught it, a gap `schema.py`'s construction-time-only validator cannot close.
(2) `_shifted`'s validation catches a composite constraint `schema.py`
structurally cannot see: a term at `offset=-MAX_LOOKBACK` is legal alone but
illegal once embedded in a crossing comparison, which implicitly needs one bar
deeper. (3) The crossover NaN guard prevents a named bug from the Stage 3
plan's findings — `NaN < x` and `NaN > x` are both `False` in Python, so an
unguarded check can spuriously fire during warmup. (4) Debugged two real
episodes to ground truth rather than assumption: morning star's initial false
negative (traced to one of seven leaves, a synthetic-data error, fixed and
paired with a true-negative to prove the AND composition); and three exit-condition
KeyErrors, pinned to a missing-test-data gap in the fake context (not an
evaluator bug) via exact traceback inspection, closed out by the structural
fact that `evaluate_condition` has no concept of "entry" vs "exit" at all.
`indicators.py`/`schema.py` move verified as byte-for-byte behavior-identical
(diffed smoke-test output before/after). 16/16 existing tests still green.
No formal test_evaluator.py yet — that's Component 8.

---

## Stage 3 component 5: the strategy interpreter

**Change:** Added `strategies/rule_strategy.py` — `make_rule_strategy(rule)`
compiles a validated `StrategyRule` into a real `backtesting.py` `Strategy`
subclass. All 4 `KNOWN_STRATEGIES` now run through the actual `run_backtest`.

What is non-obvious: (1) Two real bugs found and fixed via direct evidence,
not guessing. `ta.sma`'s numba path needs a genuine `int`, not the `float`
every `IndicatorTerm.params` value is typed as — fixed by normalizing
whole-valued floats to `int`, verified safe for fractional params (`bbands`
`lower_std`) via `np.allclose` on actual output, not assumed. (2) The bigger
one: precomputed indicators stored in a dict silently never advanced past
their `init()`-time snapshot — `backtesting.py`'s run loop only re-slices
indicators it discovers as *direct instance attributes* via `isinstance`
scanning, once, right after `init()`; a dict entry is invisible to that scan.
Produced `num_trades=0` with no exception, on data confirmed via plain pandas
to have 22 real crossings. Root cause found by reading `backtesting.py`'s
actual source, not guessing further. Fixed by storing each indicator as its
own named attribute. (3) The regression test written to guard against this
initially passed against the bug when deliberately reintroduced — a false
negative, because it read the attribute directly instead of going through
`BarContext.indicator()`, the actual code path the bug lived in. Rewritten and
proven both ways (fails on bug, passes on fix) — full narrative in
step-04-rule-strategy.md, since this is the one lesson most worth keeping.
Also verified: indicator dedup holds with attribute storage; VWAP works
end-to-end through the real `self.I()` path for the first time. 17/17 tests
green (16 existing + 1 new regression test).
