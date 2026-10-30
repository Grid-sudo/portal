from __future__ import annotations

import asyncio
from datetime import UTC, datetime

from aiogram import Bot
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from redis.asyncio import Redis

from app.adapters.registry import build_adapters
from app.bot import build_dispatcher, set_bot_commands
from app.config import get_settings
from app.db import Database
from app.logging_setup import configure_logging
from app.scanner import Scanner, scheduled_scan
from app.storage.migrations import migrate_schema
from app.storage.models import Base


async def main() -> None:
    settings = get_settings()
    secrets = [
        settings.bot_token.get_secret_value(),
        settings.database_url,
        settings.redis_url,
    ]
    if settings.portals_api_key:
        portals_key = settings.portals_api_key.get_secret_value()
        if portals_key:
            secrets.append(portals_key)
    configure_logging(
        settings.log_level,
        settings.log_file,
        secrets=tuple(secrets),
    )
    database = Database(settings)
    redis = Redis.from_url(settings.redis_url, decode_responses=True)
    adapters = build_adapters(settings)
    scanner = Scanner(settings, database, redis, adapters)
    bot = Bot(token=settings.bot_token.get_secret_value())
    dispatcher = build_dispatcher(settings, database, scanner, adapters)

    async with database.engine.begin() as connection:
        await migrate_schema(connection)
        await connection.run_sync(Base.metadata.create_all)

    scheduler = AsyncIOScheduler(timezone=UTC)
    scheduler.add_job(
        scheduled_scan,
        "interval",
        seconds=settings.scan_interval_seconds,
        next_run_time=datetime.now(UTC),
        args=[scanner, bot],
        id="marketplace-scan",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    scheduler.start()
    try:
        await set_bot_commands(bot)
        await dispatcher.start_polling(bot)
    finally:
        scheduler.shutdown(wait=False)
        await redis.aclose()
        await database.close()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
