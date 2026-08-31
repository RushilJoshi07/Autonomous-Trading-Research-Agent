"""Regression coverage for eval/resumable.py -- Stage 6's resumability,
pacing, and fail-fast primitives.

Everything here is zero-cost and zero-wait: no real sleep, no real
database, no real LLM. The one property this module exists to prove --
does the pacing/stop-early logic actually behave correctly -- is exactly
the property a live run cannot cheaply verify, which is why it is proven
here, in milliseconds, before it is ever wired to a real Bedrock call.
"""

import asyncio

from eval.resumable import ResumeRecord, is_rate_limited, load_resume_state, resume_action, run_with_pacing, save_resume_state


def _record(healthy_passed: bool, **overrides) -> ResumeRecord:
    defaults = dict(
        name="fake_case", category="planted_true", expected_status="confirmed",
        ticker="FAKE", charter_id="c1", hypothesis_id="h1", design_id="d1",
        healthy_passed=healthy_passed, healthy_detail="x",
    )
    defaults.update(overrides)
    return ResumeRecord(**defaults)


# ---------------------------------------------------------------------------
# resume_action -- the skip / cleanup-and-retry / build-fresh decision
# ---------------------------------------------------------------------------


def test_resume_action_builds_fresh_with_no_prior_record():
    assert resume_action(None) == "build_fresh"


def test_resume_action_skips_a_case_that_already_succeeded():
    assert resume_action(_record(healthy_passed=True)) == "skip"


def test_resume_action_cleans_up_and_retries_a_case_that_previously_failed():
    assert resume_action(_record(healthy_passed=False)) == "cleanup_and_retry"


# ---------------------------------------------------------------------------
# is_rate_limited -- a disclosed heuristic, tested as exactly that
# ---------------------------------------------------------------------------


def test_is_rate_limited_matches_the_real_error_repr():
    assert is_rate_limited("execution loop raised: RateLimitError(\"Error code: 429 - ...\")")


def test_is_rate_limited_is_false_for_an_ordinary_failure():
    assert not is_rate_limited("execution loop ended with status='failed'")
    assert not is_rate_limited("verdict validation failed after retries: [...]")


# ---------------------------------------------------------------------------
# run_with_pacing -- the property a live run cannot cheaply prove.
# Every test below calls asyncio.run() directly on a small async body --
# no pytest-async plugin required, and no test here ever actually sleeps:
# sleep_fn is always a fake that records calls instead of waiting.
# ---------------------------------------------------------------------------


def test_paces_between_items_but_never_before_the_first_or_after_the_last():
    processed_items = []
    sleep_calls = []

    async def fake_sleep(seconds):
        sleep_calls.append(seconds)

    async def process(item):
        processed_items.append(item)
        return False

    async def run():
        return await run_with_pacing([1, 2, 3], process, pace_seconds=999, sleep_fn=fake_sleep)

    count = asyncio.run(run())

    assert processed_items == [1, 2, 3]
    assert count == 3
    assert sleep_calls == [999, 999]  # one fewer sleep than items processed


def test_stops_immediately_when_process_signals_true_no_further_items_attempted():
    processed_items = []

    async def fake_sleep(seconds):
        pass

    async def process(item):
        processed_items.append(item)
        return item == 3  # simulate a rate-limit-like stop signal on the 3rd item

    async def run():
        return await run_with_pacing([1, 2, 3, 4, 5, 6], process, pace_seconds=1, sleep_fn=fake_sleep)

    count = asyncio.run(run())

    assert processed_items == [1, 2, 3]  # 4, 5, and 6 were never even attempted
    assert count == 3


def test_an_ordinary_false_signal_does_not_stop_the_batch():
    processed_items = []

    async def fake_sleep(seconds):
        pass

    async def process(item):
        processed_items.append(item)
        return False  # every item "fails" in the ordinary sense, but none are fatal

    async def run():
        return await run_with_pacing([1, 2, 3], process, pace_seconds=1, sleep_fn=fake_sleep)

    count = asyncio.run(run())
    assert processed_items == [1, 2, 3]
    assert count == 3


def test_a_real_wait_never_actually_happens_in_this_test_suite():
    """Explicit, named proof that this suite does not really wait: pace_seconds
    is a full simulated hour, and the test still completes instantly, because
    sleep_fn is a fake recorder, never the real asyncio.sleep.
    """
    seen = []

    async def fake_sleep(seconds):
        seen.append(seconds)

    async def process(item):
        return False

    async def run():
        return await run_with_pacing([1, 2], process, pace_seconds=3600, sleep_fn=fake_sleep)

    asyncio.run(run())
    assert seen == [3600]


def test_empty_item_list_processes_nothing_and_never_sleeps():
    async def fake_sleep(seconds):
        raise AssertionError("should never be called with zero items")

    async def process(item):
        return False

    async def run():
        return await run_with_pacing([], process, pace_seconds=1, sleep_fn=fake_sleep)

    assert asyncio.run(run()) == 0


# ---------------------------------------------------------------------------
# Persistence round-trip
# ---------------------------------------------------------------------------


def test_resume_state_round_trips_through_real_json(tmp_path):
    path = tmp_path / "resume_state.json"
    state = {"golden_true_1": _record(healthy_passed=True, study_run_id="run-123")}
    save_resume_state(path, state)
    reloaded = load_resume_state(path)
    assert reloaded["golden_true_1"].study_run_id == "run-123"
    assert reloaded["golden_true_1"].healthy_passed


def test_load_resume_state_returns_empty_dict_when_no_file_exists(tmp_path):
    assert load_resume_state(tmp_path / "does_not_exist.json") == {}
