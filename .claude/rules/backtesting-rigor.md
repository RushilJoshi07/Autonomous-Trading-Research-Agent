# Rigor rules — backtesting engine

Applies to: the backtester, strategy execution, performance metrics.

## SACRED GATE 1
Prove the engine has no lookahead bias. Deliberately attempt to introduce lookahead
and confirm the engine prevents it. Confirm transaction costs change outcomes.

This gate is never weakened, worked around, or deferred.

## No lookahead, structurally
At each bar, entry conditions are evaluated using ONLY data available up to that bar.
Simulate sequentially rather than computing everything vectorized, because vectorized
computation makes it easy to accidentally peek at the future.

## Costs and fills are mandatory
Transaction costs are included. Fills are realistic. A backtest without costs is not
a backtest — it is a fantasy.

## Build on an existing library
Use backtesting.py or vectorbt. Do NOT write a backtesting engine from scratch —
those libraries have already solved the fiddly mechanics correctly.

## The control is mandatory
The question is never "did this make money" but "did it beat randomized entries at
the same trade frequency". That comparison is what separates edge from volatility,
and it is what retail tools skip.

## Determinism
The backtester is a deterministic function. Same inputs always produce the same
outputs. It does not reason, plan, or decide. It is never called an "agent".

This determinism is what makes fabrication preventable — every number in a verdict
must be checkable against a recorded tool output.
