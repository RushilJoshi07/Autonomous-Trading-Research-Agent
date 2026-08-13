# Architecture — Autonomous Trading Strategy Research Agent

> This document is the single source of truth for what we are building and why.
> Decisions here were reached deliberately. Do not silently deviate from them.
> If a decision looks wrong, raise it with me rather than quietly changing course.

---

## Contents

| # | Section | What's in it |
|---|---|---|
| 1 | What this is | The product, the load-bearing test, the pain solved |
| 2 | What the agent is — and is not | Scope boundaries, refusal rules, agent vs tools |
| 3 | Governing principles | The three rules everything else follows from |
| 4 | The two sacred gates | Non-negotiable verification requirements |
| 5 | **The user journey** | **All 9 workflow steps, front end + back end** |
| 6 | The data layer | Sources, refresh, corporate actions, survivorship |
| 7 | The stack | Every technology and why |
| 8 | Cost discipline | Budget, routing, the real risks |
| 9 | **Build order** | **All 8 stages with verification criteria** |
| 10 | Where the hard engineering lives | The actual difficulty |
| 11 | Defensible summary | Interview phrasing |

---

## At a glance

### The 9-step workflow (detail in section 5)

| Step | What happens | Agentic? |
|---|---|---|
| 1 | She sets a research mandate → parsed to a validated charter → **she confirms** | LLM parses; human approves |
| 2 | Agent generates hypotheses (tiered grounding), pre-registers falsification | Yes |
| 3 | Agent designs the study (splits, walk-forward, mandatory control) | Yes |
| 4 | Execution loop: decide next action → call tool via MCP → write to state → repeat | Yes — this is the core |
| 5 | Verdict synthesised; every claim validated against tool output | Yes, with code enforcement |
| 6 | Scoreboard of living beliefs; scheduled decay re-testing | Scheduled |
| 7 | She interrogates a verdict or redirects the charter | Yes |
| 8 | Autonomous scheduled runs on new data | Scheduled |
| 9 | Golden-set evaluation and drift detection | Continuous |

### The 8 build stages (detail in section 9)

| Stage | Deliverable | Gate | LLM? |
|---|---|---|---|
| 1 | Data pipeline (yfinance → Postgres, cache, retry) | Data matches source; graceful failure | No |
| 2 | Backtesting engine | **SACRED GATE 1** — no lookahead; costs change outcomes | No |
| 3 | Strategy schema + 2–3 known strategies | Literature-consistent results | Build-time only |
| 4 | Tools wrapped as MCP servers | Each callable manually before any agent use | No |
| 5 | Agentic core (LangGraph, loop, RAG, guardrails) | **SACRED GATE 2** — never fabricates; kills bad hypotheses | Yes |
| 6 | Evaluation harness | Catches a deliberately-broken agent | Yes |
| 7 | Frontend | A stranger can use it unexplained | — |
| 8 | Deploy + monitor | Traced in production; drift alerts fire | — |

**Stages 1–3 involve no LLM in the runtime path** — months of that work ran at zero
API cost. Stage 3 makes one narrow, disclosed exception: a single offline,
build-time LLM call proposes parameter bounds for extended indicators, and every
proposal is verified by actual execution before it can be used (detail: §7, §9
Stage 3). No stage before 5 depends on an LLM being reachable at runtime — the
agent that reasons doesn't exist until then.
**End of Stage 3 = a complete working backtesting product.**

---

## 1. What this is

An **autonomous research program for trading strategies**. An agent that generates
its own hypotheses, designs experiments, runs them rigorously, kills the ones that
fail, and maintains a living scoreboard of what works and what has decayed.

The user sets a research direction and reads findings. She does not drive the tool.

### The load-bearing test
Delete the agent and there is **no product** — no hypotheses (it generated them),
no studies (it designed them), no verdicts (it reached them), no scoreboard (it
maintains it). What remains is a backtesting library with no user-facing product.

This test was the reason an earlier design was rejected: in that version, deleting
the agent left a fully functional backtesting app, which proved the agent was a
feature rather than the product.

### The pain it solves
Finding a strategy is easy. Running a *continuous research program* — generating
candidates, testing them honestly, and re-checking whether last year's beliefs still
hold — is relentless work almost nobody does. People test one idea, fall in love
with it, and don't notice when it decays.

