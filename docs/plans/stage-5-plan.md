# Stage 5 — The Agentic Core (top-level plan)

## Context

Stage 4 closed clean: all six conceptual tools (backtester, market data, indicators,
regime classifier, statistics, screener) exist as 9 registered MCP tool functions on
one stdio server, individually correct (220-test formal suite) and confirmed
reachable through the real MCP protocol (Component 9's manual verification). No LLM
has entered the runtime path anywhere in Stages 1–4 except one disclosed, closed,
build-time exception (Stage 3's extended-indicator bounds proposal).

Stage 5 is the first stage where an LLM runs in the loop at all — the research
agent itself. Per `docs/architecture.md`'s build order, its deliverable is the
agentic core (LangGraph orchestration, the propose→design→execute→synthesize loop,
tiered RAG grounding, loop guardrails) and its gate is **Sacred Gate 2**: prove the
agent never fabricates a claim, and prove it kills a hypothesis when the evidence
says to.

Two provider choices were resolved directly rather than defaulted silently, since
both mean picking up a new paid external account:
- **Tier-2 grounding search → Tavily** (built for agent use, native
  domain-whitelist param, free tier).
- **Tracing → LangSmith, wired up now**, not deferred to Stage 8. Every LangGraph
  node and `llm_client` call gets instrumented from the start so the loop is
  inspectable step-by-step while it's being built, even though Postgres's own
  `tool_call_trace` table is what Sacred Gate 2's validator actually reads
  (LangSmith is a debugging aid layered on top, not a correctness dependency).

Scope is strictly what `docs/architecture.md`'s build-order table assigns to
Stage 5: user-journey Steps 1–5 (charter → hypothesis → study design → execution
loop → verdict), plus the scoreboard's data model (Step 6) since it's just a table
Step 5's verdicts write into. Explicitly out of scope: FastAPI/React (Stage 7),
the scheduled decay-recheck job and autonomous overnight runs (Stage 8), the
golden-set evaluation harness (Stage 6), Celery/RQ async job wrapping (Stage 7).

---

## Architecture decisions

### 1. LangGraph orchestration — state and graph shape

State is a single Pydantic model: `charter`, `hypothesis`, `study_design`,
`tool_call_trace: list[ToolCallRecord]`, `step_count`, `verdict`.

Graph: `propose_hypothesis → design_study → decide_next_action ⇄ execute_tool →
synthesize_verdict → END`. The `decide_next_action ⇄ execute_tool` pair is the
only cycle. A conditional edge routes to `execute_tool` when the LLM's action is a
tool call, or to `synthesize_verdict` when it returns `Conclude` or `step_count`
hits the budget cap (code-enforced, not prompt-requested).

Charter creation/confirmation happens before the graph is invoked — a plain
function call gated on a human "confirmed" flag, not a graph node.

### 2. The loop — what `decide_next_action` returns

A static, closed discriminated union, one Pydantic arm per registered MCP tool
plus a `Conclude` arm. Each arm's fields mirror that tool's real parameters. A
single call to `llm_client.structured_output(prompt, response_model=Action)`.

The prompt is the entire current state, rendered to text — not a growing native
multi-turn tool-calling conversation. Rejected alternative: LangGraph's prebuilt
`ToolNode` + native tool-calling — fights "model proposes, code disposes," since
a prebuilt `ToolNode` auto-executes whatever the model emits.

`execute_tool` takes the validated `Action`, opens the real MCP client connection,
calls the real tool, writes a `ToolCallRecord` into state.

### 3. Tiered RAG grounding — where it lives and how it escalates

Grounding lives under Step 2 (hypothesis generation), not the execution loop —
deterministic code that runs once, automatically, before `propose_hypothesis`.
Escalation is a threshold comparison in Python, never an LLM judgment call.

- Tier 1 — local corpus (30–50 papers, sourced via web search, stored in
  `data/corpus/paper_list.json`, chunked/embedded into pgvector).
- Tier 2 — whitelist search via Tavily, domain-restricted to SSRN, NBER, arXiv,
  Fed working-paper domains.
- Tier 3 — ungrounded, flagged `grounding: none`, facing a stricter downstream
  significance bar.

### 4. Sacred Gate 2 — the three code-level enforcement points, plus the proof

1. Falsification applied mechanically — pre-registered condition, plain Python
   comparison, evaluated the same way regardless of LLM prose.
2. Verdict claim validator — walks every claim, resolves its trace reference,
   re-checks the referenced number. Missing/mismatched reference → rejected.
3. Grounding-tier-adjusted significance — reuses `research_stats` with a stricter
   multiplier for `grounding: none`.

The proof is a dedicated adversarial script, not folded into the mocked test
suite — has to run against the real model to mean anything. Two required cases:
a fabrication attempt (rejected), and a real bad hypothesis killed end-to-end
through the real graph (verdict = rejected, not softened).

---

## Component breakdown

1. Dependencies, schema, tracing scaffolding
2. Charter
3. Tiered RAG grounding
4. Hypothesis generation node
5. Study design node
6. The execution loop
7. Verdict synthesis + validator
8. Scoreboard persistence
9. Formal test suite
10. Sacred Gate 2 verification script

Per the working agreement, each component gets explained before it's written,
then walked through after.

## Verification

- Components 1–8: exercised interactively as they're built.
- Component 9: `pytest tests/agentic_core/` — fast, mocked, free.
- Component 10: manual script run against the real Bedrock-backed agent — this is
  Sacred Gate 2 itself, never weakened, worked around, or deferred.
- Stage-level: after Component 10 passes, invoke `explanation-writer` for the
  Stage 5 synthesis.
