"""Stage 4 gate: call each tool manually through MCP before any agent
touches them (docs/architecture.md's own literal Stage 4 criterion).

Launches src/mcp_tools/server.py as a real subprocess over stdio and drives
it with the actual MCP client SDK (mcp.ClientSession, mcp.stdio_client) --
NOT the in-process _handle_call_tool() shortcut used for every interactive
check throughout this stage's build (Components 1-8). That shortcut proved
each tool's own logic correctly, repeatedly, but it never actually
exercised the real subprocess-plus-stdio transport a real MCP client --
Stage 5's LangGraph client, eventually -- will use. This script is the
first and only place in Stage 4 that does, which is what makes it the
actual gate rather than one more instance of the same interactive check
already performed nine times.

For each of the tools registered on the server, checks a happy-path call
AND at least one invalid-input or edge case, confirming MCP surfaces a
clean, structured result either way. Two of the nine tools (list_indicators,
correct_p_values) take no ticker/date arguments with an obvious invalid
form, so only their happy path is checked here -- consistent with how
Components 4 and 6 scoped their own interactive verification.

Run: .venv/bin/python scripts/verify_stage4_gate.py
Exit code 0 = every check passed. 1 = at least one failed.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys

from mcp import ClientSession, StdioServerParameters, stdio_client

from backtester.schema import SMA_CROSSOVER

TICKER = "AAPL"

_EXPECTED_TOOLS = {
    "get_price_data",
    "run_backtest",
    "compute_indicator",
    "list_indicators",
    "classify_regime",
    "test_significance",
    "confidence_interval",
    "correct_p_values",
    "screen_universe",
}

_results: list[tuple[str, bool, str]] = []


def record(name: str, passed: bool, detail: str = "") -> None:
    _results.append((name, passed, detail))
    status = "PASS" if passed else "FAIL"
    print(f"[{status}] {name}" + (f"  -- {detail}" if detail else ""))


async def check_get_price_data(session: ClientSession) -> None:
    ok = await session.call_tool(
        "get_price_data", {"ticker": TICKER, "start": "2024-01-01", "end": "2024-01-10"}
    )
    rows = ok.structured_content.get("result", []) if not ok.is_error else []
    record("get_price_data happy path", not ok.is_error and len(rows) > 0, f"is_error={ok.is_error}, rows={len(rows)}")

    bad = await session.call_tool("get_price_data", {"ticker": "NOTAREALTICKER"})
    record("get_price_data invalid ticker -> is_error", bad.is_error)


async def check_run_backtest(session: ClientSession) -> None:
    rule = json.loads(SMA_CROSSOVER.model_dump_json())
    ok = await session.call_tool(
        "run_backtest", {"rule": rule, "ticker": TICKER, "start": "2015-01-01", "end": "2024-12-31"}
    )
    num_trades = ok.structured_content.get("num_trades", 0) if not ok.is_error else 0
    record("run_backtest happy path", not ok.is_error and num_trades > 0, f"is_error={ok.is_error}, num_trades={num_trades}")

    bad_rule = json.loads(SMA_CROSSOVER.model_dump_json())
    bad_rule["entry"]["comparison"]["left"]["name"] = "NOTAREALINDICATOR"
    bad = await session.call_tool("run_backtest", {"rule": bad_rule, "ticker": TICKER})
    record("run_backtest malformed rule -> is_error", bad.is_error)


async def check_indicators(session: ClientSession) -> None:
    ok = await session.call_tool(
        "compute_indicator",
        {"ticker": TICKER, "name": "SMA", "params": {"length": 10}, "start": "2024-01-01", "end": "2024-02-15"},
    )
    rows = ok.structured_content.get("result", []) if not ok.is_error else []
    record("compute_indicator happy path", not ok.is_error and len(rows) > 0, f"is_error={ok.is_error}, rows={len(rows)}")

    bad = await session.call_tool("compute_indicator", {"ticker": TICKER, "name": "NOTREALINDICATOR"})
    record("compute_indicator unknown indicator -> is_error", bad.is_error)

    listed = await session.call_tool("list_indicators", {})
    entries = listed.structured_content.get("result", []) if not listed.is_error else []
    record("list_indicators happy path", not listed.is_error and len(entries) > 100, f"is_error={listed.is_error}, count={len(entries)}")


async def check_classify_regime(session: ClientSession) -> None:
    ok = await session.call_tool("classify_regime", {"ticker": TICKER, "start": "2024-01-01", "end": "2024-01-10"})
    rows = ok.structured_content.get("result", []) if not ok.is_error else []
    real_labels = all(r["trend_regime"] != "insufficient_history" for r in rows)
    record(
        "classify_regime happy path (2024 request against a decade-plus history -> real labels)",
        not ok.is_error and len(rows) > 0 and real_labels,
        f"is_error={ok.is_error}",
    )

    short = await session.call_tool("classify_regime", {"ticker": TICKER, "end": "2010-02-01"})
    short_rows = short.structured_content.get("result", []) if not short.is_error else []
    starts_insufficient = len(short_rows) > 0 and short_rows[0]["trend_regime"] == "insufficient_history"
    record(
        "classify_regime short-history edge case -> explicit insufficient_history, not an error",
        not short.is_error and starts_insufficient,
        f"is_error={short.is_error}",
    )


async def check_statistics(session: ClientSession) -> None:
    rule = json.loads(SMA_CROSSOVER.model_dump_json())

    sig = await session.call_tool(
        "test_significance",
        {"rule": rule, "ticker": TICKER, "start": "2015-01-01", "end": "2024-12-31", "n_resamples": 30, "seed": 0},
    )
    p_value = sig.structured_content.get("p_value", -1) if not sig.is_error else -1
    record("test_significance happy path", not sig.is_error and 0.0 <= p_value <= 1.0, f"is_error={sig.is_error}, p_value={p_value}")

    impossible_rule = {
        "name": "impossible",
        "description": "RSI can never cross below -100 -- structurally 0 trades",
        "entry": {
            "kind": "leaf",
            "comparison": {
                "left": {"kind": "indicator", "name": "RSI", "params": {"length": 14}},
                "op": "crosses_below",
                "right": {"kind": "constant", "value": -100},
            },
        },
        "exit_after_bars": 5,
    }
    bad_sig = await session.call_tool(
        "test_significance", {"rule": impossible_rule, "ticker": TICKER, "start": "2024-01-01", "end": "2024-06-01"}
    )
    record("test_significance zero real trades -> is_error", bad_sig.is_error)

    ci = await session.call_tool(
        "confidence_interval", {"rule": rule, "ticker": TICKER, "start": "2015-01-01", "end": "2024-12-31"}
    )
    low = ci.structured_content.get("low", 1) if not ci.is_error else 1
    high = ci.structured_content.get("high", 0) if not ci.is_error else 0
    record("confidence_interval happy path", not ci.is_error and low < high, f"is_error={ci.is_error}")

    bad_ci = await session.call_tool(
        "confidence_interval", {"rule": rule, "ticker": TICKER, "start": "2024-01-01", "end": "2024-06-01"}
    )
    record("confidence_interval too few trades -> is_error", bad_ci.is_error)

    cp = await session.call_tool("correct_p_values", {"p_values": [0.001, 0.01, 0.03, 0.04, 0.049, 0.2, 0.5]})
    adjusted = cp.structured_content.get("adjusted_p_values", []) if not cp.is_error else []
    record("correct_p_values happy path", not cp.is_error and len(adjusted) == 7, f"is_error={cp.is_error}")


async def check_screen_universe(session: ClientSession) -> None:
    ok = await session.call_tool("screen_universe", {"sector": "Technology", "metric": "liquidity"})
    group_size = ok.structured_content.get("group_size", 0) if not ok.is_error else 0
    record("screen_universe happy path", not ok.is_error and group_size > 0, f"is_error={ok.is_error}, group_size={group_size}")

    empty = await session.call_tool("screen_universe", {"sector": "NotARealSector"})
    empty_ok = (
        not empty.is_error
        and empty.structured_content.get("group_size") == 0
        and empty.structured_content.get("candidates") == []
    )
    record("screen_universe empty group -> valid empty result, not an error", empty_ok, f"is_error={empty.is_error}")


async def main() -> None:
    params = StdioServerParameters(
        command=os.path.abspath(".venv/bin/python3"),
        args=["-m", "mcp_tools.server"],
        cwd=os.getcwd(),
    )

    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            tool_list = await session.list_tools()
            found = {t.name for t in tool_list.tools}
            record(
                "all 9 tool functions registered on the server",
                found == _EXPECTED_TOOLS,
                f"missing={_EXPECTED_TOOLS - found or None}, unexpected={found - _EXPECTED_TOOLS or None}",
            )

            await check_get_price_data(session)
            await check_run_backtest(session)
            await check_indicators(session)
            await check_classify_regime(session)
            await check_statistics(session)
            await check_screen_universe(session)

    print()
    print("=" * 70)
    passed = sum(1 for _, ok, _ in _results if ok)
    total = len(_results)
    print(f"{passed}/{total} checks passed")
    if passed == total:
        print(
            "Stage 4 gate: PASSED -- every tool called manually through real MCP "
            "(subprocess + stdio + real client session), happy path and invalid "
            "input alike."
        )
    else:
        print("Stage 4 gate: FAILED")
        for name, ok, detail in _results:
            if not ok:
                print(f"  FAILED: {name} ({detail})")
    sys.exit(0 if passed == total else 1)


if __name__ == "__main__":
    asyncio.run(main())
