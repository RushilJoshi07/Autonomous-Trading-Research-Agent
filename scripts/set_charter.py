"""Interactive charter creation and confirmation -- the terminal stands in
for Stage 7's not-yet-built FastAPI/React confirmation flow (see
docs/architecture.md Step 1: "she confirms it... that confirmation flag is
what allows the agent to start").

Deliberately no automated retry: if parsing produces something wrong, or
the resolved universe comes back empty, this script blocks confirmation and
exits -- re-running it with clearer wording IS the retry mechanism, human-
mediated rather than automated. See step-02's explainer for why that's the
right call specifically for this component, and why Component 6's loop will
need a different (automated, feedback-driven) mechanism instead.

Run: .venv/bin/python scripts/set_charter.py
"""

from __future__ import annotations

import sys

from agentic_core.charter import confirm_charter, create_charter


def main() -> None:
    print("Enter your research mandate (end with an empty line):")
    lines = []
    while True:
        line = input()
        if not line:
            break
        lines.append(line)
    mandate_text = " ".join(lines).strip()
    if not mandate_text:
        print("No mandate entered, exiting.")
        sys.exit(1)

    charter_id, charter, blocked = create_charter(mandate_text)

    print(f"\nCharter id: {charter_id}")
    print(f"Universe filter: sector={charter.parsed.universe.sector!r} "
          f"industry={charter.parsed.universe.industry!r} "
          f"metric={charter.parsed.universe.metric} cut={charter.parsed.universe.cut}")
    print(f"Screening as of {charter.screening_as_of}: "
          f"{charter.screening_group_size} tickers matched sector/industry, "
          f"{len(charter.resolved_universe)} survived the {charter.parsed.universe.cut} cut.")
    print(f"Resolved universe: {charter.resolved_universe}")
    print(f"Hypothesis families: {[f.value for f in charter.parsed.hypothesis_families]}")
    print(f"Timeframe: {charter.parsed.timeframe}   History start: {charter.parsed.history_start or 'all available'}")
    print(f"Scoring preference: {charter.parsed.scoring_preference}")

    if blocked:
        print(
            "\nBLOCKED: the resolved universe is empty. This charter is saved "
            "unconfirmed but cannot be confirmed as-is -- check the sector/"
            "industry values above against what's actually in the database, "
            "then re-run this script with clearer wording."
        )
        sys.exit(1)

    answer = input("\nConfirm this charter? [y/N] ").strip().lower()
    if answer == "y":
        confirm_charter(charter_id)
        print("Confirmed. The agent may now start work under this charter.")
    else:
        print("Not confirmed. Re-run this script to try again with different wording.")


if __name__ == "__main__":
    main()
