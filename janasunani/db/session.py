"""Async SQLAlchemy engine and session factory for the complaint store."""

from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from janasunani.config import settings
from janasunani.db.models import Base

engine = create_async_engine(settings.DB_URL)

AsyncSessionLocal = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def init_db() -> None:
    """Create all tables if they do not already exist."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def get_db():
    """Yield an async database session."""
    async with AsyncSessionLocal() as session:
        yield session
