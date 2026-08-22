"""Tiered RAG grounding: Tier 2 (whitelist search) and the mechanical
escalation across all three tiers. Tier 1 itself (the local corpus) lives
in agentic_core/corpus.py -- this module builds on top of it rather than
duplicating it. See docs/explanations/stage-5/step-03-tier1-corpus.md for
Tier 1's own design, and step-04 (this component) for Tier 2 and escalation.

Tier 1's relevance and Tier 2's score are both roughly 0-1, higher-is-
better, but computed by genuinely different mechanisms -- local cosine
similarity against a fixed BGE embedding space vs. Tavily's own search
ranking. They are not the same metric wearing two names, which is why
LOCAL_RELEVANCE_THRESHOLD and WHITELIST_RELEVANCE_THRESHOLD are two
separate constants, independently tunable -- see each one's own comment.
"""

from __future__ import annotations

from tavily import TavilyClient

from agentic_core.corpus import retrieve_local
from agentic_core.schemas import GroundingChunk, GroundingResult
from data_pipeline.config import settings

# PROVISIONAL -- calibrated against an incomplete 6-paper corpus (only 4 of
# 6 EffectFamily values had any ingested content at all: mean_reversion x1,
# low_volatility x1, value x2, methodology x2 -- zero momentum, zero
# quality, zero seasonality). Re-tune once the corpus reaches the
# Component-3-completion trigger point (30-50 papers, full family coverage
# per docs/architecture.md) -- do not treat 0.90 as settled before then.
#
# 0.90 replaces an original 0.5, raised after a real adversarial test found
# a false positive: "January effect seasonality stock returns calendar
# anomaly" scored 0.782 via retrieve_local against a Fama & French VALUE
# chunk that happens to discuss the January effect as a caveat (real
# chunk-level topical overlap, wrong paper as a primary grounding source) --
# 0.782 sits inside the 0.77-0.85 range of that same session's confirmed-
# CORRECT matches, so no threshold between those two numbers could cleanly
# separate this false positive from a real one. Regression test:
# tests/agentic_core/test_grounding.py -- reproduces this exact chunk and
# query, and fails loudly if this threshold (or the corpus/embedding setup)
# ever regresses to letting it back through.
#
# 0.90 was chosen deliberately conservative, not by measuring a clean
# separation point (none was found): a false Tier-1 match cites the WRONG
# PAPER as grounding (a real integrity problem); an unnecessary Tier-2
# escalation just costs one extra Tavily call (cheap, harmless). Biasing
# toward over-escalation given that asymmetry means real, currently-correct
# matches scoring in the 0.77-0.89 range (e.g. "low beta stocks outperform"
# -> Betting Against Beta at 0.77) will ALSO now escalate to Tier 2
# unnecessarily -- an accepted, known cost of this conservative choice, not
# an oversight.
LOCAL_RELEVANCE_THRESHOLD = 0.90

# PROVISIONAL, same as LOCAL_RELEVANCE_THRESHOLD above, but unlike it this
# number has NOT been adversarially tested -- no query has yet been run
# through retrieve_whitelist specifically looking for a false positive the
# way the seasonality/Fama-French case was found for Tier 1. Left at the
# original starting value (comfortably below Tavily's own observed score
# for a real, correct match: 0.87 for "Quality Minus Junk" against a
# quality-factor query) rather than raised, because there's no evidence yet
# either way -- raising it without a demonstrated failure would be exactly
# the kind of unfounded tightening this project avoids elsewhere. Re-test
# the same way Tier 1 was once Component 4 starts generating real,
# non-hand-picked queries against this tier.
WHITELIST_RELEVANCE_THRESHOLD = 0.5

# federalreserve.gov only -- the Board's own FEDS working-paper series, the
# single most canonical "Federal Reserve working papers" source named in
# docs/architecture.md. Not the 12 regional Fed banks' own domains: no real
# Tier 2 query has yet turned up a case where one of those was needed, and
# guessing a longer domain list without that evidence would be exactly the
# kind of premature expansion this project's own working agreement warns
# against. Expand from a real miss, not speculatively.
_WHITELIST_DOMAINS = ["ssrn.com", "papers.ssrn.com", "nber.org", "arxiv.org", "federalreserve.gov"]


def retrieve_whitelist(query: str, top_k: int = 5) -> list[GroundingChunk]:
    """Deterministic -- no LLM involved, same as retrieve_local. api_key is
    passed explicitly rather than relying on tavily-python's own
    os.getenv("TAVILY_API_KEY") fallback, matching llm_client's own
    explicit-over-ambient reasoning for aws_profile: the dependency should
    travel with the function, not with whichever shell happens to have the
    right environment variable set.
    """
    client = TavilyClient(api_key=settings.tavily_api_key)
    response = client.search(query, include_domains=_WHITELIST_DOMAINS, max_results=top_k)
    return [
        GroundingChunk(
            source="whitelist_search",
            title=result["title"],
            text=result["content"],
            relevance=result["score"],
            url=result["url"],
        )
        for result in response["results"]
    ]


def ground_topic(query: str, top_k: int = 5) -> GroundingResult:
    """The tiered escalation itself -- three plain comparisons, no LLM
    judgment anywhere in this function. Only the single best-ranked result
    in each tier is checked against that tier's threshold: if the top
    result isn't relevant enough, nothing ranked below it will be either,
    so there's no need to look past it.
    """
    local_results = retrieve_local(query, top_k)
    local_chunks = [
        GroundingChunk(
            source="local_corpus",
            title=result["title"],
            text=result["chunk_text"],
            relevance=result["relevance"],
            paper_id=result["paper_id"],
        )
        for result in local_results
    ]
    if local_chunks and local_chunks[0].relevance >= LOCAL_RELEVANCE_THRESHOLD:
        return GroundingResult(tier="local_corpus", chunks=local_chunks)

    whitelist_chunks = retrieve_whitelist(query, top_k)
    if whitelist_chunks and whitelist_chunks[0].relevance >= WHITELIST_RELEVANCE_THRESHOLD:
        return GroundingResult(tier="whitelist_search", chunks=whitelist_chunks)

    return GroundingResult(tier="none", chunks=[])
