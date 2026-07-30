# Commit log — Stage 3

Lightweight notes after each commit — what changed, why, anything non-obvious.
Entries before this file existed (Stage 1, Stage 2, Stage 3 Component 1 & 2, the
post-commit hook tooling commit) live in the old flat `docs/explanations/commit-log.md`.

---

## docs: split commit-log.md into per-stage folders going forward

**Change:** Updated CLAUDE.md and the `explanation-writer` skill so Level-1 commit
notes append to `docs/explanations/stage-N/commit-log.md` instead of one flat
top-level file, matching how step/stage explainer files already organize per-stage.

What is non-obvious: this is a going-forward-only split — history through Stage 3
Component 2 stays in the old flat file untouched, by explicit choice, rather than
retroactively moved. Step explainer naming (`step-NN-name.md`) is unchanged; only
the commit-log path convention changed. This very commit's hook run confirmed the
automatic (non-manual) post-commit trigger genuinely fires on a real `git commit` —
it correctly skipped writing this entry itself because `CLAUDE_CODE_OAUTH_TOKEN`
wasn't set in that shell, which is why this entry is written interactively instead.

---

## Stage 3 component 3: strategy rule schema

**Change:** Added `schema.py` — seven `Term` kinds as a discriminated union
(`IndicatorTerm`, `PriceTerm`, `ConstantTerm`, `BodyTerm`, `MidpointTerm`,
`RangeTerm`, `ScaledTerm`), `Comparison`, a recursive `Condition` and/or/leaf tree,
`StrategyRule`, and four `KNOWN_STRATEGIES` (SMA crossover, RSI 14/30-70, RSI
2/10-90, morning star).

What is non-obvious: (1) offset validation here is Sacred Gate 1 extended one
layer up — a rule can never even be constructed with a positive (future-peeking)
offset, checked again later at evaluation time as defense in depth. (2) `ScaledTerm`
bans nesting (factors would just multiply — zero added expressiveness, real added
ambiguity for future rule dedup). (3) `and`/`or` require >= 2 children, not just
"not empty" — a single-child branch is identical to its child, a pointless
construct in the same spirit as rejecting bbands' dead `std` param. (4) Morning
star is the actual reason `BodyTerm`/`MidpointTerm`/`RangeTerm`/`ScaledTerm` exist
at all — the other three strategies only need `IndicatorTerm`+`ConstantTerm`.
Verification: all 4 KNOWN_STRATEGIES construct; all 13 planned ValidationError
cases raise correctly; 2 should-succeed edge cases (partial params, incomplete
cross-check) succeed; 16/16 existing tests still green. No formal test_schema.py
yet — that's Component 8.
