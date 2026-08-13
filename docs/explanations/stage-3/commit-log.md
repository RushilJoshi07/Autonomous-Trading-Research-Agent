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

---

## Stage 3 component 6: BacktestResult provenance fields

**Change:** Added `indicators_used`/`extended_indicators_used` fields to
`BacktestResult` (`result.py`), threaded through `engine.py`'s `run_backtest`
via `getattr(strategy_cls, "indicators_used", [])`. Closes the gap between
Component 5 (which computed this data) and anywhere it could actually be seen.

What is non-obvious: the `getattr` fallback (not a required attribute, not an
`isinstance(strategy_cls, RuleStrategy)` check) is the one real decision —
`run_backtest` is shared Stage 2 infrastructure used by both `SMACrossover`
(no concept of provenance) and `RuleStrategy` (always has it); an `isinstance`
check would make Stage 2's `engine.py` import from Stage 3's
`strategies/rule_strategy.py`, inverting the correct dependency direction.
Silent empty-list fallback risk acknowledged explicitly rather than ignored:
today there's exactly one producer of this attribute so no typo/fan-out risk
exists yet, and unlike the Component 5 dict-storage bug (a wrong computation),
a wrong provenance value today would be an incomplete disclosure, not a false
one — nothing currently branches on these fields. Named the trigger for when
that stops being true: a second producer, or Stage 5/6 actually scrutinizing
`extended_indicators_used`. Verified both paths (RuleStrategy populates real
data, SMACrossover defaults safely) plus full regression including Stage 2's
sacred-gate tests, which exercise this exact path. 17/17 green, unchanged.

---

## Stage 3 component 7: minimal LLM abstraction (llm_client)

**Change:** Added `src/llm_client/__init__.py` — `structured_output(prompt,
response_model)` calls Claude via `AnthropicBedrock` (forced tool-use for
reliable structured output) and returns a validated Pydantic instance or
raises `StructuredOutputError`. First LLM call anywhere in the project, per
the amended "Stages 1-3 use no LLM except one bounded offline case" rule.

What is non-obvious: (1) Corrected the plan's stated provider (direct
Anthropic API → Bedrock) against the user's actual cost-driven intent,
verified this didn't contradict architecture.md's actual (permissive, not
exclusive) wording before proceeding. (2) No retry loop — deliberately
deferred to Stage 5 per the plan's own scoping, since retry policy needs a
budget Stage 5's loop guardrails don't exist yet to provide; `StructuredOutputError`
carries the raw `Message` + `ValidationError` so that future retry logic
doesn't require changing this function's contract. (3) `aws_profile` is an
explicit parameter (default `"bedrock"`), not `AWS_PROFILE` env var — third
occurrence this session of ambient-shell-state breaking portability
(`CLAUDE_CODE_OAUTH_TOKEN`, this same AWS profile). (4) Three real, layered
failures resolved via direct evidence, not guessing, before a live call
worked: unresolvable credentials (traced to `AnthropicBedrock` only checking
the boto3 default profile), a named profile mismatch (`bedrock`, not
`default`, found via structural grep on config section names, never secret
values), and a Bedrock-specific on-demand-invocation error requiring an
inference-profile ID (`us.anthropic.claude-sonnet-4-6`, looked up directly
via `list_inference_profiles()` against the real account, not guessed at
from a naming pattern). Live end-to-end call verified working; full
narrative in step-06-llm-client.md. 17/17 tests green, unchanged.

---

## Stage 3 component 8: extended indicator generation and verification

**Change:** Added the two-tier extended registry: `scripts/generate_extended_indicators.py`
(introspection + batched LLM bounds proposals) and `scripts/verify_extended_indicators.py`
(execution-based checks: shape, per-param sensitivity, cross-check, non-NaN).
193 candidates generated, 127 verified for real. New `registry.py` merges
core+extended with a collision check; `schema.py`/`rule_strategy.py` switched
from `CORE_INDICATORS` to `ALL_INDICATORS` — the actual fix that makes extended
indicators usable at all. New `test_indicator_core.py`, `test_schema.py`,
`test_extended_indicators.py` (116 new tests). 133/133 green.

What is non-obvious: (1) fixed a real, latent Component-2 bug — pandas-ta's
`open_` alias was never matched by `_infer_inputs`, invisible until this sweep
exercised an open-requiring function. (2) Column-prefix derivation needed 3
sample points, not 2 — a naive 2-point diff was fooled by `kc`'s `length`
bounds `(1, 100)` coincidentally sharing a leading digit. (3) `ta.ichimoku`
uniquely returns `tuple[DataFrame, DataFrame]`, not Series/DataFrame — excluded
explicitly rather than crashing downstream; this also appears to have resolved
an unreproducible native `SIGTRAP` crash whose exact mechanism was never fully
confirmed (recorded honestly in step-07, not hidden). (4) Cross-check claims
(e.g. MACD-style `fast < slow`) are execution-verified via satisfied/violated
orderings, not taken on the LLM's word — a requirement raised during plan
review, alongside the registry collision check and per-chunk LLM fault
isolation, all three implemented and verified working, not just acknowledged.
Full narrative in step-07-extended-indicators.md.

---

## Stage 3 plan §8: complete the formal test suite

**Change:** Added `test_evaluator.py` (28 new tests) and expanded
`test_rule_strategy.py` (+6) and `test_extended_indicators.py` (+3), closing
out the deferred plan §8 test-suite pass. Suite goes from 133 to 170 tests,
all green. No `src/` changes.

What is non-obvious: (1) `test_evaluator.py`'s `FakeBarContext` raises
`KeyError` on any unset `(field, offset)` lookup rather than defaulting —
deliberate, so a forgotten test-setup value crashes loudly instead of
silently evaluating against a phantom zero. (2) The crossover test suite
specifically includes an "already above on both bars, should NOT fire" case
— without it, a regression degrading `crosses_above` into a plain threshold
check would pass every other test. (3) Positive-offset-raises is tested at
two layers on purpose (isolated `resolve_term`, and the whole compiled
`make_rule_strategy` + `run_backtest` pipeline) via the established
post-construction `.offset` mutation bypass, since schema.py's
construction-time validator can't see a value set after construction. (4)
Applied the Component-5-dedup-test lesson proactively: monkeypatched
`validate_offset` across all three modules' independently-bound references
(patching only `indicators.py`'s copy would NOT affect `schema.py`'s or
`evaluator.py`'s, since `from .indicators import validate_offset` binds the
name at import time) and confirmed both positive-offset tests genuinely fail
without the fix — one with an unexpected `KeyError`, the other with "DID NOT
RAISE" — before trusting either as real coverage. (5) The extended-indicator
stub-validity test imports the real, checked-in `EXTENDED_INDICATORS` dict
directly, not a fixture — confirmed with the user twice before approval,
since only the real artifact can catch a future bad regeneration. Full
narrative in step-08-test-suite-completion.md.
