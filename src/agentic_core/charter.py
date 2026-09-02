"""Charter creation and confirmation -- the one step in Stage 5 that runs
before the LangGraph loop (Component 6) exists to invoke anything. See
docs/explanations/stage-5/step-02-charter.md for the full reasoning; this
module is deliberately thin, since most of the actual design lives in the
two-model split in agentic_core/schemas.py.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import select

from agentic_core.db.models import Charter as CharterRow
from agentic_core.schemas import Charter, ParsedCharter
from data_pipeline.db.models import TickerMetadata
from data_pipeline.db.session import SessionFactory
from data_pipeline.screener import screen
from llm_client import structured_output

# Relative, never hand-picked -- .claude/rules/data-pipeline.md. ParsedCharter.
# universe.cut is a closed Literal precisely so this is the only place a cut
# name becomes a number; the LLM never emits one.
CUT_TO_PERCENTILE = {"quintile": 80.0, "tercile": 100.0 / 3.0 * 2, "decile": 90.0}


def _charter_prompt(mandate_text: str, sector_industry_pairs: dict[str, list[str]]) -> str:
    pairs_block = "\n".join(
        f"  {sector}: {', '.join(industries)}" for sector, industries in sector_industry_pairs.items()
    ) or "  (none)"
    return f"""Translate this research mandate into a structured charter.

Mandate: "{mandate_text}"

The universe filter's sector and industry fields must exactly match values
already present in the database -- do not invent a plausible-sounding name
(e.g. "Tech") if the closest real value is different (e.g. "Technology").

These are the only real (sector, industry) PAIRS in the database, grouped by
sector -- an industry listed under one sector never occurs under any other
sector, so sector and industry are not independently choosable:
{pairs_block}

If you specify industry, it must be one of the industries listed under the
sector you chose, from this exact grouping -- never combine a sector with an
industry from a different sector's list, even if both names individually
look real. If the mandate implies an industry-level restriction that doesn't
clearly match one of these real pairs, leave industry null and filter on
sector alone. If no sector clearly applies either, leave both null.

universe.cut controls how selective the universe filter is, relative to the
matched sector/industry group, not an absolute number: "quintile" keeps the
top 20% by the chosen metric, "tercile" keeps the top ~33%, "decile" keeps
the top 10%. Infer the tightest cut the mandate's own wording actually
supports (e.g. "liquid" alone does not imply "decile" over "quintile"
without her saying so) -- default to "quintile" when the mandate gives no
signal either way.

