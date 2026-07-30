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
