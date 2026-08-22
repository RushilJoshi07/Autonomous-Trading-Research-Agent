"""Tier-1 local corpus: ingesting data/corpus/paper_list.json into
corpus_papers/corpus_chunks, and retrieving from it. See
docs/explanations/stage-5/step-03-tier1-corpus.md for the full design
reasoning -- this module implements exactly what's described there. Tier 2
(whitelist search) and the tiered escalation between them are a separate,
later component -- this module is Tier 1 only.

Four fetch_path values, four different outcomes -- see ingest_paper's own
dispatch. Idempotent: re-running against a paper already in corpus_papers
skips it rather than re-fetching/re-embedding (data_pipeline/ingest/
upsert.py's own discipline, applied here).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import arxiv
import requests
import tiktoken
from langchain_text_splitters import RecursiveCharacterTextSplitter
from pypdf import PdfReader
from sentence_transformers import SentenceTransformer
from sqlalchemy import select

from agentic_core.db.models import CorpusChunk, CorpusPaper
from data_pipeline.db.session import SessionFactory

_REPO_ROOT = Path(__file__).resolve().parents[2]
_PAPER_LIST_PATH = _REPO_ROOT / "data" / "corpus" / "paper_list.json"
_RAW_DIR = _REPO_ROOT / "data" / "corpus" / "raw"

# ~500 tokens per chunk, ~50 overlap -- the plan's own stated target, made
# literal with a real tokenizer rather than a character-count approximation.
_CHUNK_TOKENS = 500
_CHUNK_OVERLAP_TOKENS = 50

# bge-small-en-v1.5 is retrieval-tuned and asymmetric: passages are embedded
# plain (no prefix), only the query side gets an instruction prefix -- see
# embed_query. Getting this backwards (or applying the prefix to both sides)
# quietly degrades retrieval quality without ever raising an error, which is
# why it's stated here rather than left to be rediscovered.
_EMBEDDING_MODEL_NAME = "BAAI/bge-small-en-v1.5"
_QUERY_INSTRUCTION = "Represent this sentence for searching relevant passages: "

_embedding_model: SentenceTransformer | None = None


def _get_embedding_model() -> SentenceTransformer:
    global _embedding_model
    if _embedding_model is None:
        _embedding_model = SentenceTransformer(_EMBEDDING_MODEL_NAME)
    return _embedding_model


def load_paper_list() -> list[dict]:
    return json.loads(_PAPER_LIST_PATH.read_text())


def _arxiv_id_from_url(url: str) -> str:
    match = re.search(r"arxiv\.org/abs/([\w.\-]+)", url)
    if not match:
        raise ValueError(f"could not extract an arXiv id from {url!r}")
    return match.group(1)


def _fetch_arxiv_pdf(entry: dict) -> Path:
    """Confirms the paper genuinely exists on arXiv (a real API round trip,
    not just trusting the id string) before downloading -- if the entry's
    url has a typo'd or stale arXiv id, this raises here rather than
    silently downloading nothing or the wrong paper.
    """
    arxiv_id = _arxiv_id_from_url(entry["url"])
    client = arxiv.Client()
    result = next(client.results(arxiv.Search(id_list=[arxiv_id])))
    pdf_link = next(link for link in result.links if link.content_type == "application/pdf")

    dest = _RAW_DIR / f"{entry['id']}.pdf"
    response = requests.get(pdf_link.href, timeout=30)
    response.raise_for_status()
    dest.write_bytes(response.content)
    return dest


def _extract_text(pdf_path: Path) -> str:
    reader = PdfReader(str(pdf_path))
    text = "\n\n".join(page.extract_text() or "" for page in reader.pages)
    # Confirmed empirically: at least one of the downloaded NBER PDFs
    # extracts one or more literal NUL (0x00) bytes via pypdf -- a known
    # artifact of certain PDFs' internal encoding, not a pypdf bug to work
    # around elsewhere. Postgres's text type cannot store NUL at all (not
    # an escaping problem -- it's a hard storage limitation), so this strips
    # them here, once, rather than at every downstream call site. Same
    # disclosed-not-hidden category as the plan's existing note that math/
    # tables extract noisily -- this is one more instance of "PDF text
    # extraction is imperfect," not a new kind of problem.
    return text.replace("\x00", "")


def _chunk_text(text: str) -> list[str]:
    encoding = tiktoken.get_encoding("cl100k_base")
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=_CHUNK_TOKENS,
        chunk_overlap=_CHUNK_OVERLAP_TOKENS,
        length_function=lambda s: len(encoding.encode(s)),
    )
    return splitter.split_text(text)


@dataclass
class IngestOutcome:
    paper_id: str
    status: str  # ingested / already_ingested / skipped_not_placed / skipped_needs_confirmation / skipped_citation_only
    detail: str
    chunk_count: int = 0


def ingest_paper(entry: dict, session) -> IngestOutcome:
    paper_id = entry["id"]
    fetch_path = entry["fetch_path"]

    if fetch_path == "citation_only":
        return IngestOutcome(
            paper_id, "skipped_citation_only",
            "no accessible full text -- citation-only reference, never ingested, never retrievable",
        )
    if fetch_path == "manual_needs_confirmation":
        return IngestOutcome(
            paper_id, "skipped_needs_confirmation",
            "provenance not yet verified -- will not be placed or ingested until confirmed",
        )

    existing = session.get(CorpusPaper, paper_id)
    if existing is not None:
        chunk_count = len(session.execute(select(CorpusChunk.id).where(CorpusChunk.paper_id == paper_id)).all())
        return IngestOutcome(paper_id, "already_ingested", "already in corpus_papers, skipped (idempotent)", chunk_count)

    if fetch_path == "arxiv":
        pdf_path = _fetch_arxiv_pdf(entry)
    elif fetch_path == "manual":
        pdf_path = _RAW_DIR / f"{paper_id}.pdf"
        if not pdf_path.exists():
            return IngestOutcome(
                paper_id, "skipped_not_placed",
                f"expected {pdf_path.relative_to(_REPO_ROOT)} -- not yet placed "
                f"(source: {entry.get('source', '?')}, url: {entry.get('url') or 'none recorded'})",
            )
    else:
        raise ValueError(f"unknown fetch_path {fetch_path!r} for {paper_id!r}")

    text = _extract_text(pdf_path)
    chunks = _chunk_text(text)
    model = _get_embedding_model()
    embeddings = model.encode(chunks, normalize_embeddings=True)

    session.add(CorpusPaper(
        id=paper_id,
        title=entry["title"],
        fetch_path=fetch_path,
        fetched_at=datetime.now(),
        raw_path=str(pdf_path.relative_to(_REPO_ROOT)),
    ))
    # Confirmed empirically (reproduced with a single CorpusPaper + single
    # CorpusChunk, no batching involved): without an ORM relationship()
    # linking the two mapper classes, SQLAlchemy's unit-of-work does not
    # infer the FK dependency and can flush corpus_chunks' insert before
    # corpus_papers', even though both were add()ed in the correct order in
    # this same transaction -- a real ForeignKeyViolation, not a batching
    # quirk. Forcing the paper's own insert to hit the DB first removes any
    # dependence on the ORM's implicit ordering.
    session.flush()
    for i, (chunk, embedding) in enumerate(zip(chunks, embeddings)):
        session.add(CorpusChunk(paper_id=paper_id, chunk_index=i, chunk_text=chunk, embedding=embedding.tolist()))
    session.commit()
    return IngestOutcome(paper_id, "ingested", f"{len(chunks)} chunks", len(chunks))


def ingest_all() -> list[IngestOutcome]:
    outcomes = []
    with SessionFactory() as session:
        for entry in load_paper_list():
            outcomes.append(ingest_paper(entry, session))
    return outcomes


def embed_query(query: str) -> list[float]:
    model = _get_embedding_model()
    return model.encode(_QUERY_INSTRUCTION + query, normalize_embeddings=True).tolist()


def embed_passage(text: str) -> list[float]:
    """No instruction prefix -- passages are embedded plain, per bge-small-
    en-v1.5's asymmetric convention (see this module's own docstring).
    A single-text counterpart to ingest_paper's own batched
    model.encode(chunks, ...) call, for callers (tests, mainly) that need to
    embed one known piece of text the exact same way a real chunk would be.
    """
    model = _get_embedding_model()
    return model.encode(text, normalize_embeddings=True).tolist()


def retrieve_local(query: str, top_k: int = 5) -> list[dict]:
    """Cosine similarity via pgvector's <=> operator (1 - cosine_similarity,
    so ascending order = most similar first). A plain sequential scan --
    deliberately no ivfflat/hnsw index at this corpus's size (a few thousand
    rows at most), see step-01's own note on this.
    """
    query_embedding = embed_query(query)
    with SessionFactory() as session:
        distance = CorpusChunk.embedding.cosine_distance(query_embedding)
        stmt = (
            select(CorpusChunk, CorpusPaper.title, distance.label("distance"))
            .join(CorpusPaper, CorpusChunk.paper_id == CorpusPaper.id)
            .order_by(distance)
            .limit(top_k)
        )
        rows = session.execute(stmt).all()
    return [
        {
            "paper_id": chunk.paper_id,
            "title": title,
            "chunk_text": chunk.chunk_text,
            "relevance": 1.0 - distance,
        }
        for chunk, title, distance in rows
    ]