---

## 2. What the agent is — and is not

**It reasons about evidence. It never forecasts.**

**In scope** (past/present tense — questions about what *has been* true):
- Does strategy X work on ticker/universe Y?
- Why did X stop working?
- Compare X on A vs B.
- Is this edge real or overfit?
- Has my working strategy decayed?
- What regimes does X depend on?

**Out of scope — the agent must refuse and redirect** (future tense):
- Will this stock go up?
- Should I buy?
- Is this a good investment?
- What happens next / how much will I make?

**The refusal to predict is a headline feature, not an apology.** A system users
trust for honesty cannot also be a fortune-teller. The guardrail against prediction
is as important as the guardrail against fabrication.

Correct refusal behaviour: decline the forecast, explain that nothing reliably
predicts prices, and offer the honest historical evidence instead.

### Two modes, honestly labelled
- **Research mode** — the agentic core and the primary product. IN SCOPE for v1.
- **Scanning mode** — "find stocks forming this pattern now" is a deterministic
  screener query, i.e. a *tool*, not an agent. DEFERRED out of v1.
  Only *conditional* scanning ("find setups, but only where this strategy has
  historically worked") is genuinely agentic.

Do not inflate a deterministic feature into an "agent". Knowing which parts of the
product do NOT need an agent is a maturity signal.

### Exactly one agent; everything else is deterministic
There is exactly **one agent** — the research agent. It is the only component that
reasons, plans, or decides.

The backtester, indicators, statistics, screener, and regime classifier are
**deterministic Python functions**: identical inputs always produce identical
outputs. They perform no inference and make no choices.

This separation is what makes fabrication preventable. Every number in a verdict
traces to a recorded tool output, so any claim can be checked against the value the
tool actually returned. If the LLM performed the computation, there would be no
independent record to check against.

The agent decides which computations to run; the tools perform them.

---

## 3. Governing principles

### Vagueness stops at the human boundary
The user's English is fuzzy. Everything past her **confirmed charter** is typed,
validated, and deterministic.

The LLM is permitted to be fuzzy at exactly two moments:
1. Translating her sentence into a structured charter (which she then confirms).
2. Writing prose around numbers that are already locked to tool outputs.

Everywhere else: schemas, validators, and deterministic code.

### Model proposes, code disposes
The LLM never executes anything. It emits an *intention* (a structured tool call);
orchestration code executes it and writes the result into state. The LLM never
touches data, never runs a backtest, never computes a statistic.

If the LLM is ever making a quantitative decision by "judgment", that is a bug.

### Rigor throughout
Correctness over cleverness, always. A brilliant agent calling a sloppy backtester
produces confident nonsense.

---

## 4. The two sacred gates

These are never weakened, never worked around, never deferred.

**Gate 1 (Stage 2): Prove the backtester has no lookahead bias.**
Deliberately attempt to introduce lookahead and confirm the engine prevents it.
Confirm transaction costs change outcomes.

**Gate 2 (Stage 5): Prove the agent never fabricates AND that it kills hypotheses
when the evidence says to.**
The second half is the harder problem — LLMs are agreeable and want to confirm.

---

## 5. The user journey (front end + back end)

### Step 1 — She sets the mandate

**Front end:** She types a research direction, e.g. *"Investigate mean-reversion on
liquid tech names, daily. Prefer robustness over raw returns."* Then she's done.

**Back end:**
- FastAPI endpoint receives it, calls the LLM with a schema requesting **structured
  JSON** (not prose) → a **charter**: universe description, hypothesis families,
  timeframe, scoring preferences.
- **Pydantic validates immediately.** Malformed/hallucinated fields → reject, retry.
  Nothing unvalidated proceeds.
- The **screener tool** resolves "liquid tech names" into an actual ticker list.
- Charter persisted to Postgres, shown to her, and **she confirms it**. That
  confirmation flag is what allows the agent to start. Human-in-the-loop by design —
  her chance to catch a misparse before hours are wasted.

#### Screener mechanics (settled decisions)
- **Metadata filters** are lookups (sector = Technology, from stored yfinance metadata).
- **Computed filters** require calculation (liquidity = average daily dollar volume
  over a lookback window, computed from cached price data).
- **Thresholds are RELATIVE, never hand-picked.** Not "volatility below 20%" but
  "lowest volatility quintile within the sector."
  Rationale: self-calibrating across regimes, and much harder to gerrymander after
  seeing results. A hand-picked number can be quietly retuned until the backtest
  looks good — that is overfitting hidden in the universe definition.
- **Sensitivity testing is required**: run at quintile, tercile, decile. If a finding
  survives only one cut, it is an artifact of the threshold, not an edge.
- **Universe selection must be point-in-time.** Screening on "past year's volatility"
  using today's data and then backtesting from 2015 uses future information to select
  the universe. Same lookahead discipline as the backtester, one layer up — easy to miss.

### Step 2 — The agent generates hypotheses

**Front end:** Her research log fills with entries she did not ask for.
Status: *proposed*.

**Back end:** LangGraph enters the `propose_hypothesis` node.

#### Grounding — tiered fallback (settled design)
1. **Local corpus** — 30–50 curated open-access papers, auto-ingested via the arXiv
   API, chunked, embedded, stored in a vector DB (pgvector or Chroma).
2. **Whitelist search** — SSRN, NBER, Federal Reserve working papers, arXiv.
   NOT general web search. NOT Wikipedia (definitions only; no empirical evidence
   about whether strategies work). Quantpedia is useful as a *pointer* to papers,
   not as a source.
3. **Ungrounded** — the LLM proposes from its own knowledge, flagged
   `grounding: none`.

**Escalation is MECHANICAL**: retrieval relevance below a threshold. Never a
subjective LLM judgment about whether a paper is "ambiguous."

**Tier 3 must remain genuinely reachable.** A fallback chain that always finds
*something* would make everything look grounded and defeat the purpose.

#### What retrieval is actually for
It does **not** look for a paper about the exact question — no paper says "RSI(2)
below 10 on liquid tech names." It finds the **effect family** (e.g. short-horizon
reversal), and the agent instantiates a specific testable rule from it.

