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


def create_charter(mandate_text: str) -> tuple[str, Charter, bool]:
    """Parses, resolves, and persists an UNCONFIRMED charter row.

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


def confirm_charter(charter_id: str) -> None:
    with SessionFactory() as session:
        row = session.get(CharterRow, charter_id)
        row.confirmed = True
        row.confirmed_at = datetime.now()
        session.commit()
