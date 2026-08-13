# Handoff — Stage 3, after Component 7 (llm_client)

## Where things stand

Stage 3 build order: **Components 1-7 committed and done.** Component 8 (extended
indicator generation) is next.

1. Dependencies (pandas-ta, anthropic) — done
2. `indicators.py` — core registry, 28 entries — done
3. `schema.py` — Term/Comparison/Condition/StrategyRule, KNOWN_STRATEGIES — done
4. `evaluator.py` — pure condition evaluation — done
5. `strategies/rule_strategy.py` — `make_rule_strategy` interpreter — done
6. `BacktestResult` provenance fields (`indicators_used`, `extended_indicators_used`) — done
7. `llm_client/__init__.py` — `structured_output` via AnthropicBedrock — done

Verify actual state with `git log --oneline` and `git status` before trusting this
document blindly — it's a snapshot, not a live source of truth.

## What's NOT done yet (remaining Stage 3 work, per docs/plans/stage-3-plan.md)

- Component 8: `scripts/generate_extended_indicators.py` — LLM proposes bounds/
  cross-checks for non-core pandas-ta indicators
- `scripts/verify_extended_indicators.py` — execute-and-check each generated spec
- Full test suite: `test_indicator_core.py`, `test_evaluator.py`, `test_schema.py`,
  `test_rule_strategy.py`, `test_extended_indicators.py` — **none of these exist yet**.
  All verification so far has been manual/interactive, not automated pytest files.
  This is a real gap — no regression suite currently protects Components 2-7.
- `scripts/verify_stage3_gate.py` — the actual gate script (literature strategies +
  morning star on real AAPL data)
- Alembic baseline setup (deferred to stage close, now due)
- **Doc updates not yet done**: `docs/architecture.md` and `CLAUDE.md` still need the
  amendment recording that Stage 3 uses the LLM (offline, build-time) — this was
  planned in the original Stage 3 spec (§11 / §2 of the addendum) and has not been
  applied yet. Do this before Stage 3 is declared closed.

## Major decisions that affect future work — do not silently re-derive these

### Provider: AWS Bedrock, not direct Anthropic API
- Reason: cost — AWS student credits ($200) fund this instead of a separate
  Anthropic bill.
- SDK: `anthropic[bedrock]` package, `AnthropicBedrock` client — NOT raw
  `boto3.client("bedrock-runtime")`. Verified: `AnthropicBedrock().messages` and
  `Anthropic().messages` are the literal same class, same `.create()` signature,
  same return type. `structured_output`'s call and parsing logic work identically
  against either backend — only client construction differs.
- Model: `anthropic.claude-sonnet-4-6` — confirmed via `list_foundation_models`
  against the real account, and confirmed working via a real invocation.
  **`anthropic.claude-sonnet-5` is listed in the catalog but returns
  AccessDeniedException — not usable yet.** Don't default to it.
- Region: `us-east-1`.
- AWS profile: named **`bedrock`**, not `default`. `boto3.Session()` with no
  explicit profile only checks `[default]`, so a bare Session() silently finds no
  credentials even though they exist. Fixed by adding an explicit `aws_profile`
  parameter to `structured_output` (default `"bedrock"`), threaded into
  `AnthropicBedrock(aws_region=..., aws_profile=...)` — NOT via a shell-level
  `AWS_PROFILE` env var, because that only exists in whatever terminal happens to
  set it and breaks in any other session (this exact class of bug hit 3 times this
  session: `CLAUDE_CODE_OAUTH_TOKEN`, `aws sts get-caller-identity`, and this).
- **General lesson**: anything depending on ambient shell/environment state is
  fragile across sessions. Prefer explicit parameters/config over environment
  assumptions, everywhere in this codebase.

### structured_output has NO retry logic (deliberately)
- Single call, validate once, raise a clear exception on failure. No retry loop.
- Reason 1: retry-with-feedback (forced tool-use, send validation error back as a
  tool_result, re-ask) is real but unverified — no live Bedrock credentials were
  available in the execution shell to test the tool-result protocol mechanics
  against a real call. Don't build unverified logic; this project's whole discipline
  has been "test empirically before trusting," and this would violate that.
- Reason 2: a sensible retry CAP requires a budget, and the budget belongs to
  Stage 5's agent loop (step/cost guardrails), which doesn't exist yet. Inventing a
  retry count now means guessing at a policy Stage 5 owns.
- **What this means for Stage 5**: the raised exception carries BOTH the Pydantic
  validation error AND the raw Claude response — deliberately, so Stage 5 can build
  retry-with-feedback by wrapping this function in a loop, without needing to modify
  `structured_output` itself. This function is a building block Stage 5 calls, the
  same way Stage 3 calls Stage 2's `run_backtest` repeatedly rather than Stage 2
  needing to loop internally.

## The most important bug found in Stage 3 — hold onto this for interviews

**Component 5, the indicator storage bug — a real, silent lookahead-bias bug found
by the project's own verification discipline, one stage after Sacred Gate 1 was
built specifically to prevent lookahead bias.**

- Indicators were originally stored in a dict (`self._series[key] = self.I(...)`)
  for deduplication. backtesting.py's per-bar simulation loop only re-slices
  indicators it discovers as **direct named instance attributes** (an
  `isinstance(v, _Indicator)` scan over `strategy.__dict__`, run once after
  `init()`). A dict value is invisible to that scan. Result: dict-stored indicators
  silently never advanced past their full-length `init()`-time snapshot — every bar,
  for the whole backtest, the "current" indicator value was actually computed with
  the entire future visible.
- Caught by comparing `len()` of the same indicator accessed via a named attribute
  vs. a dict key at the same bar: 45 vs 500. Unambiguous proof.
