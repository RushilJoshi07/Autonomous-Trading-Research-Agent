---
name: explanation-writer
description: Write teaching explanations of work just completed. Fires at two levels - a step explainer whenever a working component is finished (schema, fetcher, cache, retry logic), and a stage explainer when a full build stage passes its verification gate. Use when a component works, a stage completes, or the user asks for an explanation. Every explanation must justify why each choice was made and why the alternatives were rejected.
---

# Stage explanation writer

## Purpose

These documents are the user's learning record and interview preparation. Write for
a reader who must be able to REBUILD this stage from scratch and DEFEND every line
under questioning, months from now, with no other context available.

The user will read these files to learn. They must teach, not summarize.

## Three levels of explanation

### Level 1 — Commit note (lightweight, frequent)
3-6 lines after each commit: what changed, why, anything non-obvious.
Append to `docs/explanations/commit-log.md`. Do not use this skill for these.

### Level 2 — STEP EXPLAINER (deep, per working component)
**Fires whenever a working component is complete** — the database schema is
designed, the fetcher works, retry logic is in, the cache layer functions, the
corporate-actions handler is done.

A "component" is the smallest piece that works on its own and could be explained
in isolation. If someone could ask "walk me through how you handled X" and X is
this piece, it is a component.

Output: `docs/explanations/stage-N/step-<order>-<component-name>.md`
Example: `docs/explanations/stage-1/step-03-retry-and-fallback.md`

Scope: deep on THIS component only. Full "why this and not the alternative"
treatment. Do not re-explain other components — link to their files instead.

### Level 3 — STAGE EXPLAINER (synthesis, per stage)
Fires when a stage completes and its verification gate passes.

Output: `docs/explanations/stage-N/stage-N-summary.md`

Because step explainers already covered the code line by line, the stage document
is **synthesis, not repetition**:
- how the components fit together and why the boundaries sit where they do
- what the verification gate proved, and what it did NOT prove
- interview defense for the stage as a whole
- what would break downstream if this stage were wrong

Do not re-walk code already covered in step explainers. Reference them.

## Which sections to use

| Section | Step explainer | Stage explainer |
|---|---|---|
| 1. What this does / scope boundaries | yes | yes |
| 2. Every meaningful line explained | yes | no — reference step files |
| 3. Decisions and rejected alternatives | yes | only cross-component ones |
| 4. Concepts introduced | yes | only ones spanning components |
| 5. Verification | how this component was tested | the full gate, exhaustively |
| 6. Interview defense | yes | yes |
| 7. What comes next | brief | yes |

Both levels are self-contained: assume no other context, define terms on first use,
never write "as we discussed" — the reader may be reading this months later with
nothing else available.

## THE CENTRAL REQUIREMENT

Every explanation must answer **"why this and not the other way?"**

Stating what the code does is not enough. For every meaningful decision, the document
must show:

1. What was chosen
2. What the realistic alternatives were
3. **Why each alternative was rejected** — the specific failure it would cause
4. What it would cost to change this later

If a section describes a choice without naming a rejected alternative, it is
incomplete. Go back and add it.

This applies at every level: architectural choices, library choices, function
signatures, data structures, variable scoping, error handling, even test design.

## Required sections

### 1. What this stage does
Plain language. What exists now that did not before. What it is for. What it is NOT
for (scope boundaries).

### 2. Every meaningful line explained
Walk the code. For each meaningful line or block:
- what it does
- **why it is written that way and not another way**
- what would break if it were written differently — be specific about the failure

Do not skip lines that seem obvious. Obvious-to-write is not obvious-to-recall six
months later. Skip only genuine boilerplate, and say explicitly what you skipped.

Where a line exists to prevent a specific bug or bias, say which one.

### 3. Design decisions and rejected alternatives
The heart of the document. For each real decision, in prose not bullets:
- what was chosen
- what else was genuinely considered
- **why the alternative was rejected** — the concrete problem it would have caused
- what it would cost to reverse this decision later
- whether this decision is reversible or load-bearing

Include decisions that were made for cost, scope, or free-data reasons. Those
constraints are defensible in interviews when stated plainly, and indefensible when
hidden.

### 4. Concepts introduced
Any concept the user may not have known before this stage, explained properly rather
than named. Include DOMAIN concepts (lookahead bias, survivorship bias, multiple
comparisons, adjusted vs raw prices, walk-forward testing), not only programming ones.

For each concept: what it is, why it matters here, and what goes wrong when it is
ignored. A concrete example of the failure is worth more than a definition.

### 5. How the verification gate was satisfied
What was tested, how, and what the result proves — and equally, **what it does not
prove**. State the residual risk.

For the two sacred gates (Stage 2 lookahead, Stage 5 fabrication and willingness to
kill hypotheses), be exhaustive. These are the load-bearing claims of the entire
project. If either gate is weakly verified, say so loudly rather than quietly.

### 6. Interview defense
Anticipate what a hiring manager would ask, and give the answer.

Required:
- At least three questions, of which at least one is genuinely HARD — something that
  probes a weakness or an uncomfortable tradeoff, not a softball.
- At least one question of the form "why didn't you just do X instead?"
- The honest weaknesses of this stage, stated plainly with how to answer them.

Naming a limitation is stronger than pretending it does not exist. Show the user how
to say the weakness confidently.

### 7. What comes next and why
How this stage feeds the next. What would break downstream if this stage were wrong,
and how that breakage would show up.

## Style

- Plain language. Explain jargon on first use.
- Prose over bullet fragments for reasoning. Bullets flatten causality, and causality
  is the thing being taught.
- Teach, don't summarize. Recognition is not recall.
- Be honest about weaknesses, shortcuts, and known limitations.
- Never assert something the code does not actually do. If the implementation is
  partial, say which part is missing.
