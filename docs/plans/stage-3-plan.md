# Stage 3 — Compositional Strategy Rule Schema + Two-Tier Indicator System

## Context

Stage 2 delivered `run_backtest(data, strategy_cls, **params) → BacktestResult` with
hardcoded `Strategy` subclasses. The product is an agent that takes ANY strategy
expressed in plain English, translates it to an executable rule, and backtests it.
A fixed menu of strategies contradicts that.

Stage 3 builds:
1. A **general compositional rule format** — a boolean tree of comparisons over indicator
   values and OHLCV references, evaluated bar by bar.
2. A **two-tier indicator system** — ~25 manually verified core indicators + all remaining
   pandas-ta indicators auto-generated (LLM proposes bounds only; code determines
   everything else) and verified by execution.
3. A **rule interpreter** that compiles any valid `StrategyRule` into a backtesting.py
   `Strategy` subclass, ready for `run_backtest`.

**Deliberate change to a standing rule:** "Stages 1–3 use no LLM" is amended. The
extended indicator auto-generation uses the LLM offline, one-time, to propose parameter
bounds. Output is verified by execution. This is a build-time step, not a runtime
dependency. `docs/architecture.md` and `CLAUDE.md` are updated to record this.

**Gate:** three literature strategies produce literature-consistent results AND morning
star is representable and executes. If morning star cannot be expressed, the schema is
incomplete and the gate does not pass.

---

## Empirical findings (verified before planning)

### Indicator API surface
| Indicator | inputs | return type | notes |
|---|---|---|---|
| sma/ema/wma/dema/tema/hma | close | Series | |
| rsi | close | Series | |
| macd | close | DataFrame | cols: `MACD_f_s_g`, `MACDh_f_s_g`, `MACDs_f_s_g` |
| stoch | high,low,close | DataFrame | cols: `STOCHk_k_d_d`, `STOCHd_k_d_d`, `STOCHh_k_d_d` |
| cci/roc/mom/willr | see above | Series | |
| atr/natr/true_range | high,low,close | Series | |
| bbands | close | DataFrame | 5 cols: `BBL_`, `BBM_`, `BBU_`, `BBB_`, `BBP_` |
| obv | close,volume | Series | |
| vwap/mfi/ad | high,low,close,volume | Series | |
| adx | high,low,close | DataFrame | cols: `ADX_`, `ADXR_`, `DMP_`, `DMN_` |

### Critical bugs discovered
- **`bbands` has no `std` parameter** in pandas-ta 0.4.71b0. `std=1.0` vs `std=3.0`
  produces identical output. The param name silently absorbed via `**kwargs`. This is
  exactly the hallucination the verification layer must catch.
- **pandas-ta silently accepts unknown kwargs** — `ta.bbands(close, not_a_real_param=999)`
  runs without error. So "it didn't raise" is not proof a param works.
- **Column names embed params as floats** with irregular formatting (`BBL_14_2.0_2.0`).
  Template-based name prediction is fragile; **prefix matching is robust** (verified:
  prefix always returns exactly 1 match across all tested param combinations).

### Offset/NaN semantics
- Schema offset `k` → array index `k - 1` inside `next()`.
- At the first `next()` call, offsets 0 and −1 are non-NaN; offset −2 and deeper can be
  NaN. The NaN guard is load-bearing.
- NaN > 30 is False → spurious `crosses_above` on warmup. Guard: any comparison with a
  NaN operand evaluates to False; crossover ops require all four values non-NaN.

---

## Components (build order)

### 1. `src/backtester/indicators.py` — two-tier indicator registry

```python
MAX_LOOKBACK = 5

@dataclass(frozen=True)
class IndicatorSpec:
    fn: Callable               # ta.rsi, ta.macd, etc.
    inputs: tuple[str, ...]    # ("close",) or ("high","low","close")
    params: dict[str, tuple[float, float]]  # name → (min, max) inclusive
    column_prefix: str | None = None   # for DataFrame returns: "BBL_", "MACDs_", etc.
    cross_check: dict | None = None    # {"type":"less_than","left":"fast","right":"slow"}
    tier: Literal["core", "extended"] = "core"
    verified: bool = True
    verified_on: date | None = None
    lib_version: str | None = None
```

