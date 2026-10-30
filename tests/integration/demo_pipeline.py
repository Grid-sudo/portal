"""Disposable Compose-network smoke test for PostgreSQL, Redis, and the scanner."""

from __future__ import annotations

import asyncio
import os
import re
import sys
from decimal import Decimal
from urllib.parse import quote

from pydantic import SecretStr
from redis.asyncio import Redis
from sqlalchemy import func, select, text

from app.adapters.demo import DemoAdapter
from app.adapters.registry import build_adapters
from app.config import Settings
from app.db import Database
from app.domain.models import RunMode
from app.scanner import Scanner
from app.storage.migrations import migrate_schema
from app.storage.models import (
    Base,
    ListingRow,
    NotificationDeliveryRow,
    OpportunityRow,
)
from app.storage.repository import Repository

TEST_DATABASE_NAME = "nft_monitor_e2e_test"
TEST_CHAT_ID = 9900000001


class RecordingBot:
    """Captures Telegram payloads locally and can fail exactly one send."""

    def __init__(self, fail_first: bool = False):
        self.fail_first = fail_first
        self.attempts = 0
        self.sent: list[tuple[int, str]] = []

    async def send_message(self, chat_id: int, text: str, reply_markup=None) -> None:
        self.attempts += 1
        if self.fail_first:
            self.fail_first = False
            raise OSError("simulated Telegram delivery failure")
        self.sent.append((chat_id, text))


def settings_for_test() -> Settings:
    password = os.environ.get("POSTGRES_PASSWORD", "")
    if not re.fullmatch(r"[a-f0-9]{64}", password):
        raise RuntimeError("Set a new 64-character hex POSTGRES_PASSWORD in local .env first.")
    database_url = (
        f"postgresql+asyncpg://giftbot:{quote(password, safe='')}@"
        f"{os.environ.get('NFT_TEST_DATABASE_HOST', 'postgres')}:5432/"
        f"{TEST_DATABASE_NAME}"
    )
    return Settings(
        bot_token=SecretStr("123456:LOCAL_TEST_ONLY"),
        app_mode=RunMode.DEMO,
        database_url=database_url,
        redis_url="redis://redis:6379/0",
        listing_max_age_seconds=300,
        min_discount_percent=Decimal("10"),
        min_trait_comparables=3,
    )


async def prepare_database(database: Database) -> None:
    async with database.engine.begin() as connection:
        await migrate_schema(connection)
        await connection.run_sync(Base.metadata.create_all)
        await connection.execute(
            text(
                "TRUNCATE TABLE notification_deliveries, opportunities, floor_snapshots, "
                "listings, subscribers RESTART IDENTITY CASCADE"
            )
        )
    async with database.sessions() as session:
        await Repository(session).subscribe(TEST_CHAT_ID)
        await session.commit()


async def assert_redis_outage(settings: Settings) -> None:
    database = Database(settings)
    redis = Redis.from_url(
        settings.redis_url,
        decode_responses=True,
        socket_connect_timeout=0.25,
        socket_timeout=0.25,
    )
    try:
        scanner = Scanner(settings, database, redis, [DemoAdapter()])
        result = await scanner.scan()
        if result.skipped or result.fetched_listings != 5 or result.new_opportunities != 0:
            raise AssertionError(f"unexpected result during Redis outage: {result}")
        print("REDIS_OUTAGE_SCAN_OK: local lock ran scan; 5 rows fetched; no duplicate opportunities")
    finally:
        await redis.aclose()
        await database.close()


async def run_pipeline(settings: Settings) -> None:
    database = Database(settings)
    restarted_database: Database | None = None
    redis = Redis.from_url(
        settings.redis_url,
        decode_responses=True,
        socket_connect_timeout=1,
        socket_timeout=1,
    )
    try:
        await prepare_database(database)
        first_bot = RecordingBot(fail_first=True)
        first_scanner = Scanner(settings, database, redis, [DemoAdapter()])
        first_result = await first_scanner.scan(first_bot)
        if first_result.skipped or first_result.fetched_listings != 5:
            raise AssertionError(f"unexpected first scan result: {first_result}")
        if first_result.new_opportunities < 1:
            raise AssertionError("DEMO scan did not detect any opportunities")
        if first_bot.attempts != first_result.new_opportunities:
            raise AssertionError("first scan did not enqueue one notification per new opportunity")
        if len(first_bot.sent) != first_result.new_opportunities - 1:
            raise AssertionError("simulated first delivery failure was not recorded")

        async with database.sessions() as session:
            demo_listings = await session.scalar(
                select(func.count()).select_from(ListingRow).where(ListingRow.mode == "DEMO")
            )
            live_listings = await session.scalar(
                select(func.count()).select_from(ListingRow).where(ListingRow.mode == "LIVE")
            )
            deliveries = list(await session.scalars(select(NotificationDeliveryRow)))
        if demo_listings != 5 or live_listings != 0:
            raise AssertionError(f"mode leakage or duplicate rows: DEMO={demo_listings}, LIVE={live_listings}")
        if sum(row.status == "pending" for row in deliveries) != 1:
            raise AssertionError("failed send did not leave exactly one pending outbox row")

        await database.close()
        restarted_database = Database(settings)
        retry_bot = RecordingBot()
        restarted_scanner = Scanner(settings, restarted_database, redis, [DemoAdapter()])
        retry_result = await restarted_scanner.scan(retry_bot)
        if retry_result.new_opportunities != 0 or len(retry_bot.sent) != 1:
            raise AssertionError("restart did not retry only the pending alert")

        third_result = await restarted_scanner.scan(retry_bot)
        if third_result.new_opportunities != 0 or retry_bot.attempts != 1:
            raise AssertionError("repeated scan sent a duplicate notification")

        async with restarted_database.sessions() as session:
            delivery_rows = list(await session.scalars(select(NotificationDeliveryRow)))
            demo_opportunities = await session.scalar(
                select(func.count()).select_from(OpportunityRow).where(OpportunityRow.mode == "DEMO")
            )
            live_opportunities = await session.scalar(
                select(func.count()).select_from(OpportunityRow).where(OpportunityRow.mode == "LIVE")
            )
        if not delivery_rows or any(row.status != "sent" for row in delivery_rows):
            raise AssertionError("outbox still has pending deliveries after retry")
        if demo_opportunities != first_result.new_opportunities or live_opportunities != 0:
            raise AssertionError("opportunities were duplicated or leaked into LIVE")

        live_settings = settings.model_copy(update={"app_mode": RunMode.LIVE})
        live_scanner = Scanner(live_settings, restarted_database, redis, build_adapters(live_settings))
        live_result = await live_scanner.scan()
        if live_result.fetched_listings != 0 or live_result.new_opportunities != 0:
            raise AssertionError(f"LIVE scanner returned DEMO data: {live_result}")

        print(
            "DEMO_PIPELINE_OK: fetched=5; "
            f"opportunities={first_result.new_opportunities}; "
            "restart retried 1 failed alert; repeated scan sent 0 duplicates; LIVE fetched=0"
        )
    finally:
        await redis.aclose()
        await database.close()
        if restarted_database is not None:
            await restarted_database.close()


async def main() -> None:
    settings = settings_for_test()
    if len(sys.argv) > 1 and sys.argv[1] == "--redis-outage":
        await assert_redis_outage(settings)
    else:
        await run_pipeline(settings)


if __name__ == "__main__":
    asyncio.run(main())
