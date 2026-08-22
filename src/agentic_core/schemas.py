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

from pydantic import BaseModel, Field


class EffectFamily(str, Enum):
    """The effect families docs/architecture.md names as the ones a handful
    of curated papers can cover ("only a few dozen effect families...
    momentum, mean-reversion, low-volatility, value, quality, seasonality").
    Component 3's corpus tags each paper against this same enum, so a
    hypothesis's family and the corpus's own categorization can never drift
    apart into two different vocabularies.
    """

    MOMENTUM = "momentum"
    MEAN_REVERSION = "mean_reversion"
    LOW_VOLATILITY = "low_volatility"
    VALUE = "value"
    QUALITY = "quality"
    SEASONALITY = "seasonality"


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
