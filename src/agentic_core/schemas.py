"""Pydantic schemas for the charter -- the one human-in-the-loop step before
Stage 5's agent loop exists at all (Component 6). Split into two models on
purpose: ParsedCharter is exactly, only, what the LLM is trusted to produce;
Charter wraps it with resolved_universe, which the LLM never sees or writes
to. See agentic_core/charter.py for how the second half gets filled in.
"""

from __future__ import annotations

from datetime import date
from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from backtester.schema import StrategyRule


class EffectFamily(str, Enum):
    """The effect families docs/architecture.md names as the ones a handful
    of curated papers can cover ("only a few dozen effect families...
    momentum, mean-reversion, low-volatility, value, quality, seasonality").

    This is exactly the closed set ParsedCharter.hypothesis_families offers
    her -- "methodology" is deliberately NOT a member here, even though the
    corpus also holds methodology papers (Component 3): "investigate
    methodology" is not something a research mandate would ever ask for,
    since methodology papers ground the system's own statistical rigor
    (the multiple-comparisons correction), not a hypothesis family to test.
    See CorpusEffectFamily below for the corpus's own, slightly wider tag set.
    """

    MOMENTUM = "momentum"
    MEAN_REVERSION = "mean_reversion"
    LOW_VOLATILITY = "low_volatility"
    VALUE = "value"
    QUALITY = "quality"
    SEASONALITY = "seasonality"


CorpusEffectFamily = EffectFamily | Literal["methodology", "liquidity"]
"""What a corpus paper (data/corpus/paper_list.json) can be tagged with --
EffectFamily's six values, plus "methodology" (papers like Harvey/Liu/Zhu
and Bailey/Lopez de Prado that ground the agent's own statistical rigor
rather than a specific effect) and "liquidity" (papers like Amihud (2002)
that ground the SCREENER's liquidity metric -- UniverseFilter.metric,
above -- not a hypothesis family a research mandate would investigate).

"liquidity" specifically cannot be added to EffectFamily itself, even
though it reads like a natural sixth-ish family: UniverseFilter.metric
already uses the literal string "liquidity" for a completely different
concept (which screener column a universe filter ranks tickers by). Two
fields both called "liquidity" meaning two different things would be a
real, confusing collision, not a cosmetic one -- keeping this corpus-only
tag on CorpusEffectFamily, not EffectFamily, is what keeps that distinction
intact everywhere else in the schema.
"""


class UniverseFilter(BaseModel):
    """What screen_universe (Stage 4) needs to resolve a real ticker list.

    cut is a closed vocabulary, not a raw percentile float, on purpose --
    .claude/rules/data-pipeline.md requires relative, never hand-picked,
    thresholds. Code (agentic_core.charter.CUT_TO_PERCENTILE) maps each
    name to a fixed percentile; the LLM never emits a number here.
    """

    sector: str | None = None
    industry: str | None = None
    metric: Literal["liquidity", "volatility"] = "liquidity"
    cut: Literal["quintile", "tercile", "decile"] = "quintile"


class ParsedCharter(BaseModel):
    """Exactly what llm_client.structured_output is asked to produce from
    her mandate text. No ticker symbols, no resolved data of any kind --
    only fields whose fuzziness is the sanctioned kind (translating her
    sentence into structure), per docs/architecture.md's "vagueness stops
    at the human boundary."
    """

    universe: UniverseFilter
    hypothesis_families: list[EffectFamily] = Field(min_length=1)
    timeframe: Literal["daily", "weekly", "monthly"] = "daily"
    history_start: date | None = None  # None = use all available history
    scoring_preference: Literal["robustness", "raw_returns", "balanced"] = "balanced"


class Charter(BaseModel):
    """ParsedCharter plus what code alone resolves. resolved_universe is
    never a field the LLM is asked to fill in -- there is no path by which
    a hallucinated ticker symbol can reach this object.
    """

    parsed: ParsedCharter
    resolved_universe: list[str]
    screening_as_of: date
    screening_group_size: int  # disclosure: how many tickers matched sector/industry before the cut


class GroundingChunk(BaseModel):
    """One retrieved piece of evidence, from either tier -- a unified shape
    so Component 4 can build a hypothesis's citations the same way
    regardless of which tier actually fired. paper_id is only ever set for
    local_corpus (Tier 1 knows exactly which curated paper a chunk came
    from); url is only ever set for whitelist_search (Tier 2 is live web
    search, with no curated paper_id to point at). relevance is comparable
    within a tier, not necessarily across tiers -- see ground_topic's own
    module docstring for why.
    """

    source: Literal["local_corpus", "whitelist_search"]
    title: str
    text: str
    relevance: float
    paper_id: str | None = None
    url: str | None = None


class GroundingResult(BaseModel):
    """What ground_topic returns -- tier is the mechanical escalation
    outcome (docs/architecture.md: "Escalation is MECHANICAL... never a
    subjective LLM judgment"), chunks is empty exactly when tier == "none".
    """

    tier: Literal["local_corpus", "whitelist_search", "none"]
    chunks: list[GroundingChunk]


class FalsificationCondition(BaseModel):
    """Pre-registered BEFORE any testing (.claude/rules/agent-honesty.md) --
    deliberately single-clause, not a compound and/or tree the way entry/
    exit conditions are: pre-registration integrity benefits from staying
    simple, and a compound condition is exactly the kind of thing that could
    quietly grow wiggle room later. metric's vocabulary is drawn directly
    from BacktestResult's and SignificanceResult's real field names
    (backtester/result.py, research_stats/significance.py) so Component 7
    can mechanically evaluate this against whatever result object it
    actually has, with no name mismatch possible.

    This is the hypothesis's OWN pre-registered bar, separate from Component
    7's multiple-comparisons-corrected significance threshold (which
    adjusts for how many hypotheses have been tested under the charter, plus
    a stricter multiplier for grounding: none) -- two different mechanisms
    applied at two different points, not overlapping.
    """

    metric: Literal[
        "sharpe_ratio", "annual_return_pct", "total_return_pct",
        "max_drawdown_pct", "win_rate_pct", "p_value",
    ]
    comparison: Literal["less_than", "greater_than"]
    threshold: float

    @model_validator(mode="after")
    def _check_p_value_range(self) -> "FalsificationCondition":
        if self.metric == "p_value" and not (0.0 <= self.threshold <= 1.0):
            raise ValueError(f"p_value threshold must be in [0, 1], got {self.threshold}")
        return self


class ParsedHypothesis(BaseModel):
    """Exactly what llm_client.structured_output is asked to produce --
    rule reuses Stage 3's StrategyRule as-is, which means its own
    model_validators (real indicator, valid params, well-formed exit) fire
    automatically the moment this whole object validates. No separate
    executability check needed; Component 4 doesn't rebuild what Stage 3
    already built.
    """

    rule: StrategyRule
    prediction: str
    falsification_condition: FalsificationCondition
    rationale: str


class Hypothesis(BaseModel):
    """ParsedHypothesis plus what code alone resolves. citations and
    grounding_tier are never fields the LLM is asked to fill in -- both
    come directly from the same GroundingResult that grounded the prompt,
    the same resolved_universe-style guarantee Charter already established:
    there is no path by which a hallucinated or misremembered citation can
    reach this object.
    """

    parsed: ParsedHypothesis
    grounding_tier: Literal["local_corpus", "whitelist_search", "none"]
    citations: list[GroundingChunk]
