"""Offline, one-time (well -- idempotent, so safe to re-run) ingestion of
data/corpus/paper_list.json into corpus_papers/corpus_chunks. Prints a full,
honest accounting of every entry's outcome -- including the ones that were
never going to be ingested (citation_only) and the ones still waiting on a
human step (manual PDFs not yet placed, or provenance not yet confirmed) --
not just a count of successes.

Run: .venv/bin/python scripts/ingest_corpus.py
"""

from __future__ import annotations

from agentic_core.corpus import ingest_all


def main() -> None:
    outcomes = ingest_all()

    print(f"{len(outcomes)} entries in data/corpus/paper_list.json\n")
    for outcome in outcomes:
        print(f"  [{outcome.status:<26}] {outcome.paper_id:<38} {outcome.detail}")

    by_status: dict[str, int] = {}
    for outcome in outcomes:
        by_status[outcome.status] = by_status.get(outcome.status, 0) + 1

    total_chunks = sum(o.chunk_count for o in outcomes)
    print(f"\n{sum(v for k, v in by_status.items() if k in ('ingested', 'already_ingested'))} "
          f"papers in the retrievable corpus ({total_chunks} chunks total)")
    for status, count in sorted(by_status.items()):
        print(f"  {status}: {count}")


if __name__ == "__main__":
    main()
