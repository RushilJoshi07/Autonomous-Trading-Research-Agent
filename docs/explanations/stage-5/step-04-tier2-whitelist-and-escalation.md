# Stage 5, Component 3 (part 2) — Tier 2 whitelist search and mechanical escalation

## 1. What this component does

This closes out tiered RAG grounding: Tier 2 (live search restricted to
`ssrn.com`, `papers.ssrn.com`, `nber.org`, `arxiv.org`, `federalreserve.gov`
via Tavily) and `ground_topic`, the single function that mechanically
escalates a query from Tier 1 (the local corpus, step-03) through Tier 2 to
Tier 3 (ungrounded) based on fixed relevance thresholds — no LLM judgment
anywhere in the escalation path, matching `.claude/rules/agent-honesty.md`'s
"Escalation is MECHANICAL... never a subjective LLM judgment."

**What this explicitly does not cover:** this component doesn't call an
LLM either — `ground_topic`'s output is context for a later prompt
(Component 4's job), not something an LLM helps decide. It also doesn't
persist anything of its own; the grounding tier a hypothesis actually used
gets recorded on the `Hypothesis` row itself (`grounding_tier`, already
part of Component 2's schema), not in a separate log this component owns.

## 2. Every meaningful line explained

`GroundingChunk` (`agentic_core/schemas.py`) is a single shape both tiers
return through — `paper_id` only ever set for `local_corpus`, `url` only
ever set for `whitelist_search`, so Component 4 can build a hypothesis's
citations the same way regardless of which tier actually fired, without
branching on which fields might be `None`.

`retrieve_whitelist` calls `TavilyClient(api_key=settings.tavily_api_key)`
— the key passed explicitly rather than relying on `tavily-python`'s own
`os.getenv("TAVILY_API_KEY")` fallback (confirmed to exist by reading
`tavily/tavily.py` directly, same as Component 1's own check). This mirrors
`llm_client`'s `aws_profile` reasoning exactly: the dependency should travel
with the function, not with whichever shell happens to have the right
variable set. `include_domains=_WHITELIST_DOMAINS` is Tavily's own native
parameter, confirmed by inspecting the real installed client's `search`
signature before writing any code against it — not a query-string `site:`
hack, which the original plan explicitly rejected.

`ground_topic` checks only the single top-ranked result in each tier
against that tier's threshold. If the best result isn't relevant enough,
nothing ranked below it will be either — there's no reason to inspect the
rest of either tier's results before deciding whether to escalate.

## 3. Design decisions and rejected alternatives

### Tavily's own `score`, not a second embedding pass

The alternative — embedding Tier 2's live search snippets through the same
local BGE model Tier 1 uses, so both tiers' relevance numbers come from an
identical mechanism — was considered and rejected. Tavily's ranking is
already tuned specifically for "does this document answer this query,"
which is a better-matched tool for live web content than repurposing an
asymmetric retrieval embedding model tuned on a fixed local corpus. The
honest cost of this choice: Tier 1's `relevance` and Tier 2's `score` are
not the same metric wearing two names, stated explicitly in this module's
own docstring rather than left implicit.

### `federalreserve.gov` only, not the 12 regional Fed banks

Raised directly by the user before any code was written, with a claimed
precedent (a New York Fed paper already in the corpus) that turned out not
to exist — checked directly against the real, current `paper_list.json`
and its full git history, neither of which contained any NY Fed entry.
Confirming this rather than assuming it mattered is what kept the domain
list at its originally-designed scope: one canonical domain, expanded only
from a real Tier 2 miss, not a remembered-but-nonexistent one.

### Raising `LOCAL_RELEVANCE_THRESHOLD` from 0.5 to 0.90 after a real adversarial finding

This is the component's central finding, and it came from testing
requested specifically to try to break the mechanism, not from another
confirming case. The original three-query verification (step-03) had every
query hand-picked to have an obvious correct answer. A fourth query,
`"January effect seasonality stock returns calendar anomaly"`, was chosen
specifically because the then-6-paper corpus had zero seasonality content —
the honest, correct behavior was to miss Tier 1 entirely and escalate. It
didn't: it matched a Fama & French *value*-factor chunk at relevance
`0.782`. Direct inspection of that chunk (not just the score) showed why:
the chunk is Fama & French's own real, genuine discussion of the January
effect — citing Roll (1983) and Keim (1983) — as a caveat *inside* a paper
that's fundamentally about book-to-market equity, not a paper primarily
about seasonality. The embedding model wasn't wrong about chunk-level
topical overlap; the paper is still the wrong primary grounding source for
a seasonality hypothesis. `0.782` sits inside the `0.77`–`0.85` range of
that same session's three confirmed-*correct* matches from step-03 — which
is why no single threshold between those two numbers could cleanly separate
this false positive from a true one; the ranges genuinely overlap.

The fix chosen — raising the threshold to `0.90`, well clear of the
overlapping band, rather than searching for some more precise separation
point — was deliberate given the two failure costs are not symmetric: a
false Tier-1 match cites the *wrong paper* as grounding (a real integrity
problem downstream, since Component 4's citations would then rest on
content that doesn't actually support the hypothesis); an unnecessary Tier-2
escalation costs one extra Tavily call (cheap, and — confirmed by re-running
the full three-query suite after the fix — Tavily still finds the *correct*
paper when Tier 1 no longer does, so nothing is actually lost by
escalating). The known, accepted cost of this choice: real, previously-
correct matches scoring `0.77`–`0.89` (the "low beta stocks" → Betting
Against Beta match, at `0.77`, is a confirmed real example) now also
escalate unnecessarily. That's a stated tradeoff, not an oversight, and it's
the direct, mechanical consequence of biasing conservative given the
asymmetric costs — not a side effect nobody noticed.

The threshold is documented in code, not just in this file, specifically so
it doesn't quietly become "just a number" the way the user named LangSmith
and the pgvector gap almost did earlier in this project: the constant's own
comment in `grounding.py` states it's provisional, names the exact
calibration gap (6 papers, only 4 of 6 families represented), and names the
concrete trigger for revisiting it (the already-agreed 30–50 paper
expansion). `WHITELIST_RELEVANCE_THRESHOLD` was deliberately left at its
original `0.5` rather than raised in sympathy — no adversarial test has yet
been run against Tier 2 the way one was against Tier 1, and raising a
threshold with no demonstrated failure behind it would be exactly the kind
of unfounded tightening this project avoids everywhere else. Its own
comment says so explicitly, distinguishing "provisional, untested" from
"provisional, disproven-at-0.5" rather than treating both constants as
equally suspect.

### The regression test targets `retrieve_local` directly, not the full `ground_topic` chain

Testing the full escalation chain would require either mocking Tavily or
making a real API call on every test run — the first adds a fair amount of
scaffolding for a test whose actual job is narrower, the second violates
this project's own "mock what costs money or time, keep the suite fast and
free" habit (`docs/architecture.md` §8: "mock the LLM entirely when testing
orchestration"). The bug was specifically about Tier 1 producing a false
positive; asserting `retrieve_local`'s raw score against
`LOCAL_RELEVANCE_THRESHOLD` directly tests exactly that claim, with no
network dependency, in about half a second.

## 4. Concepts introduced

**Chunk-level relevance vs. paper-level appropriateness.** A retrieval
system can be functioning completely correctly — genuinely finding text
that discusses what was asked about — and still return the wrong grounding
source, because a paper can discuss a topic as an aside without being a
paper *about* that topic. This is a sharper, more specific failure mode
than "the embedding model made a mistake," and it doesn't show up by
checking whether retrieved text mentions the right keywords — the Fama-
French chunk mentions "January," "seasonal," and "January effect"
literally, multiple times. It only shows up by checking whether the
*paper*, not just the *passage*, is the right thing to cite.

**Asymmetric failure costs justify an asymmetric mitigation.** Two ways a
threshold can be wrong aren't inherently equally bad just because they're
both "wrong." Here, a false accept (Tier 1 fires on the wrong paper) and a
false reject (Tier 1 is skipped when it shouldn't be) have genuinely
different downstream consequences — one produces a wrong citation, the
other produces one extra, cheap API call — and once that asymmetry is
identified, the correct response isn't "find the number that minimizes
total errors," it's "bias toward the cheaper failure mode," even at the
cost of accepting more of it.

## 5. How this component was verified

`retrieve_whitelist` was exercised with a real, live Tavily call before any
other code depended on it — confirmed domain-restriction actually worked
(results genuinely came only from `nber.org`/`conference.nber.org` for a
quality-factor query) and confirmed the real result shape (`score`,
`content`, `url`, `title`) before writing `GroundingChunk`-mapping code
against an assumed shape.

The threshold bug was found by a fourth, deliberately adversarial query —
not a fifth confirming one — and the fix was verified three ways: the
regression test (`tests/agentic_core/test_grounding.py`) reproduces the
exact real chunk text and exact real query, asserts the exact real score
(`0.782`) stays below the new threshold, and passes; the full three-tier
demonstration was re-run after the fix, showing all three original queries
now behave correctly (`"mean reversion..."` and the seasonality query both
correctly escalate to Tier 2 and Tavily finds genuinely correct results for
both; the cookie-recipe query still correctly returns `tier="none"`); and
the full pre-existing test suite plus the new test together
(`pytest -q`, 221 tests) passes clean.

**What this does not prove.** `WHITELIST_RELEVANCE_THRESHOLD` has not been
adversarially tested the way `LOCAL_RELEVANCE_THRESHOLD` was — it's
possible Tier 2 has its own false-positive gap that simply hasn't been
looked for yet. The regression test guards the one specific chunk-and-query
pair that broke; it says nothing about whether a *different* paper's
passing mention of an unrelated topic could produce the same failure mode
today, at `0.90`, since no systematic search for other instances was run.
And `ground_topic`'s full three-tier escalation logic itself — as opposed to
each tier's own retrieval function — has only been exercised by the three
demonstration queries in this session, not by an automated test; the formal
suite (Component 9) is where that gap gets closed properly.

## 6. Interview defense

**Q: Why does `WHITELIST_RELEVANCE_THRESHOLD` stay at `0.5` when
`LOCAL_RELEVANCE_THRESHOLD` just got raised to `0.90` — shouldn't the same
caution apply to both?**

A: The caution that actually applies here is "don't tighten a threshold
without a demonstrated reason to," and that cuts the same way for both
constants even though it produced different actions. `LOCAL_RELEVANCE_
THRESHOLD` moved because a specific, real, reproduced false positive was
found. `WHITELIST_RELEVANCE_THRESHOLD` hasn't had the equivalent
adversarial test run against it yet — raising it now would be guessing, not
responding to evidence, which is exactly the kind of unfounded change this
project avoids making elsewhere (the same reasoning that kept the Tier 2
domain list at one entry rather than guessing at regional Fed banks).

**Q (hard): You picked `0.90` by looking at where the false positive
(`0.782`) and the confirmed-good matches (`0.77`–`0.85`) sat and choosing a
number clearly above both — but that's four data points total. How
confident should anyone actually be that `0.90` is the right number rather
than another guess with slightly better-looking evidence behind it?**

A: Not very confident, and the code says so in its own comment rather than
implying otherwise. Four data points is enough to prove the *old* number
(`0.5`) was wrong and to establish that whatever the right number is, it
has to clear `0.85` — it is not enough to prove `0.90` is correctly
calibrated rather than merely "clearly safer than before." The actual
justification for `0.90` isn't precision, it's the asymmetric-cost argument:
given the choice between a threshold that's probably too conservative and
one that's precisely tuned against four data points from an incomplete
corpus, the conservative one is the responsible choice *because* being
wrong in that direction costs almost nothing, while the alternative directly
risks the specific failure mode Sacred Gate 2 exists to prevent. The honest
position is "this is very likely too high, revisit with a real corpus and
real Component 4 usage," which is exactly what the code comment states, not
"this is correct."

**Q: Why is `WHITELIST_RELEVANCE_THRESHOLD` even a separate constant from
`LOCAL_RELEVANCE_THRESHOLD` if they started at the same value anyway —
wasn't that speculative before this component ever ran?**

A: It was speculative when Component 3 part 1 (step-03) first proposed it,
and this component is the direct proof it was the right call anyway: the
two constants diverged the moment real evidence arrived for one of them but
not the other. If they'd been a single shared constant, fixing the local
false positive would have meant either accepting an unjustified change to
the whitelist threshold too, or hand-splitting them under time pressure
during a bug fix — worse than deciding the separation was warranted calmly,
in advance, before either number was tested against anything real.

**Honest weaknesses, stated plainly:** the `0.90` threshold is a
deliberately conservative guess, not a calibrated number, and the code says
so. `WHITELIST_RELEVANCE_THRESHOLD` is completely unvalidated — zero
adversarial queries have been run against it. `ground_topic`'s full
escalation logic has no automated test yet, only manual demonstration.

## 7. What comes next and why

Tiered RAG grounding (Tier 1 + Tier 2 + escalation) is now complete and
proven, closing out Component 3. Component 4 (hypothesis generation) is
next, and it's the first component that actually *uses* `ground_topic`'s
output inside a real LLM prompt rather than just inspecting it directly.
If either threshold is still wrong in a way this component's testing didn't
catch, the most likely place it surfaces is there: a hypothesis whose
stated rationale doesn't actually follow from its cited grounding — the
same downstream signal named in step-03's own "what comes next," now with
a second possible cause (a Tier 2 false positive never adversarially tested
here) in addition to the first (an untested Tier 1 edge case). Both would
look identical from Component 4's side — a citation that doesn't hold up —
which is exactly why Sacred Gate 2's own citation-validation requirement
exists as a second, independent line of defense past whatever this
component's own thresholds do or don't catch.
