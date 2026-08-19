from sqlalchemy import Engine

from data_pipeline.db.models import Base
from data_pipeline.db.session import get_engine
import agentic_core.db.models  # noqa: F401 -- registers Stage 5's tables onto the same Base.metadata


def create_schema(engine: Engine) -> None:
    Base.metadata.create_all(engine)


if __name__ == "__main__":
    create_schema(get_engine())
    print("Schema created.")
