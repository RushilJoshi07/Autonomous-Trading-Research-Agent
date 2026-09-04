# Frontend Redesign Specification — "Read It, Don't Decode It"

## Why this document exists

Stage 7's components (1-6) are functionally correct and already committed — real data,
real polling, real trace drill-down, all verified live. This document does not change
any backend logic. It changes how existing, real data gets *presented*. Nothing here
requires new backend endpoints beyond the translation layer described in Section 4.

## The one governing rule

**No raw internal field names, ever, in the default view. No card-stack layout,
anywhere. Every screen leads with a real sentence a person reads, backed by a small
number of labeled, plain-language numbers. Full raw evidence stays available, but
collapsed by default, for anyone who wants to verify it by hand.**

If a screen's primary content is JSON keys, tool names, or internal variable names
(`window_index`, `null_std_sharpe`, `observed_num_trades`), that screen fails this
spec, regardless of how it's styled.

---

## Section 1 — Page-by-page walkthrough

### 1.1 Dashboard (landing page)

**Problem today:** empty widgets (portfolio value stuck at zero, no watchlist) because
those features were never built as part of this project's actual scope — the product
is a research agent, not a brokerage.

**Fix:** stop pretending to be a trading dashboard. Lead with what the product
actually does: show real research activity.

- A single headline summary, in a sentence: *"3 hypotheses confirmed, 5 rejected,
  2 currently under investigation."* — computed from real `GET /hypotheses` status
  counts, not a placeholder.
- Below it, the 3 most recent verdicts, each as one line of real plain-English
  summary (see Section 4), not a card — e.g. *"Rejected — momentum on tech names
  showed no edge beyond chance (Sept 2)."* Click through to the full research page.
- Remove any UI element that implies real-time portfolio tracking, live prices, or
  a watchlist — none of that exists in this project's actual backend scope. If
  desired later, that's new, explicitly-scoped backend work, not a frontend fix.

### 1.2 Ask a research question (charter flow)

**Status: keep as-is.** This flow (mandate textarea → plain-language interpretation
→ correction round-trip → confirm) already meets this spec's standard — it takes
something messy (a sentence) and returns something clear (a confirmed plan), in
real language, with no internal jargon exposed. No changes needed here.

### 1.3 Research page (the primary rework)

This is the page that currently shows raw `tool_call_trace` rows as stacked cards
(`window 0`, `test_significance`, `null_std_sharpe`, etc.). Replace entirely with
three stacked *sections*, not cards:

**Section A — The verdict (top of page, largest text on the screen)**

- Status word: **Confirmed** / **Rejected** / **Inconclusive** — large, colored per
  the existing Fathom verdict-status palette (already built, already
  colorblind-checked — reuse it, don't reinvent it).
- 2-3 sentences of real prose beneath it, generated from the verdict's own
  `narrative` field (this already exists — `render_verdict` produces it; the gap is
  that today's UI doesn't surface it as the primary content).
- If `narrative` is thin or absent for a given verdict, that's the one place new
  backend work may be needed — flag it, don't fabricate prose client-side to fill
  the gap.

**Section B — Key numbers, translated (a labeled row/grid, not a card)**

Show 3-5 translated metrics per ticker/window tested, using Section 4's translation
table. No raw field names visible anywhere in this section.

**Section C — "View the evidence" (collapsed by default)**

- A single toggle, closed on page load.
- When expanded: the existing trace timeline exactly as already built (Component 6's
  work is correct and stays untouched) — this is the receipt, not the headline.
- Visually distinct from Sections A/B when expanded (e.g. monospace, muted
  background) so it reads as "raw record" rather than blending into the main content.

### 1.4 Scoreboard (Component 7, not yet built)

Build it to this spec from the start — do not build a raw table first and translate
later.

- Three sections: Confirmed, Under investigation, Decayed (the last will be empty
  until Stage 8's re-check job exists — show it honestly empty, not hidden, per this
  project's existing disclosure pattern for unbuilt features).
- Each entry: one plain-language line (rule name + one-line why-it-matters), not a
  raw status enum. Click through to that hypothesis's full research page.

---

## Section 2 — Layout and visual language

- **No card grids.** Replace with vertical prose sections and labeled data rows —
  the visual language of a research report, not a dashboard of widgets.
- **Typographic hierarchy carries the meaning:** verdict sentence largest and boldest
  on the page; translated numbers secondary; raw evidence smallest/most muted,
  visually clearly a different *kind* of content.
- **Reuse Fathom's existing design tokens** (color, type, spacing) — this is a
  content/structure change, not a new design system. The verdict-status palette,
  typography choices, and dark/light theming already built in Component 3 stay
  exactly as they are.

---

## Section 3 — What does NOT change

- Backend logic, all six MCP tools, the agentic core, Sacred Gate 2 — untouched.
- The charter creation/correction flow (Section 1.2) — untouched.
- Trace drill-down's actual data and broken-reference handling (Component 6) —
  untouched, just relocated behind a collapsed toggle instead of being the default
  view.
- The status-polling mechanism (Component 5) — untouched, still drives the
  "awaiting verdict" / "run failed" states, just feeding into the new verdict-first
  layout instead of a card stack.

---

## Section 4 — Translation table (raw field → plain language)

This is the concrete spec for the new presentation layer. Every row is a real field
this project's backend already returns.

| Raw field | Plain-language treatment |
|---|---|
| `sharpe_ratio` (e.g. `0.51`) | "Annualized risk-adjusted return: 0.51" *or*, softer: "Modest positive return, above zero but not exceptional." |
| `p_value` (e.g. `0.12`) | "Low confidence this is a real edge — a random strategy would produce a result this good about 12% of the time by chance." Threshold language: p < 0.05 → "statistically reliable"; p ≥ 0.05 → "not statistically reliable / plausibly chance." |
| `num_trades` / `observed_num_trades` | "Based on 224 trades" — plain count, labeled, no other change needed. |
| `win_rate_pct` | "Won 48.7% of trades" — direct translation, add context only if useful ("roughly a coin flip"). |
| `total_return_pct` / `annual_return_pct` | "+456% total return (13.8% annually) over the tested period" — combine both into one readable sentence rather than two separate raw fields. |
| `max_drawdown_pct` | "Worst peak-to-trough loss: 37.6%" — always phrase as a real risk statement, never a bare negative number. |
| `commission_pct` | Usually omit from the primary view entirely (it's an audit detail, not a user-facing metric) — available in the expanded evidence section only. |
| `null_mean_sharpe` / `null_std_sharpe` / `n_resamples` | Never shown in the primary view under these names. If shown at all, folded into the p-value's plain-language sentence ("compared against 300 random simulations"). Otherwise: evidence-section only. |
| `window_index` / `window N` labels | Replace with plain temporal language: "In-sample period (2010–2019)" / "Out-of-sample test (2019–2023)" rather than "window 0" / "window 1." |
| `ticker` (e.g. `NVDA`) | Fine as-is — already plain. Optionally pair with the real company name if available. |
| `grounding_tier` (`local_corpus` / `whitelist_search` / `none`) | "Grounded in [paper title]" / "Grounded in external research" / "No supporting literature found — held to a stricter evidence bar" — never show the raw enum value. |

---

## Section 5 — What to hand Claude Code, concretely

Build in this order:
1. The translation layer (Section 4) — a shared utility, not duplicated per-page,
   since the scoreboard (1.4) will need it too.
2. Research page rework (1.3) — the primary deliverable.
3. Dashboard rework (1.1).
4. Scoreboard (Component 7), built to this spec from the start.

Each should go through the same explain-before-write, verify-live discipline as
every other component in this project — no exception for "just a UI change."