**Key design decisions:**
- `inputs` determined by **signature introspection** (checking params against
  `{"open","high","low","close","volume"}`). Not LLM-proposed — provably derivable.
- `column_prefix` replaces the base spec's `column: Callable` — verified to match
  exactly 1 column across all tested param combinations. No lambda needed.
- Multi-output indicators get one registry entry per output (MACD, MACD_SIGNAL,
  MACD_HISTOGRAM; STOCH_K, STOCH_D; BB_LOWER, BB_MID, BB_UPPER; ADX, DMP, DMN).
- `cross_check` is declarative: `{"type":"less_than","left":"fast","right":"slow"}`.
  A small interpreter (`_apply_cross_check`) handles the three types: `less_than`,
  `greater_than`, `not_equal`.
- For bbands: **no `std` param declared** — it is non-functional in this version.
  Only `length` is registered. This is the honest call; documenting a dead param
  would allow rules that claim to vary std when they don't.

**Core set (~25 entries, one per output column):**
```
# Trend/MA (Series, close-only)
SMA, EMA, WMA, DEMA, TEMA, HMA

# Momentum (Series unless noted)
RSI, CCI, ROC, MOM, WILLR
MACD          (DataFrame → prefix "MACD_")
MACD_SIGNAL   (DataFrame → prefix "MACDs_")
MACD_HISTOGRAM(DataFrame → prefix "MACDh_")
STOCH_K       (DataFrame → prefix "STOCHk_")
STOCH_D       (DataFrame → prefix "STOCHd_")

# Volatility
ATR, NATR, TRUE_RANGE
BB_LOWER (DataFrame → prefix "BBL_")
BB_MID   (DataFrame → prefix "BBM_")
BB_UPPER (DataFrame → prefix "BBU_")

# Volume
OBV, VWAP, MFI, AD

# Trend strength
ADX       (DataFrame → prefix "ADX_")
DMP       (DataFrame → prefix "DMP_")
DMN       (DataFrame → prefix "DMN_")
```

### 2. `src/backtester/schema.py` — the rule format

**Terms** (discriminated union on `kind` — a closed set of primitive kinds):
- `IndicatorTerm(kind="indicator", name, params, offset=0)`
- `PriceTerm(kind="price", field, offset=0)`
- `ConstantTerm(kind="constant", value)`
- `BodyTerm(kind="body", offset=0)` → abs(open − close)
- `MidpointTerm(kind="midpoint", offset=0)` → (open + close) / 2
- `RangeTerm(kind="range", offset=0)` → high − low
- `ScaledTerm(kind="scaled", term, factor)` — no nesting enforced by validator

`Term = Annotated[union, Field(discriminator="kind")]`

Validators:
- every offset: `−MAX_LOOKBACK <= offset <= 0` (positive = hard error)
- IndicatorTerm: name in registry, verified=True, params present+in-bounds, cross_check
- ScaledTerm: factor > 0 and finite; term is not ScaledTerm (tested)

**Comparison:** `left: Term, op: Literal[gt/lt/gte/lte/crosses_above/crosses_below/eq_within], right: Term, tolerance: float | None`

**Condition:** recursive `kind: Literal[and/or/leaf]` with comparison or children.

**StrategyRule:** `name, description, literature_source?, entry: Condition, exit: Condition?, exit_after_bars: int?` — at least one of exit/exit_after_bars required.

**KNOWN_STRATEGIES** — four worked examples:
1. SMA(10/30) crossover (Brock et al. 1992)
2. RSI(14) 30/70 (Wilder 1978)
3. RSI(2) 10/90 (Connors & Alvarez 2009)
4. Morning star (multi-bar candlestick, the expressiveness proof)

### 3. `src/backtester/evaluator.py` — pure condition evaluation

Separated from backtesting.py wiring for isolated testing.

```python
class BarContext(Protocol):
    def price(self, field: str, offset: int) -> float: ...
    def indicator(self, key: IndicatorKey, offset: int) -> float: ...

def resolve_term(term: Term, ctx: BarContext) -> float
def evaluate_comparison(cmp: Comparison, ctx: BarContext) -> bool
def evaluate_condition(cond: Condition, ctx: BarContext) -> bool
```