- Fixed by storing each unique indicator as its own named attribute
  (`self._ind_0`, `self._ind_1`, ...), read back via `getattr()`.
- **A regression test for this exists** in `test_rule_strategy.py` (informal —
  written during the session, not yet part of the formal Component 8 test suite
  build-out). Its FIRST version was itself broken — it read `getattr(self, attr)`
  directly, which is always correctly live-updated by backtesting.py regardless of
  the bug, so it passed even with the bug deliberately reintroduced (a false
  negative). Fixed by routing the test through `self._ctx.indicator(key, offset=0)`
  — the same method `evaluate_condition` actually calls in production. Verified both
  ways: fails with the bug present, passes with the fix.
- **When Component 8's formal test suite is built, this exact test must be
  preserved/formalized with a comment referencing this bug** — do not let it be lost
  or silently rewritten into something weaker.

## Other real findings from Stage 3 (all verified empirically, not assumed)

- `bbands` has no working `std` parameter in the installed pandas-ta version — the
  real functional params are `lower_std`/`upper_std`. Registered those instead;
  `std` is not registered at all (a dead param would let rules claim to vary
  something that does nothing).
- pandas-ta silently accepts unknown kwargs — "it didn't raise" is never proof a
  param works. This is why extended-indicator verification includes a **sensitivity
  test** (run with two different values, confirm output actually changes).
- Column names for DataFrame-returning indicators embed params with irregular float
  formatting — **prefix matching** (`column_prefix`, e.g. `"BBL_"`) is robust;
  template-based exact name prediction is not.
- VWAP needs a real `pandas.DatetimeIndex` — silently returns `None` (prints a
  warning, doesn't raise) on integer-indexed data. Confirmed backtesting.py's
  internal data DOES preserve a real DatetimeIndex through to `init()`, so no special
  handling was needed in `rule_strategy.py` — but this was verified, not assumed.
- `ta.stoch(..., d=1)` raises regardless of other params — a real bug in this
  pandas-ta version's rolling-window handling at `d=1`. Fixed by raising
  `STOCH_K`/`STOCH_D`'s `d` bound to `(2, 50)`.
- numba-jitted functions (e.g. `ta.sma`) need real Python `int` for bar-count params,
  not `float` — `IndicatorTerm.params` is uniformly typed `float` (correct for
  bounds-checking) but must be normalized to `int` before calling. Verified safe for
  genuinely fractional params too (bbands `lower_std=1.0` vs `1` — numerically
  identical via `np.allclose`, differ only in cosmetic column-name formatting that
  prefix matching already ignores).
- Schema offset `k` → backtesting.py array index `k - 1` inside `next()`. Offsets 0
  and -1 are non-NaN on the first `next()` call; -2 and deeper can be NaN.
  `NaN > x` / `NaN < x` are both `False` in Python — this can cause spurious
  `crosses_above` firing during warmup if not guarded. Guard: any comparison with a
  NaN operand is `False`; crossover ops require all four values (both sides, current
  + previous) non-NaN.
- Crossover's "previous bar" offset is computed by the evaluator (`_shifted`), not
  declared anywhere in the original rule — `schema.py` cannot validate it, because it
  only ever checks a term's own declared offset in isolation, never in the context of
  an operator that implicitly reaches one bar deeper. `evaluator.py` re-validates
  offsets at resolution time for exactly this reason (also catches direct attribute
  mutation post-construction, since Pydantic models here aren't frozen/
  validate_assignment).
- `entry_bar` (via `self.trades[-1].entry_bar`) is used for `exit_after_bars` bar
  counting rather than manual tracking — verified more correct, not just simpler: it
  reports actual fill bar (next-bar-open, per Stage 2 semantics), which a hand-rolled
  counter would get wrong by exactly one bar.

## Process/tooling decisions in effect (apply going forward)

- **Alembic**: still deferred. Was supposed to happen "at stage close" — Stage 3 is
  not yet closed (test suite, gate script, extended indicators all outstanding), so
  this is still correctly pending, not overdue. Revisit when those are done.
- **Git post-commit hook** for explanation-writer: scoped-allowlist permissions
  chosen over `--dangerously-skip-permissions`. Uses `claude setup-token` for
  headless auth — a SEPARATE credential from `ANTHROPIC_API_KEY`/Bedrock (this token
  authenticates the `claude` CLI/Claude Code identity; has nothing to do with
  `llm_client`'s Bedrock auth). Status of actually finishing/testing this hook:
  **check whether it was completed** — it was being built around the same time as
  Component 6/7 and may still be in progress. Verify before assuming it's live.
- **Explanation files**: three-tier system (commit notes → step explainers → stage
  synthesis). Step explainers have been written and committed for each component
  1-7 so far — verify with `ls docs/explanations/` that all are actually present,
  not just claimed.
- **AWS onboarding**: all 5 "Explore AWS" credit-earning tasks completed (EC2, RDS,
  Lambda, Bedrock, Budgets) for the $200 credit. **Cleanup still owed**: confirm the
  second EC2 instance was terminated (not just stopped), and confirm the RDS test
  instance (`onboarding-test-db`) was deleted after its credit registered — both
  bill hourly if left running and neither is needed for anything.

## Immediate next step

Component 8 per `docs/plans/stage-3-plan.md`: extended indicator generation. Uses
`llm_client.structured_output` — this will be its first real caller. LLM proposes
ONLY `(min,max)` bounds and cross-param rules (the two things code cannot determine);
everything else (inputs, multi-output detection, param existence) comes from
deterministic introspection, per the earlier resolved design question. Output stored
as a generated Python module (not JSON+lambdas), per the earlier resolved storage
question. Every generated spec starts `verified=False` and must pass execution-based
verification (including a sensitivity test, per the bbands lesson) before becoming
usable in a rule.