There are only a few dozen effect families (momentum, mean-reversion, low-volatility,
value, quality, seasonality, …). ~40 good papers cover most of them. This is why
"there aren't papers for everything" dissolves as a concern.

#### Output
A structured hypothesis object:
- the **rule** (in the validated strategy schema),
- the **prediction**,
- the **falsification condition — WRITTEN BEFORE TESTING**,
- **rationale with citations** + the grounding tier used.

Pre-registering falsification is a deliberate anti-hallucination design: the agent
cannot retroactively invent a story that fits whatever came out.

**Pydantic validates that the rule is executable** — real indicator, valid
parameters, well-formed exit condition. A malformed rule never reaches the backtester.

**Deduplication** against the research log prevents re-proposing killed hypotheses.

### Step 3 — It designs the study

**Front end:** She expands a hypothesis and sees the plan: in-sample window,
out-of-sample window, walk-forward parameters, control definition, null hypothesis.

**Back end:** The LLM generates a structured **study design** object.

This **cannot be hardcoded** — different hypotheses need different experiments:
- a regime-dependence claim needs data split by regime,
- a decay claim needs rolling walk-forward,
- a cross-sectional claim tests the universe together.

**The control is MANDATORY.** The question is never "did this make money" but
"did it beat randomized entries at the same trade frequency." That comparison is
what separates edge from volatility, and it is what retail tools skip.

**Code enforces the data split, not the prompt.** Out-of-sample data is structurally
withheld during the in-sample phase. Do not ask the model nicely not to peek — make
peeking impossible.

### Step 4 — The execution loop (the heart)

**Front end:** Status flips to *testing*, with a live trace of tool calls and results.

**Back end:** LangGraph holds a **state object** (charter, hypothesis, design, all
results so far, steps taken) and cycles between two nodes:

- `decide_next_action` — the LLM sees the **entire current state** and returns a
  structured action: which tool, what arguments, or "conclude".
- `execute_tool` — invokes it **via MCP**, writes the result into state.

Loop. **Each decision is conditioned on the last result.** In-sample 1.34 → run
out-of-sample. Out-of-sample 0.21 → investigate why → call the regime classifier.

