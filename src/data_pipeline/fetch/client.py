import logging

from tenacity import before_sleep_log, retry, stop_after_attempt, wait_exponential

logger = logging.getLogger(__name__)


class FetchError(Exception):
    """Raised when a yfinance fetch fails after all retries are exhausted."""


retry_on_failure = retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=4, max=60),
    reraise=True,
    before_sleep=before_sleep_log(logger, logging.WARNING),
)