Rules:
- positive offset → `raise ValueError` (Sacred Gate 1 extension)
- NaN operand → comparison is False
- crossover: requires all four values (current+previous, both sides) non-NaN
- ScaledTerm: `resolve(inner_term) * factor`
- BodyTerm/MidpointTerm/RangeTerm: resolved from price values at offset

### 4. `src/backtester/strategies/rule_strategy.py` — the interpreter

```python
def make_rule_strategy(rule: StrategyRule) -> type[Strategy]
```

- `init()`: walk rule, collect all IndicatorTerms, **deduplicate on
  `(name, frozenset(params.items()))`**, precompute each once via `self.I(...)`.
  For multi-input indicators (ATR, STOCH, etc.), pass the price series the registry's
  `inputs` tuple specifies. For DataFrame-returning indicators, select the column
  using `column_prefix`.
- `next()`: build BarContext backed by `self.data` and precomputed indicators; evaluate
  entry/exit conditions via the evaluator; track bars-held for `exit_after_bars`.
- Scope: long-only, single position, full allocation (documented boundary).

**Provenance:** `make_rule_strategy` records which indicators it used and their tier.
After `run_backtest`, the result is augmented with `indicators_used` and
`extended_indicators_used`.

### 5. `BacktestResult` provenance fields

Add to `src/backtester/result.py`:
```python
indicators_used: list[str] = []
extended_indicators_used: list[str] = []
```

These are populated by `make_rule_strategy` and threaded through. A result using only
core indicators has `extended_indicators_used == []`. Stage 5/7 can surface this.

### 6. `src/llm_client/__init__.py` — minimal LLM abstraction

Architecture §7 mandates all LLM access through one module. This is the first LLM call
in the project. Build the minimal version:

```python
def structured_output(
    prompt: str,
    response_model: type[T],
    model: str = "claude-haiku-4-5-20251001",
    max_tokens: int = 4096,
) -> T:
    """Call Claude, parse the response into response_model via Pydantic."""
```

Uses the Anthropic SDK directly. Provider abstraction (Bedrock vs direct API) is a
one-line change here when needed. Stage 5 extends this with prompt caching, retries, etc.

Add `anthropic>=0.40` to pyproject.toml dependencies.

### 7. Extended indicator generation + verification

**`scripts/generate_extended_indicators.py`:**
1. Introspect pandas-ta for all indicator functions (deterministic).
2. Subtract the core set.
3. For each remaining indicator, deterministically extract: function reference, input
   params (from signature), whether it returns Series or DataFrame (by executing once).
4. **LLM proposes ONLY:** `(min, max)` bounds per parameter and cross-param rules. This
   is the one thing code cannot determine. Model: `claude-haiku-4-5-20251001` via
   `llm_client.structured_output`.
5. Write to `src/backtester/extended_indicators.py` as Python data (list of IndicatorSpec
   with `tier="extended"`, `verified=False`).

**`scripts/verify_extended_indicators.py`:**
For each `verified=False` spec:
1. Call the function with params at proposed min and max. Does it execute?
2. **Parameter sensitivity test** (the bbands lesson): run with two different param
   values and confirm the output actually changes. A param that doesn't affect the output
   is flagged as dead.
3. Produces non-NaN values after warmup?
4. If DataFrame, does the prefix match exactly one column?

Outcomes: `verified=True` or `verified=False` with recorded reason. Only verified
indicators are usable in rules.

**Runtime guard:** if a verified-True extended indicator produces all-NaN or wrong shape
at backtest time, the backtest fails loudly.

### 8. Tests

**`tests/backtester/test_indicator_core.py`:**
- Every core indicator runs on 500-bar synthetic data without error
- Returns expected shape (Series or DataFrame with named column via prefix)
- Produces non-NaN values after warmup
- Params at declared min and max execute; params outside bounds rejected by schema

**`tests/backtester/test_evaluator.py`:**
- gt/lt/gte/lte on constants and price terms
- crosses_above true only on flip; **false when either value is NaN**
- NaN operand → False
- AND/OR composition
- body/midpoint/range compute correctly
- ScaledTerm multiplies correctly
- **positive offset raises** (Sacred Gate 1 extension)