If the mandate does not state a scoring preference, timeframe, or history
start date, use the schema's defaults rather than inventing one.
"""


def _real_sector_industry_pairs(session) -> dict[str, list[str]]:
    """Grouped by sector, not two independent flat lists -- an industry only
    ever occurs under the sector it's grouped with in the real data. Two
    flat lists let the LLM combine a real sector with a real industry that
    never co-occur on any actual ticker (confirmed empirically: "consumer
    tech companies" produced sector="Consumer Cyclical", industry="Consumer
    Electronics" -- both real values individually, but "Consumer Electronics"
    only ever pairs with sector="Technology" in this data, so the combination
    matched zero tickers). Grouping by sector encodes the pairing constraint
    directly in what the prompt shows, rather than relying on the model to
    infer it from two separate lists.
    """
    rows = session.execute(
        select(TickerMetadata.sector, TickerMetadata.industry)
        .where(TickerMetadata.sector.is_not(None), TickerMetadata.industry.is_not(None))
        .distinct()
    ).all()
    pairs: dict[str, list[str]] = {}
    for sector, industry in sorted(rows):
        pairs.setdefault(sector, []).append(industry)
    return pairs


def parse_charter(mandate_text: str) -> ParsedCharter:
    with SessionFactory() as session:
        pairs = _real_sector_industry_pairs(session)
    prompt = _charter_prompt(mandate_text, pairs)
    return structured_output(prompt, response_model=ParsedCharter)


def resolve_universe(parsed: ParsedCharter, as_of: date | None = None) -> Charter:
    """Deterministic -- no LLM involved. Calls the screener in-process
    (data_pipeline.screener.screen), not through MCP: nothing here is an
    LLM choosing which tool to call, so there's no tool-choice discipline
    for the MCP boundary to provide (see step-02's design-decision writeup).
    """
    as_of = as_of or date.today()
    with SessionFactory() as session:
        result = screen(
            session,
            sector=parsed.universe.sector,
            industry=parsed.universe.industry,
            metric=parsed.universe.metric,
            as_of=as_of,
        )
    threshold = CUT_TO_PERCENTILE[parsed.universe.cut]
    tickers = [c.ticker for c in result.candidates if c.percentile >= threshold]
    return Charter(
        parsed=parsed,
        resolved_universe=tickers,
        screening_as_of=as_of,
        screening_group_size=result.group_size,
    )


class CharterNotFoundError(Exception):
    """No charter row exists with the given id. Same naming convention as
    hypothesis.py's DuplicateHypothesisError and verdict.py's
    VerdictValidationError -- a typed exception the API layer (Component 2)
    can map to a specific HTTP status without string-matching a message.
    """


class CharterAlreadyConfirmedError(Exception):
    """Raised by correct_charter -- a confirmed charter is done; it does not
    accept further corrections. (confirm_charter itself does not raise this:
    re-confirming is harmless and idempotent, see its own docstring below.)
    """


class CharterBlockedError(Exception):
    """Raised by confirm_charter when resolved_universe is empty. Previously
    this invariant was only enforced by scripts/set_charter.py's own control
    flow (it simply never prompts to confirm when `blocked` is True) --
    confirm_charter itself had no guard, so any other caller, including an
    HTTP client, could confirm an empty-universe charter directly. Moving the
    check into confirm_charter closes that gap for every caller at once
    rather than trusting each new caller to re-implement the CLI's own
    discipline.
    """


class CorrectionLimitExceededError(Exception):
    """Raised by correct_charter once MAX_CORRECTION_ROUNDS corrections have
    already been made against this chain. See correct_charter's own
    docstring for why the limit is 2, not unbounded.
    """


# Each correction is a real, fuzzy LLM re-interpretation -- the same fuzzy
# MOMENT parse_charter already uses (docs/architecture.md's two sanctioned
# places to be fuzzy), not the same call unchanged: the prompt is different
# every round (see _combined_mandate_for_correction -- original text +
# restated interpretation + correction). What makes re-running it safe is
# that every round passes through the identical schema-validated
# parse_charter/resolve_universe pipeline regardless of what text goes in,
# not that the call itself repeats verbatim (see docs/explanations/stage-7/
# step-02-charter-confirm-correct.md for the full reasoning). Bounding how
# many times that can happen per charter is the same discipline
# .claude/rules/agent-honesty.md already applies to hypothesis testing under
# a charter ("track total hypotheses tested... correct the significance
# threshold accordingly") applied one level up, to how many swings the
# parser gets at one mandate: an unbounded retry loop against an LLM is a
# cost and drift risk regardless of which part of this system it's in.
MAX_CORRECTION_ROUNDS = 2


def create_charter(mandate_text: str) -> tuple[str, Charter, bool]:
    """Parses, resolves, and persists an UNCONFIRMED, round-0 charter row.

    Returns (charter_id, charter, blocked). blocked=True means
    resolved_universe came back empty -- confirm_charter must not be called
    for this id until she re-runs with corrected wording; the row stays
    unconfirmed either way until confirm_charter is called explicitly.
    """
    parsed = parse_charter(mandate_text)
    charter = resolve_universe(parsed)
    blocked = len(charter.resolved_universe) == 0

    charter_id = str(uuid.uuid4())
    with SessionFactory() as session:
        session.add(
            CharterRow(
                id=charter_id,
                mandate_text=mandate_text,
                charter=charter.model_dump(mode="json"),
                confirmed=False,
                created_at=datetime.now(),
            )
        )
        session.commit()
    return charter_id, charter, blocked


def _combined_mandate_for_correction(root_mandate_text: str, previous_charter: Charter, correction_text: str) -> str:
    """Builds the text handed back into parse_charter for a correction round.

    Concatenates three things -- her original sentence, a restatement of
    what the system understood it as, and her correction -- rather than
    just appending the correction to the original mandate text unchanged.
    The restated interpretation is what turns her correction into a
    correctable DELTA ("no, not consumer electronics, all of tech") instead
    of a second, free-floating instruction the model has to guess how to
    reconcile with the first one. Without it, "no, not just consumer
    electronics" means nothing on its own -- it only makes sense against
    what was just proposed.
    """
    p = previous_charter.parsed
    return (
        f'Original request: "{root_mandate_text}"\n\n'
        "This was interpreted as: "
        f"sector={p.universe.sector!r}, industry={p.universe.industry!r}, "
        f"metric={p.universe.metric!r}, cut={p.universe.cut!r}, "
        f"hypothesis_families={[f.value for f in p.hypothesis_families]}, "
        f"timeframe={p.timeframe!r}, "
        f"history_start={p.history_start.isoformat() if p.history_start else 'all available'}, "
        f"scoring_preference={p.scoring_preference!r}.\n\n"
        f'The user says: "{correction_text}"\n\n'
        "Re-interpret the original request in light of this correction, "
        "producing an updated charter that addresses what she said."
    )


def correct_charter(charter_id: str, correction_text: str) -> tuple[str, Charter, bool]:
    """Re-parses a not-yet-confirmed charter in light of a plain-language
    correction, inserting a NEW row rather than editing charter_id's own row
    -- see docs/explanations/stage-7/step-02-charter-confirm-correct.md for
    why this reuses parse_charter (the same trusted, structured translation
    moment the original request went through) instead of a narrower,
    field-level edit mechanism.

    Returns (new_charter_id, charter, blocked) -- same shape as
    create_charter, since a correction round is a re-run of exactly the same
    parse-then-resolve pipeline, just with a richer input.

    Raises CharterNotFoundError, CharterAlreadyConfirmedError, or
    CorrectionLimitExceededError (see each class's own docstring).
    """
    with SessionFactory() as session:
        row = session.get(CharterRow, charter_id)
        if row is None:
            raise CharterNotFoundError(f"no charter with id {charter_id!r}")
        if row.confirmed:
            raise CharterAlreadyConfirmedError(
                f"charter {charter_id!r} is already confirmed -- corrections only apply before confirmation"
            )
        if row.correction_round >= MAX_CORRECTION_ROUNDS:
            raise CorrectionLimitExceededError(
                f"charter {charter_id!r} has already used its {MAX_CORRECTION_ROUNDS} allowed correction "
                "rounds -- confirm this charter as-is, or start over with a fresh mandate via create_charter"
            )
        root_mandate_text = row.mandate_text
        previous_charter = Charter.model_validate(row.charter)
        next_round = row.correction_round + 1

    combined = _combined_mandate_for_correction(root_mandate_text, previous_charter, correction_text)
    parsed = parse_charter(combined)
    charter = resolve_universe(parsed)
    blocked = len(charter.resolved_universe) == 0

    new_charter_id = str(uuid.uuid4())
    with SessionFactory() as session:
        session.add(
            CharterRow(
                id=new_charter_id,
                # Denormalized from the root on every row in the chain
                # (rather than requiring a caller to walk parent_charter_id
                # back to round 0), the same "stamp it at write time instead
                # of deriving it later" reasoning tool_call_traces.window_index
                # already applies: any single row, viewed alone, should still
                # be able to answer "what did she originally ask for."
                mandate_text=root_mandate_text,
                charter=charter.model_dump(mode="json"),
                confirmed=False,
                created_at=datetime.now(),
                parent_charter_id=charter_id,
                correction_round=next_round,
                correction_text=correction_text,
            )
        )
        session.commit()
    return new_charter_id, charter, blocked


def confirm_charter(charter_id: str) -> None:
    """Idempotent by design: confirming an already-confirmed charter again
    just re-sets confirmed_at rather than erroring, since doing so twice
    changes nothing about the charter's own content and there's no
    correctness reason to forbid it. What IS forbidden, newly, is confirming
    a charter whose resolved_universe is empty -- see CharterBlockedError.
    """
    with SessionFactory() as session:
        row = session.get(CharterRow, charter_id)
        if row is None:
            raise CharterNotFoundError(f"no charter with id {charter_id!r}")
        charter = Charter.model_validate(row.charter)
        if not charter.resolved_universe:
            raise CharterBlockedError(
                f"charter {charter_id!r} has an empty resolved universe and cannot be confirmed -- "
                "request a correction or start over with a fresh mandate"
            )
        row.confirmed = True
        row.confirmed_at = datetime.now()
        session.commit()
