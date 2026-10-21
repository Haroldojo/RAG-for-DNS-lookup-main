"""SQLAlchemy session construction. Schema creation remains Alembic's responsibility."""

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings
from app.database.service import PersistenceError, PersistenceService


def get_persistence_service() -> PersistenceService:
    url = get_settings().database_url
    if not url:
        raise PersistenceError("database_not_configured")
    engine = create_engine(url, pool_pre_ping=True)
    return PersistenceService(sessionmaker(bind=engine, class_=Session, expire_on_commit=False))
