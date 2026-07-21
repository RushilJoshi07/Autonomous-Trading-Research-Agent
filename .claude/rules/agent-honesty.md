# Rigor rules — the agentic core

Applies to: LangGraph orchestration, LLM calls, hypothesis generation, verdicts.

## SACRED GATE 2
Prove the agent never fabricates, AND that it kills hypotheses when the evidence
says to. The second half is the harder problem.

## Model proposes, code disposes
The LLM emits intentions (structured tool calls); code executes them. The LLM never
touches data, never runs a backtest, never computes a statistic.

If the LLM is making a quantitative decision by "judgment", that is a bug.

## Every LLM output is validated before use
Pydantic validates every structured output. Malformed or hallucinated content is
rejected and retried. Nothing unvalidated proceeds to the next step.

Rule validation specifically must confirm the rule is EXECUTABLE (real indicator,
valid parameters, well-formed exit condition) so a malformed rule never reaches the
backtester.

## Falsification is pre-registered
The hypothesis states what would prove it wrong BEFORE any testing. This is a
deliberate anti-hallucination design: the agent cannot retroactively invent a story
that fits whatever came out.

The falsification criterion is applied MECHANICALLY by code, not by LLM judgment.

## Every quantitative claim carries a tool reference
The verdict is a structured object where each claim references the tool call that
produced it. A validator walks every claim and checks the number against state.
A claim with no valid reference is REJECTED.

This is Gate 2 implemented in code, not hoped for in a prompt.

## The agent must be willing to kill its own hypotheses
LLMs are agreeable. Left alone, one looking at Sharpe 0.21 writes "shows modest
promise" instead of "this is dead".

Defenses: pre-registration, DETERMINISTIC decision rules (code reads p=0.31 and
fails it — not the LLM), and adversarial evaluation.

## Multiple comparisons
The agent generates hypotheses itself, so it can test enough that one passes by
chance. This is the biggest threat to the honesty claim.

- Track total hypotheses tested under a charter; correct the significance threshold.
- Grounding functions as a PRIOR: an ungrounded hypothesis is closer to random
  search, so it faces a STRICTER bar.
- Disclose the count in the verdict.

## Grounding tiers
1. Local curated corpus (arXiv auto-ingest)
2. Whitelist search (SSRN, NBER, Fed working papers, arXiv) — NOT general web search
3. Ungrounded, flagged `grounding: none`

Escalation is MECHANICAL (retrieval relevance below threshold), never a subjective
LLM judgment. Tier 3 must remain genuinely reachable — a chain that always finds
something would make everything look grounded and defeat the purpose.

RAG informs WHAT TO TEST, never what is true. Verdicts come from computation.

## Never forecast
The agent answers questions about what has been or is true. It does not predict
future prices, returns, or outcomes.

Future-tense requests ("will X go up", "should I buy", "is this a good investment",
"what happens next") are refused. The correct response declines the forecast, states
that future prices are not reliably predictable, and offers the relevant historical
evidence instead.

This refusal is a designed feature, not a limitation to apologise for.

## Loop guardrails
- The LLM can only choose tools that actually exist.
- A step/budget limit stops runaway loops (also serves as cost control).
- Out-of-sample data is STRUCTURALLY WITHHELD during the in-sample phase. Do not ask
  the model nicely not to peek — make peeking impossible.
