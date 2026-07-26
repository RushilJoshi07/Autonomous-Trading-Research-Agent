# Commit log

Lightweight notes after each commit — what changed, why, anything non-obvious.

---

## Stage 1 — Data pipeline (gate passed)

**Components:** config/pyproject scaffolding, five DB models, yfinance fetcher with
tenacity retry, upsert with raw/adj separation, per-ticker runner, corporate action
handler, universe, CLI, test suite (6 tests).

What is non-obvious: (1) ON CONFLICT set_ omits raw_* by design — SQL-level
enforcement that raw prices never change. (2) `_log_ticker_error` opens its own
session because the failed ticker's session may be in an indeterminate state after
a FetchError. (3) The `autouse` conftest fixture routes the runner to the test DB
for every test — missing it would silently write to production. (4) Tenacity stores
the sleep callable at decoration time, so `_history.retry.sleep` must be patched
directly, not `time.sleep`. (5) yfinance `end` is exclusive — a one-day fetch needs
`end = target_date + timedelta(days=1)`.

Gate: 6/6 tests pass. Data matches independent source; caching increments by cached
date; per-ticker isolation proved; raw/adj separation proved; retry and FetchError
type confirmed.

---

## Stage 2 — Backtesting engine (gate passed)

**Components:** backtesting.py dependency, data_loader.py (adj_* → OHLCV DataFrame),
result.py (BacktestResult Pydantic model), engine.py (run_backtest wrapper),
sma_crossover.py (SMACrossover strategy), sacred gate tests, test suite (9 tests).
Root conftest.py introduced to share DB fixtures across test packages.

What is non-obvious: (1) `trade_on_close=True` was required for the lookahead gate —
with the default (fill at next open), a shift(-1) signal only had Sharpe ≈ 1.45, not
distinguishable from a good strategy. Only filling at the same bar's close exploits
the signal correctly. (2) `finalize_trades=True` closes open positions at the last
bar and includes them in stats; without it, the last trade is excluded and a
UserWarning fires. (3) Strategy parameters are forwarded via `bt.run(**kwargs)`, not
to the `Backtest()` constructor — backtesting.py applies them before `init()` runs.
(4) `self.I()` wraps indicators for bar-by-bar slicing in `next()` — raw numpy
arrays do not have this containment. (5) `crossover()` from backtesting.lib detects
the crossing event (prev: a < b, current: a > b) — a direct `>` comparison fires
every bar, not just the crossing bar. (6) Root conftest.py fixtures are visible to
all test subdirectories; package-level conftest.py is visible only within its package.

Gate: 16/16 tests pass (Stage 1 non-regressed). Gate 1a: lookahead Sharpe > 3.0,
clean Sharpe substantially lower. Gate 1b: cost reduces total return; commission_pct
audit field correctly populated.
