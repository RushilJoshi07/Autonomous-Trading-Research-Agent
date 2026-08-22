# Stage 5, Component 4 — Hypothesis generation

## 1. What this component does

This is the first component where an LLM call's output feeds directly into
a persisted, structured object that later components will act on — Stage
5's `propose_hypothesis` node from `docs/architecture.md` Step 2. Given a
confirmed charter and one effect family from that charter's own list, it
grounds a query (Component 3), asks the LLM for a testable rule, prediction,
pre-registered falsification condition, and rationale, and persists the
result as a new `Hypothesis` row — with citations and grounding tier
assembled by code, never by the LLM.

**What this explicitly does not do:** it doesn't run a backtest, doesn't
touch a tool beyond `ground_topic`, doesn't decide which family to
investigate (that's an explicit caller-supplied parameter), and doesn't
retry a duplicate or malformed result automatically — all deliberate scope
boundaries, covered in section 3.

## 2. Every meaningful line explained

### Reusing Stage 3's validation instead of rebuilding it

`ParsedHypothesis.rule: StrategyRule` uses Stage 3's schema directly. This
single line is doing more work than it looks like: `StrategyRule`,
`Condition`, and `IndicatorTerm` all carry their own `model_validator`s
(confirmed by reading `backtester/schema.py` directly before writing
anything) — `IndicatorTerm._check_indicator` rejects an unknown indicator
name or an out-of-range parameter against the real 222-indicator registry;
`Condition._check_shape` rejects a malformed leaf/and/or tree;
`StrategyRule._check_exit` requires a real exit condition. Because
`structured_output` calls `response_model.model_validate(...)` on whatever
the LLM emits, and `ParsedHypothesis` nests `StrategyRule` inside it, all of
these fire automatically the moment the outer object validates.
`docs/architecture.md`'s "Pydantic confirms the rule is executable" isn't
a requirement this component implements — it's a requirement Stage 3
already satisfied, that this component gets for free by composition.

### `FalsificationCondition`

```python
metric: Literal["sharpe_ratio", "annual_return_pct", "total_return_pct",
                 "max_drawdown_pct", "win_rate_pct", "p_value"]
```

Every one of these six names was checked directly against
`backtester/result.py`'s `BacktestResult` and `research_stats/
significance.py`'s `SignificanceResult` before being written — not
remembered or guessed. This is what lets Component 7 later evaluate a
falsification condition mechanically: `getattr(actual_result, condition.
metric)` will never `AttributeError`, because the vocabulary is drawn from
the real field names those two classes actually have.

### `propose_hypothesis`'s two guardrails

```python
if not charter_row.confirmed:
    raise ValueError(...)
...
if family not in charter.parsed.hypothesis_families:
    raise ValueError(...)
```

Both checked directly against the database, both proven to fire for real
against real charter rows (section 5) — not just written and assumed
correct. The first enforces `docs/architecture.md` Step 1's "that
confirmation flag is what allows the agent to start" as code, not
convention: nothing can call this function against an unconfirmed charter
and get a real hypothesis out. The second keeps the charter as the actual
boundary of what's being investigated — generating a hypothesis for a
family she never asked about would go beyond what was approved.

### The dedup hash

```python
def _rule_hash(rule: StrategyRule) -> str:
    canonical = json.dumps(rule.model_dump(mode="json"), sort_keys=True)
    return hashlib.sha256(canonical.encode()).hexdigest()
```

`sort_keys=True` matters even though Pydantic's own field ordering is
already deterministic — it's a second, independent guarantee against key
ordering ever mattering to the hash, cheap enough that there's no reason
not to have both. Compared against every existing hypothesis's `rule`
JSONB under the same `charter_id`, recomputed on the fly rather than stored
in its own column — deliberately, since Component 1 left `dedup_key` off
`hypotheses` specifically so this component could make that call once it
actually had a design (see step-01's own note on this), and at this scale
(tens of hypotheses per charter, not millions) recomputing on every call is
not a real cost.

## 3. Design decisions and rejected alternatives

### Family as an explicit parameter, not an LLM or function guess

`propose_hypothesis(charter_id: str, family: EffectFamily)` takes the
family to investigate as a caller-supplied argument. The alternative —
having the LLM (or the function itself) pick which of the charter's
possibly-several families to explore this time — was rejected for the same
reason Component 2 kept the universe cut a closed vocabulary rather than a
free-floating number: a decision that can be made deterministically and
auditably should be, rather than adding LLM freedom where none is needed.
Nothing about "which family to investigate next" requires translating fuzzy
human language into structure — that translation already happened once, at
charter-parsing time.

### Fixed per-family query templates, not an LLM-generated search query

Same reasoning, one level lower: `_FAMILY_QUERY_TEMPLATES` is a plain dict,
not a prompt asking the LLM to write a good search query. The alternative
would reopen the "vagueness stops at the human boundary" line a second time
in the same component for no real benefit — a fixed, sensible phrase per
family is simple, deterministic, and auditable in a way an LLM-generated
query string, chosen fresh every call, would not be.

### Code-derived `citations` and `grounding_tier`, never LLM-authored

The third application of the same boundary this project keeps drawing in
the same place: `Charter.resolved_universe` (Component 2), `Hypothesis.
citations`/`grounding_tier` (this component). The LLM's prompt includes the
grounding chunks' *text*, so it can write an informed rationale — but the
`citations` list attached to the persisted `Hypothesis` is built directly
from the same `GroundingChunk` objects `ground_topic` already returned, not
from the LLM re-stating what it read. The alternative — asking the LLM to
also emit its own citation list (title, author, source) — was never
seriously considered, for the same reason `resolved_universe` was never
going to be LLM-authored: it reopens exactly the hallucination surface the
whole grounding mechanism exists to close. `StrategyRule.literature_source`
(a Stage 3 field, already free-text) stays LLM-authored — it's a low-stakes
human-readable label, not the verifiable grounding trail.

### Exact-hash dedup, not fuzzy similarity — a lesson applied, not just a default

This choice was made with a specific, recent memory: Component 3's
`LOCAL_RELEVANCE_THRESHOLD` had to be raised from an untested `0.5` to a
deliberately conservative `0.90` after a real adversarial test found a
false positive that a smaller, less-tested threshold couldn't have caught.
Introducing a *second* similarity threshold here — "how similar is similar
enough to count as a duplicate hypothesis" — would mean repeating that same
exposure with even less real data to calibrate against (Component 3 at
least had three confirmed-good matches to compare a bad one to; there's no
equivalent corpus of "confirmed genuinely-different hypotheses" to check a
similarity cutoff against here). Exact structural hashing has no threshold
to get wrong — two rules either produce the same canonical JSON or they
don't. It only catches literal repeats, not near-duplicates with a
different indicator length or a slightly different threshold — a real,
named limitation (section 5), not a hidden one, and one to revisit from a
genuine observed case of near-duplicate proposals, the same "expand from a
real miss" principle already applied to the Tavily domain list.

### Raise on collision, not retry — because nobody is watching

Component 2's charter also has a "this didn't work, now what" moment, and
resolved it with human-mediated retry: she's sitting there, so re-running
the script with clearer wording is the natural, honest mechanism.
`propose_hypothesis` runs unattended — nothing in this component's own
scope is a human watching in real time. Building automatic retry-with-
feedback into this function would mean deciding, inside a function whose
job is "produce one hypothesis," questions that actually belong to
whatever orchestrates repeated calls to it (how many attempts, what backoff,
whether to try a different family instead) — none of which exists yet.
Raising a specific, catchable `DuplicateHypothesisError` keeps this
function's own contract simple and pushes that decision to the layer that
should own it.

## 4. Concepts introduced

**Composition as a validation strategy.** Rather than writing a new
"is this rule executable" check, this component nests an already-validated
schema (`StrategyRule`) inside a new one (`ParsedHypothesis`) and lets
Pydantic's own recursive validation do the rest. This is a general pattern
worth naming: when an existing schema already enforces exactly the
invariant a new component needs, reusing it *as a field type* — not
reimplementing its checks, not even calling a separate "validate this"
function — gets the guarantee for free and keeps it in exactly one place
if it ever needs to change.

**Pre-registration as a structural property, not just a described one.**
`FalsificationCondition` is produced by the *same* LLM call, in the *same*
response object, as the rule and prediction it's meant to falsify — there
is no code path where a hypothesis exists without one, and no later step
that could plausibly write one after seeing results, because nothing after
this component ever constructs a new `FalsificationCondition` at all. The
anti-hallucination property architecture.md describes in prose is enforced
here by there simply being no opportunity to do otherwise.

## 5. How this component was verified

A real hypothesis was generated end-to-end against a real, previously-
confirmed charter (from Component 2's own testing: `sector='Technology',
industry='Consumer Electronics'`, `resolved_universe=['AAPL']`,
`hypothesis_families=['low_volatility']`) — a real Bedrock call, not
mocked. The result: a structurally valid `StrategyRule` using three real
indicators (`NATR`, `SMA`, `EMA`), a `FalsificationCondition` referencing a
real metric (`sharpe_ratio`), and `grounding_tier='whitelist_search'` with
5 real citations, each with a real, whitelist-domain URL (confirmed by
querying the persisted row directly, not trusting the in-memory object) —
two arXiv papers on the low-volatility anomaly, "Betting Against (Bad)
Beta," and, notably, the exact real NBER working paper (`w16601`) for
Frazzini & Pedersen's *Betting Against Beta* — the same paper already
sitting in the local corpus, found again independently via live search
because Component 3's now-conservative `0.90` threshold pushed this query
past Tier 1 into Tier 2. This is the concrete, expected cost of that
earlier decision showing up for the first time in a downstream component,
not a new problem — Tavily still found the right paper.

The dedup mechanism was proven in both directions, not just described: the
persisted hypothesis's rule, re-fetched from the database and re-hashed,
was confirmed present in the same `existing_hashes` set
`propose_hypothesis` itself computes — proving a real repeat would be
caught — and a deliberately modified copy of the same rule (`exit_after_
bars` changed) was confirmed *not* present — proving the check isn't
trivially always-true. Both guardrails were exercised against real rows:
requesting `seasonality` against a charter whose `hypothesis_families` is
only `["low_volatility"]` raised correctly; calling against a real,
genuinely unconfirmed charter (`045a5f8c-...`, from earlier Component 2
testing) raised correctly. The full test suite (`pytest -q`, 221 tests)
stayed green throughout.

**What this does not prove.** The `raise DuplicateHypothesisError(...)`
line itself was never triggered end-to-end through a real `propose_
hypothesis` call — LLM non-determinism makes a natural collision unlikely
to hit on demand, and forcing one would mean mocking the LLM response,
which this component's testing deliberately avoided in favor of real calls
throughout. What *was* proven, in isolation, are the two pieces that line
depends on (`_rule_hash`'s correctness in both directions, and the
`existing_hashes` query genuinely reflecting what's persisted) — enough to
trust the mechanism, not enough to claim the exact line was exercised.
Only one real hypothesis has been generated so far, for one family, against
one small (1-ticker) universe — nothing yet tests a charter with multiple
resolved tickers, a `grounding_tier='none'` case (no real query has yet
missed both tiers during this component's own testing), or what happens
when the LLM's `StrategyRule` genuinely fails Pydantic validation (an
invalid indicator name, say) — that failure path exists by construction
(section 2) but hasn't been observed firing on a real response.

## 6. Interview defense

**Q: Why didn't you write your own check that a proposed rule is
executable, the way you might expect a new component to validate its own
inputs?**

A: Because one already exists, and duplicating it would create two places
that could quietly drift out of agreement about what "executable" means.
`StrategyRule`'s own validators are the actual, load-bearing check — this
component's only job was to put `StrategyRule` where the LLM's output would
have to pass through it, which is a one-line type annotation
(`rule: StrategyRule`), not a new validation function.

**Q (hard): Your dedup check only catches an exact structural repeat. A
real research agent proposing many hypotheses over time is much more likely
to produce something 95% similar to an earlier rejected idea — slightly
different parameters, same underlying logic — than a byte-identical repeat.
Doesn't that make this dedup check nearly useless in practice?**

A: It's genuinely narrow, and that's a real, stated limitation, not a
hidden one — but "narrow" isn't the same as "useless." It catches the
specific, real failure mode of the agent literally re-proposing the same
rule it already tried (which can happen — nothing stops two separate calls
from landing on the same well-known construction, like an SMA crossover, if
nothing prevented it), at zero risk of the false-positive problem Component
3 just demonstrated a similarity-based check can have. The honest position
is that near-duplicate detection is a real, likely-eventually-needed
capability this component does not provide — and building it now, with no
real example of the failure it would guard against, risks repeating
Component 3's exact mistake in a new place rather than avoiding it.

**Q: Why does `Hypothesis.citations` store the full retrieved chunk text
rather than just a lightweight pointer (paper ID, title, URL) — isn't that
a lot of redundant text sitting in the database?**

A: Because the actual auditability question Sacred Gate 2 cares about isn't
"what paper was cited" but "does the rationale's claim actually follow from
what was retrieved" — and answering that requires seeing the exact text the
LLM was shown, not just knowing which paper it came from. A pointer alone
would mean re-fetching and re-chunking the source to check a claim later;
storing the chunk verbatim means the check can be made directly against
what's already in the row.

**Honest weaknesses, stated plainly:** dedup only catches literal repeats.
The `DuplicateHypothesisError` raise path itself was never triggered end-
to-end, only its two dependencies proven in isolation. Only one real
hypothesis exists so far — no `grounding_tier='none'` case, no multi-
ticker universe, no genuine LLM-side validation failure has been observed
in this component's own testing.

## 7. What comes next and why

Component 5 (study design) is next — the first component to take a
confirmed `Hypothesis` and produce the in-sample/out-of-sample split and
walk-forward parameters that the execution loop (Component 6) will actually
test against. If this component's `FalsificationCondition` vocabulary is
wrong in some way not yet discovered — a metric name that seemed reasonable
here but doesn't actually match what Component 7's validator needs, say —
the most likely place that surfaces is Component 7 itself, trying to
mechanically evaluate a condition against a real result object and finding
nothing to compare. And if the dedup mechanism's narrowness ever matters in
practice, it will most likely first show up as an unexplained
`DuplicateHypothesisError` on a call that looks, to a human reading the
rule, like it should have succeeded — the concrete trigger for actually
building near-duplicate detection, when and if it happens.
