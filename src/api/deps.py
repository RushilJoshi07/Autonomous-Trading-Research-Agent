"""Request-scoped DB session dependency for FastAPI routes.

Every other caller in this codebase opens its own `with SessionFactory() as
session:` block per function (agentic_core/charter.py, hypothesis.py,
verdict.py, mcp_tools/server.py) -- the right pattern for a plain function
that isn't part of a request/response cycle a framework manages. Routes use
a dependency instead: FastAPI calls this generator, hands the route the
yielded session, and resumes it (running the `finally`) once the route
returns. The payoff is in tests/api/conftest.py -- app.dependency_overrides
swaps in the test session with no monkeypatching of an import path, unlike
tests/agentic_core/conftest.py's SessionFactory patching, which is only
necessary there because those modules import SessionFactory directly.
"""

from collections.abc import Iterator

from sqlalchemy.orm import Session

from data_pipeline.db.session import SessionFactory


def get_db() -> Iterator[Session]:
    session = SessionFactory()
    try:
        yield session
    finally:
        session.close()