**Nobody wrote that decision tree. The agent generates the path. That branching is
the agency.**

#### The tools (all yours, all deterministic)
- **backtester** — no-lookahead, cost-aware, realistic fills
- **market data** — reads the Postgres cache
- **indicators** — pandas-ta
- **regime classifier** — labels periods (trending/choppy, high/low vol)
- **statistics** — scipy: significance tests, confidence intervals,
  multiple-testing corrections
- **screener** — universe resolution

#### MCP
Each tool is an **MCP server** written with the Python SDK. The LLM receives their
descriptions and emits a tool-call intention; **your code executes it. The LLM never
runs anything.**

MCP is a Linux Foundation standard adopted across providers, so the tool layer is
**portable by construction**. Switching model providers leaves tools untouched; only
orchestration glue changes.

#### Loop guardrails
- The LLM can only choose from tools that actually exist.
- A **step/budget limit** stops runaway loops (also serves as cost control).

#### Async, not delayed
The study starts **immediately** and runs as a background job so the browser isn't
held for minutes. She watches progress and can close the tab.
This is NOT the overnight scheduling — that is a different workload (see Step 8).

Every tool call traced (inputs, outputs, latency, cost).

### Step 5 — The verdict

**Front end:** e.g. *rejected*, with reasons, numbers, and explicit caveats.

**Back end — three things; the third is the hard one.**

1. The LLM synthesizes a structured verdict where **every quantitative claim carries
   a reference to the tool call that produced it.** A validator walks each claim and
   checks the number against state. **A claim with no valid reference is REJECTED.**
   This is Gate 2 implemented in code, not hoped for in a prompt.

2. The **pre-registered falsification condition is applied mechanically.** The
   results either clear the bar or they don't. No retroactive storytelling.

3. **The agent must kill its own hypotheses.** LLMs are agreeable — left alone, one
   looking at Sharpe 0.21 writes "shows modest promise" instead of "this is dead."
   Fight that with: pre-registration, **deterministic decision rules** (your code
   reads p=0.31 and fails it — not the LLM), and adversarial evaluation.

#### Multiple-comparisons defense (the biggest threat to the honesty claim)
The agent generates hypotheses itself, so it can test enough of them that one passes
by chance. Therefore:
- **Track total hypotheses tested under a charter** and correct the significance
  threshold accordingly.
- **Grounding functions as a prior**: an ungrounded hypothesis is closer to random
  search, so it faces a **stricter** bar. This turns a thin corpus into a
  disciplining feature rather than a weakness.
- **Disclose the count** in the verdict: "this is hypothesis 34 under this charter;
  the threshold was adjusted accordingly."

### Step 6 — The scoreboard

**Front end:** three sections — under test; currently believed to work (with the
conditions under which they hold); and **previously believed, now decayed** (with
the date noticed and the evidence).

**Back end:** Confirmed strategies are stored as **living beliefs** — rule,
validating conditions, evidence, last-verified timestamp.

A scheduled job re-runs them against **newly arrived data** that did not exist when
the belief was formed — genuinely out-of-sample, impossible to have overfit.

Degradation past a threshold wakes the agent with a specific task: investigate and
re-verdict. It either explains the change or demotes the strategy to *decayed*.

Every claim expandable into its trace. Nothing asserted without a receipt.

### Step 7 — She interrogates and redirects

**Front end:** Chat attached to a **specific verdict** — not a floating assistant.

**Back end:** two request types:
- **Questions about existing findings** → read from stored state and traces.
  Cheap, fast, no new computation.
- **Requests for new work** ("re-test under 2020 only") → spawn a **new study**
  through Steps 3–5.
- **Redirections** amend the charter, changing what Step 2 generates.

She is commissioning a researcher, not chatting with a bot.

### Step 8 — It keeps running

**Front end:** She returns later to new verdicts and decay notifications.

**Back end:** a scheduler — nightly data ingestion, periodic belief re-verification,
new hypothesis generation within mandate capacity.

#### Compute model split (cost-critical)
- **Interactive work** (her requests) responds on demand.
- **Autonomous overnight work** has **no latency requirement** — nobody is waiting —
  so it runs as **scheduled jobs on serverless/batch compute**, billed only for the
  minutes it executes.

