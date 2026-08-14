"""Stage 3 gate: all four KNOWN_STRATEGIES against real AAPL data.

Reads AAPL 2015-01-01..2024-12-31 from the already-ingested Postgres cache
(Stage 1's job, not this script's) and checks each strategy's trade count,
Sharpe ratio, and max drawdown against the literature-consistent bounds in
docs/plans/stage-3-plan.md section 9. This is the actual Stage 3 gate --
plan §8's test suite proved the pipeline's logic correct against synthetic
data; this proves it against real market history.

Run: .venv/bin/python scripts/verify_stage3_gate.py
Exit code 0 = all four strategies within bounds, OR outside bounds only via a
disclosed, investigated exception recorded in KNOWN_DEVIATIONS below. 1 = at
least one unexplained violation, or too many disclosed deviations have
accumulated (see MAX_DEVIATIONS_BEFORE_REVIEW).
"""

from __future__ import annotations

import sys
from datetime import date

import pandas as pd

from backtester.data_loader import load_price_data
from backtester.engine import run_backtest
from backtester.result import BacktestResult
from backtester.schema import KNOWN_STRATEGIES, StrategyRule
from backtester.strategies.rule_strategy import make_rule_strategy
from data_pipeline.db.session import SessionFactory

TICKER = "AAPL"
START = date(2015, 1, 1)
END = date(2024, 12, 31)

# Bounds exactly as specified in docs/plans/stage-3-plan.md section 9.
# max_dd is a FLOOR on realism (max_drawdown_pct is always <= 0): the strategy
# must have drawn down by at least this much at some point over ~10 years of
# real data, or a near-zero drawdown itself is suspicious. sharpe is a
# CEILING: implausibly high Sharpe over a real multi-year backtest is the
# same lookahead signature Stage 2's own gate test uses -- there is no floor,
# since a legitimately bad Sharpe is not this gate's concern.
BOUNDS: dict[str, dict] = {
    "sma_10_30_crossover": {
        "min_trades": 10,
        "max_trades": 80,
        "max_sharpe": 3.0,
        "max_dd_pct": -1.0,
        "zero_trades_hint": "warmup",
        "high_sharpe_hint": "lookahead",
    },
    "rsi_14_30_70": {
        "min_trades": 20,
        "max_trades": 200,
        "max_sharpe": 3.0,
        "max_dd_pct": -1.0,
        "zero_trades_hint": "thresholds",
        "high_sharpe_hint": "lookahead",
    },
    "rsi_2_10_90": {
        "min_trades": 50,
        "max_trades": 500,
        "max_sharpe": 3.0,
        "max_dd_pct": -1.0,
        "zero_trades_hint": "dedup key collision",
        "high_sharpe_hint": "lookahead",
    },
    "morning_star": {
        "min_trades": 1,
        "max_trades": None,
        "max_sharpe": 3.0,
        "max_dd_pct": None,
        "zero_trades_hint": "ATR scale factors too strict",
        "high_sharpe_hint": None,
    },
}

# A DISCLOSED, investigated exception to one specific bound -- never a silent
# bounds-table change and never a verbal-only override. Adding an entry here
# is a deliberate, human-approved decision, made only after the deviation has
# been independently investigated (see docs/explanations/stage-3/ for the
# fuller narrative); this dict is what makes that investigation durable and
# visible on every future run instead of living only in a conversation. Keyed
# (strategy_name, bound_key) so it applies to exactly the one bound it was
# investigated for -- an accepted min_trades deviation does not silently
# excuse a future max_sharpe violation on the same strategy.
#
# Every reason string must be dated and must name the independent
# verification method used, in enough detail that someone could redo it
# without re-deriving the approach from scratch.
KNOWN_DEVIATIONS: dict[tuple[str, str], str] = {
    ("rsi_14_30_70", "min_trades"): (
        "2026-08-13: num_trades=12 on real AAPL 2015-01-01..2024-12-31, below "
        "the literature-consistent floor of 20. Independently verified correct, "
        "not a pipeline bug, via a standalone plain-Python simulation run "
        "entirely outside backtesting.py and this codebase's evaluator: "
        "compute rsi = pandas_ta.rsi(close, length=14) on the same real AAPL "
        "series, then walk it bar by bar with a hand-written long-only, "
        "single-position state machine (enter only if not already in a "
        "position and RSI crosses below 30; exit only if in a position and "
        "RSI crosses above 70). That independent simulation reproduces "
        "num_trades=12 exactly. Explanation: RSI(14) genuinely crosses below "
        "30 seventy-one separate times over the decade, but most of those "
        "dips occur in clusters while a position from an earlier dip is "
        "already open (waiting for the eventual cross above 70 to exit), so "
        "they don't generate new entries -- only 12 real flat-to-long "
        "transitions occur. AAPL's unusually persistent 2015-2024 uptrend "
        "(corroborated by sma_10_30_crossover's +304% and rsi_2_10_90's +62% "
        "returns over the same window) produced fewer oversold-then-recovered "
        "cycles than the 20-200 bound -- drawn from broader multi-asset, "
        "multi-decade literature -- anticipates for one ticker's one decade."
    ),
}

