from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import Settings


class Database:
    def __init__(self, settings: Settings):
        self.engine = create_async_engine(settings.database_url, pool_pre_ping=True)
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False, class_=AsyncSession)

    async def close(self) -> None:
        await self.engine.dispose()
