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
