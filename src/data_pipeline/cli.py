"""Command-line entry points for manual pipeline operations.

Usage:
    python -m data_pipeline.cli ingest
    python -m data_pipeline.cli refetch
    python -m data_pipeline.cli check-actions
"""
import sys

from data_pipeline.db.session import SessionFactory
from data_pipeline.ingest.runner import check_corporate_actions, full_refetch, ingest_daily
from data_pipeline.universe import all_tickers


def main() -> None:
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    command = sys.argv[1]

    with SessionFactory() as session:
        tickers = all_tickers(session)

    if command == "ingest":
        run_id = ingest_daily(tickers)
        print(f"Ingest complete. run_id={run_id}")
    elif command == "refetch":
        run_id = full_refetch(tickers)
        print(f"Full refetch complete. run_id={run_id}")
    elif command == "check-actions":
        check_corporate_actions(tickers)
        print("Corporate action check complete.")
    else:
        print(f"Unknown command: {command}")
        print(__doc__)
        sys.exit(1)


if __name__ == "__main__":
    main()