Paying for 24-hour readiness to do 30 minutes of work is the most common source of
silent cloud spend.

**LLMOps becomes real here**, not decorative: an autonomous system spending money
and making claims a user relies on must be monitored — cost per study, latency,
failure rates, and above all **quality drift**.

### Step 9 — Evaluation and drift detection

**The golden set** — hypotheses with known correct verdicts:
- **Planted false** — strategies independently confirmed to be garbage.
  The agent **must kill these**. Confirming one is a failure.
- **Planted true** — known robust edges. It must confirm these (with caveats).
  Killing them means it is uselessly over-skeptical.
- **Known-caveat** — small-sample cases where the correct answer is
  "insufficient evidence."

Each run scores: verdict correctness, whether every claim traced to a tool output
(fabrication check), and whether required caveats appeared.

**Run on every agent change AND continuously in production.** Prompt tweaks, model
updates, or subtle bugs can make it start confirming things it used to kill. When
planted-false hypotheses start passing, **an alert fires** — that is drift detection,
and it closes the loop with Step 8.

This is the single strongest differentiator of the project.

---

## 6. The data layer

**Source of truth:** yfinance → Postgres cache. **The agent reads the database,
never the network.**

Rationale:
- **Speed** — 47 tickers × network round-trips is unusable.
- **Reliability** — yfinance is unofficial and flaky (hence retry/fallback).
- **Reproducibility** — the same study run twice must use identical data, or the
  science is worthless.

### Refresh rhythms
- **Daily bars**: nightly after close, appending the delta.
- **Metadata** (sector, industry, listing status): monthly.
- **Universe membership**: changes a few times a year at index rebalances.

### Corporate actions (why append-only silently breaks)
Splits and dividends **retroactively change adjusted prices**. An append-only store
drifts out of sync with reality, producing fake single-day crashes the strategy
"trades" and destroying reproducibility.

Handling:
- Store **both raw and adjusted** prices. Raw never changes; adjusted does.
- Check the splits/dividends feed **weekly**; re-fetch only affected tickers.
- **Full re-fetch monthly** as a safety net.
- **Log every change** — otherwise a study giving different numbers in June is a
  mystery.

### Intraday limits (hard constraint)
Free intraday history is severely limited (roughly ~7 days of 1-minute, ~60 days of
5–60 minute bars). **A rigorous multi-year intraday study is not possible on free data.**

- **v1 is daily-only**, documented as a scope boundary.
- Weekly/monthly bars are **resampled from daily** — never store what you can compute.
- If intraday is added later, it is a rolling window, and the agent must flag loudly
  that a 60-day sample cannot support strong conclusions.

### Survivorship bias (the hardest data problem)
Today's index contains companies that **made it**. Bankrupt and delisted names are
invisible, so results are inflated — and **the bias does not appear anywhere in the
output**. Energy is especially brutal (two extinction events since 2014).

**The fix:** reconstruct **point-in-time membership** by walking backwards from
today's constituent list through documented historical additions/removals, snapshotting
membership at each change point.

**It will not be fully solved on free data:**
- Delisted tickers often have no free price history.
- Ticker symbols get **reused**, so key on ticker **plus date range**.

**Therefore: measure the gap and disclose it.** e.g. "This study covers 87% of the
point-in-time universe; missing names skew toward failures, so true performance is
likely worse than shown."

An agent that discloses bias in its own evidence is a stronger signal than a clean
dataset. Anyone can buy CRSP.

**Scope warning:** full point-in-time reconstruction is a project on its own. Build
Stage 1 with today's universe and a *clearly documented* limitation, get the pipeline
and backtester working end to end, then add reconstruction as distinct work.

---

## 7. The stack

- **Backend:** Python, FastAPI
- **Orchestration:** LangGraph
- **Reasoning:** Claude, behind a thin `llm_client` abstraction pointing at
  **Bedrock** (AWS credits) or the direct Anthropic API
- **Validation:** Pydantic everywhere
- **Tools:** MCP servers (Python SDK)
- **Data:** yfinance → Postgres; pgvector/Chroma for RAG
- **Backtesting:** built on backtesting.py or vectorbt — **never from scratch**
- **Indicators:** pandas-ta
- **Statistics:** scipy
- **Async:** Celery/RQ
- **Tracing:** LangSmith
- **Eval:** golden set + Ragas/TruLens
- **Frontend:** React
- **Deploy:** Docker, AWS

