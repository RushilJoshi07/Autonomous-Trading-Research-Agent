"""Hypothesis generation -- the first component that puts grounding
(Component 3) into a real LLM prompt. See
docs/explanations/stage-5/step-05-hypothesis-generation.md for the full
design reasoning.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime

from sqlalchemy import select

from agentic_core.db.models import Charter as CharterRow
from agentic_core.db.models import Hypothesis as HypothesisRow
from agentic_core.grounding import ground_topic
from agentic_core.schemas import Charter, EffectFamily, GroundingResult, Hypothesis, ParsedHypothesis
from backtester.registry import ALL_INDICATORS
from backtester.schema import StrategyRule
from data_pipeline.db.session import SessionFactory
from llm_client import structured_output

# Fixed, not LLM-generated -- the fuzziness of translating her words into
# structure already happened once, at charter-parsing time (Component 2);
# there's no reason to reopen that boundary here just to decide what to
# search for.
_FAMILY_QUERY_TEMPLATES: dict[EffectFamily, str] = {
    EffectFamily.MOMENTUM: "momentum trading strategies stock returns",
    EffectFamily.MEAN_REVERSION: "mean reversion trading strategies stock returns",
    EffectFamily.LOW_VOLATILITY: "low volatility anomaly stock returns beta",
    EffectFamily.VALUE: "value factor investing book-to-market stock returns",
    EffectFamily.QUALITY: "quality factor investing profitability stock returns",
    EffectFamily.SEASONALITY: "seasonality calendar effects stock returns",
}


class DuplicateHypothesisError(Exception):
    """Raised, not retried -- propose_hypothesis runs unattended (unlike
    Component 2's charter, nobody is present to just re-run it), so the
    caller (a script for now, Component 6/8 eventually) decides what to do
    next. Building retry-with-feedback into this function would be solving
    an orchestration-layer problem that doesn't exist yet.
    """


def _rule_hash(rule: StrategyRule) -> str:
    canonical = json.dumps(rule.model_dump(mode="json"), sort_keys=True)
    return hashlib.sha256(canonical.encode()).hexdigest()


def _hypothesis_prompt(charter: Charter, family: EffectFamily, grounding: GroundingResult) -> str:
    indicator_names = ", ".join(sorted(ALL_INDICATORS.keys()))

    if grounding.tier == "none":
        grounding_block = (
            "No grounding evidence was found in the local corpus or whitelist search "
            "for this topic. Propose from your own general knowledge of the finance "
            "literature. This hypothesis will be flagged grounding: none and held to a "
            "stricter statistical significance bar downstream than a grounded hypothesis "
            "would be -- be appropriately cautious rather than overconfident."
        )
    else:
        chunks_text = "\n\n".join(f"[{c.title}]\n{c.text}" for c in grounding.chunks)
        grounding_block = (
            f"The following evidence was retrieved (tier: {grounding.tier}). Ground "
            f"your rationale in what this evidence actually says -- do not attribute a "
            f"finding to it that this text doesn't support:\n\n{chunks_text}"
        )

    return f"""Propose a single, testable trading-strategy hypothesis in the
"{family.value}" effect family, for this research charter.

Universe ({len(charter.resolved_universe)} tickers, {charter.parsed.universe.sector or 'no sector filter'}, \
{charter.parsed.universe.metric} {charter.parsed.universe.cut}): {charter.resolved_universe}
Timeframe: {charter.parsed.timeframe}
Scoring preference: {charter.parsed.scoring_preference}

{grounding_block}

The rule's entry/exit conditions must use only real indicators from this
list -- an unlisted name will fail validation: {indicator_names}

Pre-register the falsification condition BEFORE considering what the
result might be: state the bar that would prove this hypothesis wrong,
using a real metric from BacktestResult or SignificanceResult, not one
invented for this hypothesis.
"""


def propose_hypothesis(charter_id: str, family: EffectFamily) -> tuple[str, Hypothesis]:
    """Raises ValueError if the charter doesn't exist, isn't confirmed (the
    confirmation flag is what allows the agent to start, per
    docs/architecture.md Step 1 -- enforced here, not just by convention),
    or doesn't list this family. Raises DuplicateHypothesisError if the
    generated rule is structurally identical to one already proposed under
    this charter.
    """
    with SessionFactory() as session:
        charter_row = session.get(CharterRow, charter_id)
        if charter_row is None:
            raise ValueError(f"no charter with id {charter_id!r}")
        if not charter_row.confirmed:
            raise ValueError(f"charter {charter_id!r} is not confirmed -- cannot propose hypotheses against it")
        charter = Charter.model_validate(charter_row.charter)
        if family not in charter.parsed.hypothesis_families:
            allowed = [f.value for f in charter.parsed.hypothesis_families]
            raise ValueError(f"{family.value!r} is not in this charter's hypothesis_families {allowed}")

        existing_hashes = {
            _rule_hash(StrategyRule.model_validate(rule_json))
            for (rule_json,) in session.execute(select(HypothesisRow.rule).where(HypothesisRow.charter_id == charter_id))
        }

    grounding = ground_topic(_FAMILY_QUERY_TEMPLATES[family])
    prompt = _hypothesis_prompt(charter, family, grounding)
    parsed = structured_output(prompt, response_model=ParsedHypothesis)

    if _rule_hash(parsed.rule) in existing_hashes:
        raise DuplicateHypothesisError(f"an identical rule already exists under charter {charter_id!r}")

    hypothesis = Hypothesis(parsed=parsed, grounding_tier=grounding.tier, citations=grounding.chunks)

    hypothesis_id = str(uuid.uuid4())
    with SessionFactory() as session:
        row = HypothesisRow(
            id=hypothesis_id,
            charter_id=charter_id,
            rule=parsed.rule.model_dump(mode="json"),
            prediction=parsed.prediction,
            falsification_condition=parsed.falsification_condition.model_dump(mode="json"),
            rationale=parsed.rationale,
            citations=[c.model_dump(mode="json") for c in grounding.chunks],
            grounding_tier=grounding.tier,
            status="proposed",
            created_at=datetime.now(),
        )
        session.add(row)
        session.commit()

    return hypothesis_id, hypothesis