**`tests/backtester/test_schema.py`:**
- All KNOWN_STRATEGIES + morning star parse and validate
- ValidationError on: unknown indicator; unverified indicator; out-of-range param;
  MACD fast≥slow; leaf with children; and-node no children; no exit and no exit_after_bars;
  offset beyond MAX_LOOKBACK; **positive offset**; eq_within no tolerance;
  **nested ScaledTerm**

**`tests/backtester/test_rule_strategy.py`:**
- Each KNOWN_STRATEGY compiled and run on 500-bar synthetic, num_trades > 0
- Morning star runs (few trades fine; must execute)
- Indicator dedup: RSI(14) in entry+exit → exactly 1 indicator registered
- Positive-offset term raises

**`tests/backtester/test_extended_indicators.py`:**
- Generated stubs are structurally valid
- Verification correctly flips a known-good indicator to verified
- **Verification REJECTS a deliberately-mis-specified indicator** (wrong claimed bounds
  / dead param) — analogue of "eval harness catches broken agent"
- Rule using unverified indicator fails validation
- Result using extended indicator has provenance flag populated

**Regression:** `pytest tests/` — Stages 1+2 green. HARD BLOCKER.

### 9. Gate script — `scripts/verify_stage3_gate.py`

Real AAPL 2015-01-01 → 2024-12-31, compiled via `make_rule_strategy` + `run_backtest`.

| Strategy | num_trades | sharpe | max_dd | If out of bounds: check |
|---|---|---|---|---|
| SMA(10/30) | 10–80 | < 3.0 | < −1% | 0 trades → warmup; ≥3.0 → lookahead |
| RSI(14) 30/70 | 20–200 | < 3.0 | < −1% | 0 trades → thresholds; ≥3.0 → lookahead |
| RSI(2) 10/90 | 50–500 | < 3.0 | < −1% | < 50 → dedup key collision |
| Morning star | ≥ 1 | < 3.0 | any | 0 → ATR scale factors too strict |

### 10. Alembic baseline (stage close)

Add `alembic>=1.13`; `alembic init migrations`; configure `env.py` against Base/settings;
autogenerate baseline revision; `alembic upgrade head` on prod+test; `alembic current`
shows head.

### 11. Doc updates

- `docs/architecture.md`: amend "Stages 1–3 use no LLM" to record the bounded offline
  generation step. Make explicit: build-time, not runtime.
- `CLAUDE.md`: same amendment in the build order table (add "One-time LLM (offline)" to
  Stage 3's LLM? column).

---

## Files

```
src/
  backtester/
    indicators.py              ← NEW  core registry, IndicatorSpec, MAX_LOOKBACK
    extended_indicators.py     ← GENERATED  extended tier specs
    schema.py                  ← NEW  Terms, Comparison, Condition, StrategyRule
    evaluator.py               ← NEW  pure term/condition evaluation
    strategies/
      rule_strategy.py         ← NEW  make_rule_strategy
      sma_crossover.py         (unchanged — Stage 2 gate tests still import it)
    data_loader.py, engine.py  (unchanged)
    result.py                  (+ provenance fields)
  llm_client/
    __init__.py                ← NEW  minimal structured_output

scripts/
  generate_extended_indicators.py   ← NEW
  verify_extended_indicators.py     ← NEW
  verify_stage3_gate.py             ← NEW

migrations/                    ← NEW (stage close)

tests/backtester/
  test_indicator_core.py       ← NEW
  test_evaluator.py            ← NEW
  test_schema.py               ← NEW
  test_rule_strategy.py        ← NEW
  test_extended_indicators.py  ← NEW
  (existing Stage 2 files unchanged)
```

---

## Verification

1. `pip install -e '.[dev]'` clean with pandas-ta + anthropic.
2. `pytest tests/` fully green — all stages. **HARD BLOCKER.**
3. `python scripts/verify_stage3_gate.py` — literature ranges + morning star executes.
4. Morning star expressible and running — if not, gate fails.
5. Extended layer: generation + verification scripts complete; nonzero verified count;
   rejection test passes.
6. `alembic current` shows head on prod + test DBs.

---

## Explanation checkpoints

- After registry + schema validate → `explanation-writer` step-01
- After evaluator + rule_strategy run → `explanation-writer` step-02
- After extended-tier generation + verification → `explanation-writer` step-03
- After gate passes → `explanation-writer` stage-3-summary
- Commit-log entry + push after each commit
