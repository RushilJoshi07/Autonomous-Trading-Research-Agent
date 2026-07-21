# Rigor rules — data pipeline

Applies to: data ingestion, caching, storage, universe construction.

## The agent reads the database, never the network
Rationale: speed (many tickers x network round-trips is unusable), reliability
(yfinance is unofficial and flaky), and above all REPRODUCIBILITY — the same study
run twice must use identical data, or the science is worthless.

Only the scheduled ingestion job talks to yfinance.

## Corporate actions will silently corrupt an append-only store
Splits and dividends RETROACTIVELY change adjusted prices. Append-only storage
drifts out of sync with reality, producing fake single-day crashes the strategy
"trades".

Required handling:
- Store BOTH raw and adjusted prices. Raw never changes; adjusted does.
- Check the splits/dividends feed weekly; re-fetch only affected tickers.
- Full re-fetch monthly as a safety net.
- LOG EVERY CHANGE. Otherwise a study giving different numbers next month is a mystery.

## Record when every row was fetched
Without fetch timestamps, studies are not reproducible.

## Timeframes
- v1 is DAILY ONLY. Free intraday history is severely limited (~7 days of 1-minute,
  ~60 days of 5-60 minute bars), so rigorous multi-year intraday studies are not
  possible on free data. This is a documented scope boundary, not an oversight.
- Weekly/monthly bars are RESAMPLED from daily. Never store what you can compute.

## Survivorship bias
Today's index contains companies that MADE IT. Bankrupt and delisted names are
invisible, so results are inflated — and the bias appears nowhere in the output.

- Correct fix: point-in-time membership, reconstructed by walking backwards from
  today's constituent list through documented historical additions/removals.
- It will NOT be fully solved on free data: delisted tickers often lack free price
  history, and ticker symbols get REUSED (so key on ticker PLUS date range).
- Therefore: MEASURE THE COVERAGE GAP AND DISCLOSE IT in every study that uses it.

Scope note: full reconstruction is a project on its own. Stage 1 uses today's
universe with a CLEARLY DOCUMENTED limitation. Do not let this swallow Stage 1.

## Universe selection must be point-in-time
Screening on "past year's volatility" using today's data and then backtesting from
2015 uses future information to select the universe. This is lookahead bias one
layer above the backtester, and it is easy to miss.

## Thresholds are relative, never hand-picked
Not "volatility below 20%" but "lowest volatility quintile within the sector".
A hand-picked number can be quietly retuned until the backtest looks good — that is
overfitting hidden in the universe definition.

Sensitivity testing is required: quintile, tercile, decile. If a finding survives
only one cut, it is an artifact of the threshold, not an edge.
