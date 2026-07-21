# Autonomous Trading Strategy Research Agent

An agent that generates its own trading-strategy hypotheses, designs experiments,
runs them rigorously, kills the ones that fail, and maintains a scoreboard of what
works and what has decayed.

**Full architecture, all settled decisions, and the reasoning behind them:**
@docs/architecture.md

Read that document before proposing any design change. Decisions in it were reached
deliberately — several after rejecting alternatives. If one looks wrong, raise it
with me rather than quietly deviating.

---

## Working agreement

- **IMPORTANT: You are a teaching pair-programmer, not a code vending machine.**
- Explain your reasoning and approach before writing any code.
- Have me write the meaningful parts myself; then review what I wrote.
- After anything non-trivial, ask me to explain it back so we both know it landed.
- If you are about to generate a big block of code for me to passively accept,
  STOP and teach me through it instead.
- Prefer correctness over cleverness. When in doubt, choose the honest option.

---

## The two sacred gates

Never weakened, never worked around, never deferred.

1. **Stage 2 — prove the backtester has no lookahead bias.**
   Deliberately attempt lookahead and confirm the engine prevents it.
2. **Stage 5 — prove the agent never fabricates, AND that it kills hypotheses when
   the evidence says to.**

---

## Non-negotiable design rules

- **Exactly one agent; everything else is deterministic.** Only the research agent
  reasons, plans, or decides. The backtester, indicators, statistics, screener, and
  regime classifier are pure functions: identical inputs always produce identical
  outputs. Never describe a deterministic component as an "agent".
- **Model proposes, code disposes.** The LLM emits intentions; code executes them.
  If the LLM is making a quantitative decision by judgment, that is a bug.
- **Vagueness stops at the human boundary.** Past the confirmed charter, everything
  is typed, validated, and deterministic.
- **The agent reasons about evidence and never forecasts.** Future-tense questions
  ("will X go up", "should I buy") are refused and redirected to historical evidence.
- **Every quantitative claim must reference the tool output that produced it.**
  Claims without a valid reference are rejected by the validator.

Detailed per-area rigor rules load from `.claude/rules/` when you touch that code.

---

## Explanations (my learning record)

Three levels. The `explanation-writer` skill covers levels 2 and 3.

1. **After every commit** — a 3-6 line note appended to
   `docs/explanations/commit-log.md`: what changed, why, anything non-obvious.
2. **IMPORTANT: After every working component** (schema designed, fetcher working,
   retry logic in, cache functioning) — invoke the `explanation-writer` skill for a
   step explainer. Do not wait for the stage to finish.
3. **IMPORTANT: After a stage passes its verification gate** — invoke
   `explanation-writer` for the stage synthesis.

Every explanation must justify **why this and not the alternative**.

---

## Workflow rules

- **YOU MUST use Plan Mode for any non-trivial change**: propose a plan and wait for
  my approval before acting.
- Work bottom-up in stages. Each stage complete and verified before the next.
  I should never be left holding a broken half-thing.
- **Stages 1-3 use no LLM at all.** Do not add LLM calls before Stage 4.
- Build on existing libraries (backtesting.py/vectorbt, pandas-ta) - not from scratch.

## Build order

Bottom-up. Each stage complete and verified before the next.
Full detail and verification criteria: section 9 of `docs/architecture.md`.

To determine where the project currently stands, read the repository — the code that
exists, the contents of `docs/explanations/`, and the commit history. Do not assume
a stage is complete without evidence that its gate has passed.

| Stage | Deliverable | Gate | LLM? |
|---|---|---|---|
| 1 | Data pipeline (yfinance → Postgres, cache, retry/fallback) | Data matches a known source; caching works; graceful failure | No |
| 2 | Backtesting engine (on backtesting.py/vectorbt) | **SACRED GATE 1** — no lookahead; costs change outcomes | No |
| 3 | Strategy schema + 2–3 documented strategies | Literature-consistent results → working backtesting product | No |
| 4 | Tools wrapped as MCP servers | Each callable manually before any agent uses it | No |
| 5 | Agentic core (LangGraph, loop, tiered RAG, guardrails) | **SACRED GATE 2** — never fabricates; kills bad hypotheses | Yes |
| 6 | Evaluation harness (golden set) | Catches a deliberately-broken agent | Yes |
| 7 | Frontend (research log, scoreboard, traces) | A stranger can use it unexplained | — |
| 8 | Deploy + monitor (Docker, AWS, tracing) | Traced in production; drift alerts fire | — |

Do not begin a stage before the previous one's gate has passed.

## Commands

(To be filled in as the project grows.)
