from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from data_pipeline.config import settings


def get_engine(url: str | None = None):
    return create_engine(url or settings.database_url)


_engine = get_engine()
SessionFactory = sessionmaker(bind=_engine)