### Provider abstraction
All LLM access goes through one module (`llm_client`). Underneath it can point at
Bedrock or the direct Anthropic API. This makes the provider decision a one-line
change rather than a rewrite.

MCP being vendor-neutral means the **tool layer is unaffected** by provider choice.

---

## 8. Cost discipline

AWS provides $200 in credits ($100 signup + $100 for activities including Bedrock).
Bedrock serves the **same Claude models** — no capability downgrade to save money.

- Per study: roughly $0.20–0.35 on a mid-tier model; less on the cheapest tier.
- **Prompt caching** cuts cached input substantially — ideal for the agent loop,
  where the system prompt and charter repeat every iteration.
- **Batch API** halves cost — perfect for overnight autonomous runs.
- **Route by stakes**: cheapest model for `decide_next_action`; stronger model for
  hypothesis generation and verdict synthesis.

### The real risks are not tokens
- **Credit expiry** — check the terms when credits land.
- **Idle infrastructure** — a server left running bills 24/7 whether used or not.
  This is how student credits actually evaporate.

Mitigations: set a budget alert on day one; **stay local through Stage 7**; deploy
small and late; tear down anything not in use.

### Free where quality doesn't matter
- **Mock the LLM entirely** when testing orchestration (deterministic, fast, free).
- **Ollama locally** for exercising the loop.
- **Paid API only where reasoning quality IS the product** — hypothesis generation,
  verdict synthesis, and evaluation runs.

Stages 1–3 involve **no LLM in the runtime path** — months of that work ran at
zero API cost. The one exception, Stage 3's extended-indicator bounds proposal
(§7, §9 Stage 3), is a single offline build-time call verified by execution
before use — not a recurring runtime cost, and not a precedent for adding one.

---

## 9. Build order

Bottom-up. **Each stage complete and verified before the next.** Never hold a broken
half-thing. If the timeline stretches, stop at a stage boundary with something whole.

**Stage 1 — Data pipeline.** yfinance → Postgres, caching, retry/fallback.
*Verify:* data matches a known source; caching works; failures degrade gracefully.

**Stage 2 — Backtesting engine.** Build on backtesting.py/vectorbt.
*Verify (SACRED GATE 1):* deliberately attempt lookahead and confirm the engine
prevents it; confirm transaction costs change outcomes.

**Stage 3 — Strategy schema + known strategies.** The rule format, plus 2–3
documented strategies. *Verify:* literature-consistent results.
**→ At this point there is a complete working backtesting product.**

**Stage 4 — Tools via MCP.** Wrap each tool as an MCP server.
*Verify:* call each manually through MCP before any agent touches them.

**Stage 5 — The agentic core.** LangGraph, the loop, tiered RAG, guardrails.
*Verify (SACRED GATE 2):* prove it never fabricates AND that it kills hypotheses
when the evidence says to.

**Stage 6 — Evaluation harness.** Golden set with planted false/true/caveat cases.
*Verify:* it catches a deliberately-broken agent.

**Stage 7 — Frontend.** Research log, scoreboard, expandable traces.
*Verify:* a stranger can use it with no explanation.

**Stage 8 — Deploy + monitor.** Docker, AWS, tracing, drift alerts.

---

## 10. Where the hard engineering actually lives

Not in the backtester — that is built on an existing library.

**It is in making an LLM behave like a skeptical scientist rather than an agreeable
storyteller.** That is the unsolved problem, and it is the part worth building.

---

## 11. Defensible summary (for interviews)

- **"RAG retrieves; agents act."** A RAG bot answers questions whose answers already
  exist in a corpus. This agent answers questions whose answers do not exist yet —
  it plans, computes, and conditions each step on the last.
- **"Papers ground the proposal; computation determines the verdict."** Literature
  says what is worth suspecting; the backtest is the blood test. If they disagree,
  the data wins.
- **"The refusal to predict is a feature."** A system trusted for honesty cannot be
  a fortune-teller.
- **"I evaluate against a golden set with planted false hypotheses the agent must
  reject, run continuously to catch drift."**