# If disclosed, individually-legitimate deviations start accumulating, that
# accumulation is itself a signal -- the bounds table's assumptions (or the
# choice of ticker/window) need reconsideration, not more entries in this
# dict. With 4 strategies x up to 3 checkable bounds each (12 possible
# violation slots total), 3 is a deliberately early trigger: meaningfully
# before "concerning," not a warning that only fires once things are already
# bad. Crossing it blocks a clean gate outright, regardless of how well any
# individual deviation is investigated.
MAX_DEVIATIONS_BEFORE_REVIEW = 3


def check_one(
    name: str, rule: StrategyRule, bounds: dict, data: pd.DataFrame
) -> tuple[list[tuple[str, str]], BacktestResult]:
    """Compile and run one KNOWN_STRATEGIES entry against real data.

    Returns (problems, result), where each problem is (bound_key, message).
    bound_key identifies WHICH bound was violated ("min_trades", "max_trades",
    "max_sharpe", "max_dd_pct") so main() can reconcile it against
    KNOWN_DEVIATIONS -- a plain message string alone couldn't be matched
    reliably against a specific, pre-approved exception.

    Never raises for an out-of-bounds VALUE -- those are collected as
    problems. Only a genuine pipeline failure (compile error, backtest
    raising) is allowed to propagate, since that's a different class of
    failure than "the numbers are outside the literature-consistent range"
    and should halt the script immediately rather than being reported as
    just another bound violation.
    """
    strategy_cls = make_rule_strategy(rule)
    result = run_backtest(data, strategy_cls, ticker=TICKER)

    problems: list[tuple[str, str]] = []

    if result.num_trades < bounds["min_trades"]:
        hint = bounds["zero_trades_hint"] if result.num_trades == 0 else "check bounds/thresholds"
        problems.append(
            ("min_trades", f"num_trades={result.num_trades} < min {bounds['min_trades']} (check: {hint})")
        )
    if bounds["max_trades"] is not None and result.num_trades > bounds["max_trades"]:
        problems.append(("max_trades", f"num_trades={result.num_trades} > max {bounds['max_trades']}"))

    if result.sharpe_ratio >= bounds["max_sharpe"]:
        problems.append(
            (
                "max_sharpe",
                f"sharpe_ratio={result.sharpe_ratio:.3f} >= {bounds['max_sharpe']} "
                f"(check: {bounds['high_sharpe_hint']})",
            )
        )

    if bounds["max_dd_pct"] is not None and not (result.max_drawdown_pct < bounds["max_dd_pct"]):
        problems.append(
            (
                "max_dd_pct",
                f"max_drawdown_pct={result.max_drawdown_pct:.3f}% not < {bounds['max_dd_pct']}% "
                f"(suspiciously small drawdown for {START}-{END} real data)",
            )
        )

    return problems, result


def main() -> None:
    session = SessionFactory()
    try:
        data = load_price_data(TICKER, session, start=START, end=END)
    finally:
        session.close()

    print(f"Loaded {len(data)} real {TICKER} bars, {data.index[0].date()} -> {data.index[-1].date()}\n")

    all_passed = True
    total_accepted_deviations = 0

    for name in sorted(KNOWN_STRATEGIES):
        rule = KNOWN_STRATEGIES[name]
        bounds = BOUNDS[name]
        problems, result = check_one(name, rule, bounds, data)

        unexplained: list[str] = []
        accepted: list[tuple[str, str]] = []
        for bound_key, message in problems:
            deviation_reason = KNOWN_DEVIATIONS.get((name, bound_key))
            if deviation_reason is not None:
                accepted.append((message, deviation_reason))
            else:
                unexplained.append(message)

        passed = len(unexplained) == 0
        all_passed &= passed
        total_accepted_deviations += len(accepted)

        if unexplained:
            status = "FAIL"
        elif accepted:
            status = "PASS (disclosed deviation)"
        else:
            status = "PASS"
        print(f"[{status}] {name}")
        print(
            f"  num_trades={result.num_trades}  sharpe={result.sharpe_ratio:.3f}  "
            f"max_dd={result.max_drawdown_pct:.3f}%  win_rate={result.win_rate_pct:.1f}%  "
            f"return={result.total_return_pct:.2f}%"
        )
        for message in unexplained:
            print(f"    - FAIL: {message}")
        for message, reason in accepted:
            print(f"    - DISCLOSED DEVIATION: {message}")
            print(f"        {reason}")
        print()

    print("=" * 60)
    if total_accepted_deviations >= MAX_DEVIATIONS_BEFORE_REVIEW:
        print(
            f"BLOCKED: {total_accepted_deviations} disclosed deviations >= review "
            f"threshold ({MAX_DEVIATIONS_BEFORE_REVIEW}). This many accepted exceptions "
            f"is itself a signal that the bounds table's assumptions need "
            f"reconsideration -- not that the exception mechanism is working as "
            f"intended. The gate cannot report a clean pass until this is reviewed."
        )
        all_passed = False
    elif all_passed and total_accepted_deviations:
        print(
            f"Stage 3 gate: PASSED, with {total_accepted_deviations} disclosed and "
            f"investigated deviation(s) -- see above."
        )
    elif all_passed:
        print("Stage 3 gate: PASSED -- all 4 strategies within literature-consistent bounds.")
    else:
        print("Stage 3 gate: FAILED -- see problems above.")
    sys.exit(0 if all_passed else 1)


if __name__ == "__main__":
    main()
