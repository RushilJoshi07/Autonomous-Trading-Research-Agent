"""Regression coverage for agentic_core/grounding.py's tiered escalation --
Stage 5, Component 3 (Tier 2) formal coverage.
"""

from datetime import datetime

from agentic_core.corpus import embed_passage, retrieve_local
from agentic_core.db.models import CorpusChunk, CorpusPaper
from agentic_core.grounding import LOCAL_RELEVANCE_THRESHOLD

# The exact real chunk that produced a false-positive match during Tier 2's
# own verification (Stage 5, Component 3 part 2) -- Fama & French's own
# passing discussion of the January effect (citing Roll 1983, Keim 1983) as
# a caveat WITHIN their value-factor paper, not a paper primarily about
# seasonality. The embedding correctly found genuine chunk-level topical
# overlap; the paper still isn't the right primary grounding source for a
# seasonality hypothesis. Real relevance observed against the real,
# then-6-paper corpus: 0.782 -- inside the 0.77-0.85 range of that same
# session's confirmed-GOOD matches, which is exactly why a single global
# threshold below ~0.90 couldn't reliably separate this from a true
# positive. Reproduced verbatim (not paraphrased) so this test embeds the
# exact text that produced the exact score, not an approximation that might
# behave differently.
_FALSE_POSITIVE_CHUNK_TEXT = (
    "R,, = a + bl,P,, + b2rln(ME,,) + b3,1n(BE/ME,,) + e,, \n"
    "a 2.07 5.75 6.55 1.73 6.22 3.54 2.40 5.25 5.92 \n"
    "b, -0.17 5.12 -0.62 0.10 5.33 0.25 -0.44 4.91 -1.17 \n"
    "b2 -0.12 0.89 -2.52 -0.15 1.03 -1.91 -0.09 0.74 -1.64 \n"
    "b3 0.33 1.24 4.80 0.34 1.36 3.17 0.31 1.10 3.67 \n"
    "subperiods (0.36 and 0.35) are close to the average slope (0.35) for the overall \n"
    "period. The subperiod results thus support the conclusion that, among the \n"
    "variables considered here, book-to-market equity is consistently the most \n"
    "powerful for explaining the cross-section of average stock returns. \n"
    "Finally, Roll (1983) and Keim (1983) show that the size effect is stronger in \n"
    "January. We have examined the monthly slopes from the FM regressions in \n"
    "Table VI for evidence of a January seasonal in the relation between book-to- \n"
    "market equity and average return. The average January slopes for ln(BE /ME) \n"
    "are about twice those for February to December. Unlike the size effect, \n"
    "however, the strong relation between book-to-market equity and average \n"
    "return is not special to January. The average monthly February-to-December \n"
    "slopes for ln(BE/ME) are about 4 standard errors from 0, and they are close \n"
    "to (within 0.05 of) the average slopes for the whole year. Thus, there is a \n"
    "January seasonal in the book-to-market equity effect, but the positive rela- \n"
    "tion between BE/ME and average return is strong throughout the year. \n"
    "D. p and the Market Factor: Caveats \n"
    "Some caveats about the negative evidence on the role of 0 in average"
)

_SEASONALITY_QUERY = "January effect seasonality stock returns calendar anomaly"


def test_seasonality_query_does_not_falsely_match_fama_french_chunk(corpus_db_session):
    """If this test ever fails, LOCAL_RELEVANCE_THRESHOLD or the embedding
    model/corpus setup changed in a way that has reintroduced this exact
    false-positive gap -- worth investigating by hand, not silencing the
    test or bumping the threshold again without understanding why.

    Deliberately checks retrieve_local's raw score against
    LOCAL_RELEVANCE_THRESHOLD directly, not the full ground_topic escalation
    -- the bug was specifically about Tier 1 producing a false positive, and
    testing that in isolation avoids needing a live Tavily call (Tier 2) just
    to exercise a Tier-1-only invariant.
    """
    corpus_db_session.add(CorpusPaper(
        id="fama_french_1992",
        title="The Cross-Section of Expected Stock Returns",
        fetch_path="manual",
        fetched_at=datetime.now(),
        raw_path="data/corpus/raw/fama_french_1992.pdf",
    ))
    corpus_db_session.flush()
    corpus_db_session.add(CorpusChunk(
        paper_id="fama_french_1992",
        chunk_index=0,
        chunk_text=_FALSE_POSITIVE_CHUNK_TEXT,
        embedding=embed_passage(_FALSE_POSITIVE_CHUNK_TEXT),
    ))
    corpus_db_session.commit()

    results = retrieve_local(_SEASONALITY_QUERY, top_k=1)

    assert results, "expected exactly one match -- exactly one chunk is seeded"
    best_relevance = results[0]["relevance"]
    assert best_relevance < LOCAL_RELEVANCE_THRESHOLD, (
        f"top match scored {best_relevance:.3f}, which now clears "
        f"LOCAL_RELEVANCE_THRESHOLD ({LOCAL_RELEVANCE_THRESHOLD}) -- this is "
        "the exact false-positive pattern the threshold was raised to prevent."
    )
