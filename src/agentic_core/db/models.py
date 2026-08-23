import uuid
from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import Float, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from data_pipeline.db.models import Base

# JSONB holds the parts of each object still owned by a later component's own
# design pass (Charter's fields: Component 2; Hypothesis.rule/falsification_
# condition/citations: Component 4; StudyDesign.design: Component 5; Verdict.
# claims: Component 7) -- Pydantic validates the shape at the application
# boundary before anything is written here, so the column itself doesn't need
# to. Plain typed columns are used only for fields already settled by
# docs/architecture.md itself (status, grounding_tier, FKs, timestamps) --
# see docs/explanations/stage-5/step-01-* for the line drawn between the two.


class Charter(Base):
    __tablename__ = "charters"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    mandate_text: Mapped[str] = mapped_column(Text)
    charter: Mapped[dict] = mapped_column(JSONB)
    confirmed: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = mapped_column()
    confirmed_at: Mapped[datetime | None] = mapped_column(nullable=True)


class Hypothesis(Base):
    __tablename__ = "hypotheses"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    charter_id: Mapped[str] = mapped_column(ForeignKey("charters.id"), index=True)
    rule: Mapped[dict] = mapped_column(JSONB)
    prediction: Mapped[str] = mapped_column(Text)
    falsification_condition: Mapped[dict] = mapped_column(JSONB)
    rationale: Mapped[str] = mapped_column(Text)
    citations: Mapped[list] = mapped_column(JSONB)
    grounding_tier: Mapped[str] = mapped_column(String(16))  # local_corpus / whitelist_search / none
    status: Mapped[str] = mapped_column(String(16), default="proposed")  # proposed / testing / confirmed / rejected / inconclusive
    created_at: Mapped[datetime] = mapped_column()


class StudyDesign(Base):
    """Immutable once created -- no updated_at, deliberately.

    Pre-registration integrity (the anti-hallucination design in
    .claude/rules/agent-honesty.md) depends on this row never changing after
    the falsification condition is fixed and before any tool call runs.
    Execution state (started/finished/status) lives on StudyRun instead, so
    nothing here ever needs to be mutated.
    """

    __tablename__ = "study_designs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    hypothesis_id: Mapped[str] = mapped_column(ForeignKey("hypotheses.id"), index=True)
    design: Mapped[dict] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column()


class StudyRun(Base):
    """The execution record for one StudyDesign.

    Split from StudyDesign the same way Stage 1's IngestionRun is split from
    its own inputs -- so a design can stay a pure, immutable, pre-registered
    plan while start/finish/status live somewhere that's expected to mutate.
    Stage 5 only ever creates exactly one StudyRun per hypothesis; the split
    exists so a later stage's re-test ("re-test under 2020 only",
    docs/architecture.md Step 7) is a new StudyDesign + StudyRun under the
    same Hypothesis, not a schema change.
    """

    __tablename__ = "study_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    hypothesis_id: Mapped[str] = mapped_column(ForeignKey("hypotheses.id"), index=True)
    study_design_id: Mapped[str] = mapped_column(ForeignKey("study_designs.id"), index=True)
    status: Mapped[str] = mapped_column(String(16), default="running")  # running / completed / failed
    step_count: Mapped[int] = mapped_column(Integer, default=0)
    started_at: Mapped[datetime] = mapped_column()
    finished_at: Mapped[datetime | None] = mapped_column(nullable=True)


class ToolCallTrace(Base):
    """One row per tool call the execution loop makes.

    This is the table Sacred Gate 2's verdict validator reads: every claim in
    a Verdict must resolve to a row here, or the claim is rejected.
    """

    __tablename__ = "tool_call_traces"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    study_run_id: Mapped[str] = mapped_column(ForeignKey("study_runs.id"), index=True)
    step_index: Mapped[int] = mapped_column(Integer)
    # Which StudyDesign window this call ran against. Stamped at write time
    # rather than derived later from the date arguments: Component 7 has to
    # attribute a claim ("out-of-sample Sharpe was 0.21") to the window it
    # came from, and matching on dates would work but breaks the moment two
    # windows share a boundary or a tool records dates differently.
    window_index: Mapped[int] = mapped_column(Integer, default=0)
    tool_name: Mapped[str] = mapped_column(String(64))
    arguments: Mapped[dict] = mapped_column(JSONB)
    result: Mapped[dict] = mapped_column(JSONB)
    is_error: Mapped[bool] = mapped_column(default=False)
    called_at: Mapped[datetime] = mapped_column()


class Verdict(Base):
    __tablename__ = "verdicts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    study_run_id: Mapped[str] = mapped_column(ForeignKey("study_runs.id"), index=True)
    status: Mapped[str] = mapped_column(String(16))  # confirmed / rejected / inconclusive
    claims: Mapped[list] = mapped_column(JSONB)  # [{statement, tool_call_trace_id, value}, ...]
    hypothesis_count_under_charter: Mapped[int] = mapped_column(Integer)
    corrected_significance_threshold: Mapped[float] = mapped_column(Float)
    narrative: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column()


class ScoreboardEntry(Base):
    __tablename__ = "scoreboard_entries"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    verdict_id: Mapped[str] = mapped_column(ForeignKey("verdicts.id"), index=True)
    status: Mapped[str] = mapped_column(String(16))  # confirmed / decayed
    last_verified_at: Mapped[datetime] = mapped_column()
    created_at: Mapped[datetime] = mapped_column()


class CorpusPaper(Base):
    """Fetch-provenance row for one ingested paper -- same 'record when every
    row was fetched' discipline .claude/rules/data-pipeline.md requires for
    price data.

    id is data/corpus/paper_list.json's own slug (e.g. "jegadeesh_titman_1993"),
    not a raw arXiv ID -- not every paper in the curated list has one (most of
    the strongest, most foundational papers predate arXiv's finance coverage
    entirely and live on NBER/SSRN instead), and even the one arXiv-native
    entry in the list uses a readable slug as its id, with the real arXiv ID
    only appearing in its url field. fetch_path records how this row's PDF
    was obtained ("arxiv" auto-fetched vs. "manual" pre-placed by a human) --
    provenance, not a duplicate of paper_list.json's own richer curation
    metadata (authors, year, effect_family, grounds, note), which stays there
    as the one source of truth rather than being copied into two places that
    could drift apart. raw_path points at the PDF on local disk
    (data/corpus/raw/), regenerable (re-fetch for arxiv entries, re-place the
    same file for manual ones) and therefore gitignored -- this row is the
    system of record, not the PDF itself.
    """

    __tablename__ = "corpus_papers"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    title: Mapped[str] = mapped_column(Text)
    fetch_path: Mapped[str] = mapped_column(String(16))  # arxiv / manual
    fetched_at: Mapped[datetime] = mapped_column()
    raw_path: Mapped[str] = mapped_column(String(256))


class CorpusChunk(Base):
    __tablename__ = "corpus_chunks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    paper_id: Mapped[str] = mapped_column(ForeignKey("corpus_papers.id"), index=True)
    chunk_index: Mapped[int] = mapped_column(Integer)
    chunk_text: Mapped[str] = mapped_column(Text)
    embedding: Mapped[list] = mapped_column(Vector(384))
