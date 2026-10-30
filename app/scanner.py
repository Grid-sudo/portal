from __future__ import annotations

import asyncio
import logging
import time
import uuid
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

from aiogram import Bot
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from redis.asyncio import Redis

from app.adapters.base import AdapterState, AdapterStatus, MarketplaceAdapter
from app.config import Settings
from app.db import Database
from app.domain.analysis import (
    decimal_text,
    detect_arbitrage_opportunities,
    detect_floor_opportunities,
    deduplicate_listings,
    trait_frequency,
)
from app.domain.models import (
    FloorSnapshot,
    Listing,
    ListingStatus,
    Marketplace,
    Opportunity,
    RunMode,
)
from app.scan_lock import ScanLock
from app.storage.models import NotificationDeliveryRow, OpportunityRow
from app.storage.repository import Repository

logger = logging.getLogger(__name__)
LOCK_KEY = "telegram-gift-monitor:scan-lock"


@dataclass(frozen=True, slots=True)
class ScanResult:
    skipped: bool
    fetched_listings: int
    fresh_listings: int
    new_opportunities: int
    statuses: tuple[AdapterStatus, ...]


def build_floor_snapshots(listings: list[Listing]) -> list[FloorSnapshot]:
    groups: dict[tuple[RunMode, str, str, str, str], list[Listing]] = defaultdict(list)
    dimensions = (("model", "model"), ("backdrop", "backdrop"), ("symbol", "symbol"))
    for listing in listings:
        groups[
            (listing.mode, listing.collection, "collection", listing.collection, listing.currency.upper())
        ].append(listing)
        for dimension, attr in dimensions:
            value = getattr(listing, attr)
            if value:
                groups[(listing.mode, listing.collection, dimension, value, listing.currency.upper())].append(
                    listing
                )

    recorded_at = datetime.now(UTC)
    snapshots = []
    for (mode, collection, dimension, value, currency), rows in groups.items():
        snapshots.append(
            FloorSnapshot(
                collection=collection,
                dimension=dimension,
                value=value,
                currency=currency,
                floor=min(row.price for row in rows),
                listing_count=len(rows),
                mode=mode,
                recorded_at=recorded_at,
            )
        )
    return snapshots


def format_opportunity(item: Opportunity) -> tuple[str, InlineKeyboardMarkup | None]:
    row = item.listing
    trait_lines = [f"{name}: {value}" for name, value in (
        ("Модель", row.model), ("Фон", row.backdrop), ("Символ", row.symbol)
    ) if value]
    text = [
        "🧪 DEMO — синтетические данные" if row.mode == RunMode.DEMO else "LIVE",
        "🔥 Потенциальная возможность",
        f"{row.collection}" + (f" #{row.nft_id}" if row.nft_id else ""),
        f"Цена: {decimal_text(row.price)} {row.currency.upper()}",
        f"Площадка: {row.marketplace.value}",
    ]
    if item.reference_price is not None:
        text.append(f"Сравнение: {decimal_text(item.reference_price)} {row.currency.upper()}")
    if item.discount_percent is not None:
        text.append(f"Отклонение: {item.discount_percent.quantize(Decimal('0.1'))}%")
    if item.gross_spread is not None:
        margin = (
            f" ({item.gross_margin_percent.quantize(Decimal('0.1'))}% от цены продажи)"
            if item.gross_margin_percent is not None
            else ""
        )
        text.append(
            f"Валовый спред до комиссий: {decimal_text(item.gross_spread)} "
            f"{row.currency.upper()}{margin}"
        )
    if item.estimated_net is not None:
        text.append(
            f"Оценка после известных комиссий: {decimal_text(item.estimated_net)} {row.currency.upper()}"
        )
    text.extend(trait_lines)
    text.append(item.note)
    keyboard = None
    if row.url:
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="Открыть объявление", url=row.url)]]
        )
    return "\n".join(text), keyboard


class Scanner:
    def __init__(
        self,
        settings: Settings,
        database: Database,
        redis: Redis,
        adapters: list[MarketplaceAdapter],
    ):
        self.settings = settings
        self.database = database
        self.redis = redis
        self.adapters = adapters
        self.scan_lock = ScanLock(redis, LOCK_KEY, ttl_seconds=120)

    async def _collect(self) -> tuple[list[Listing], tuple[AdapterStatus, ...]]:
        listings: list[Listing] = []
        statuses: list[AdapterStatus] = []
        for adapter in self.adapters:
            try:
                status = await adapter.status()
            except Exception as exc:
                logger.exception("Marketplace adapter %s failed", adapter.marketplace.value)
                statuses.append(
                    AdapterStatus(
                        adapter.marketplace,
                        AdapterState.UNAVAILABLE,
                        f"Adapter error: {type(exc).__name__}",
                    )
                )
                continue
            if not status.enabled:
                statuses.append(status)
                continue
            try:
                listings.extend(await adapter.fetch_listings())
                statuses.append(status)
            except Exception as exc:
                logger.exception("Marketplace adapter %s failed", adapter.marketplace.value)
                statuses.append(
                    AdapterStatus(
                        adapter.marketplace,
                        AdapterState.UNAVAILABLE,
                        f"Adapter error: {type(exc).__name__}",
                    )
                )
        return deduplicate_listings(listings), tuple(statuses)

    async def scan(self, bot: Bot | None = None) -> ScanResult:
        lock_token = uuid.uuid4().hex
        if not await self.scan_lock.acquire(lock_token):
            return ScanResult(True, 0, 0, 0, ())
        try:
            fetched, statuses = await self._collect()
            async with self.database.sessions() as session:
                repository = Repository(session)
                await repository.upsert_listings(fetched)
                fresh = await repository.load_active_listings(
                    self.settings.listing_max_age_seconds, self.settings.app_mode
                )
                floors = build_floor_snapshots(fresh)
                await repository.add_floor_snapshots(floors)
                opportunities = detect_floor_opportunities(
                    fresh,
                    self.settings.min_discount_percent,
                    self.settings.min_trait_comparables,
                )
                opportunities.extend(
                    detect_arbitrage_opportunities(
                        fresh,
                        fees={},
                        max_age_seconds=self.settings.listing_max_age_seconds,
                        now_epoch=time.time(),
                    )
                )
                frequencies = trait_frequency(fresh)
                enriched: list[Opportunity] = []
                for opportunity in opportunities:
                    listing = opportunity.listing
                    observed = []
                    for name, value in (
                        ("model", listing.model),
                        ("backdrop", listing.backdrop),
                        ("symbol", listing.symbol),
                    ):
                        if value:
                            count = frequencies.get(
                                (listing.mode, listing.collection.casefold(), name, value.casefold())
                            )
                            if count:
                                observed.append(f"{name} {count[0]}/{count[1]} observed listings")
                    if observed:
                        opportunity = Opportunity(
                            key=opportunity.key,
                            kind=opportunity.kind,
                            listing=opportunity.listing,
                            reference_price=opportunity.reference_price,
                            gross_spread=opportunity.gross_spread,
                            gross_margin_percent=opportunity.gross_margin_percent,
                            estimated_net=opportunity.estimated_net,
                            discount_percent=opportunity.discount_percent,
                            note=(
                                f"{opportunity.note} Observed trait counts: "
                                f"{', '.join(observed)}; these are not total-supply rarity or a value guarantee."
                            ),
                        )
                    enriched.append(opportunity)
                opportunities = enriched
                new_opportunities = await repository.save_new_opportunities(opportunities)
                chats = await repository.subscribers() if bot and new_opportunities else []
                if new_opportunities and chats:
                    await repository.queue_notifications(
                        [item.key for item in new_opportunities], chats
                    )
                pending = (
                    await repository.pending_notifications(self.settings.app_mode)
                    if bot
                    else []
                )
                await session.commit()

            if bot:
                await self._deliver_pending(bot, pending)
            return ScanResult(
                False, len(fetched), len(fresh), len(new_opportunities), statuses
            )
        finally:
            await self.scan_lock.release(lock_token)

    async def notify_recent(self, bot: Bot, chat_id: int, limit: int = 10) -> None:
        """Queue recent alerts for a new subscriber, then send through the same deduplicated outbox."""
        async with self.database.sessions() as session:
            repository = Repository(session)
            await repository.queue_recent_for_chat(chat_id, self.settings.app_mode, limit)
            pending = await repository.pending_notifications(self.settings.app_mode)
            await session.commit()
        await self._deliver_pending(bot, pending)

    async def _deliver_pending(
        self,
        bot: Bot,
        pending: list[tuple[NotificationDeliveryRow, OpportunityRow]],
    ) -> None:
        for delivery, row in pending:
            listing = Listing(
                marketplace=Marketplace(row.marketplace),
                listing_id=row.listing_id,
                nft_id=row.nft_id,
                collection=row.collection,
                price=Decimal(row.price),
                currency=row.currency,
                url=row.url,
                mode=RunMode(row.mode),
                model=row.model,
                backdrop=row.backdrop,
                symbol=row.symbol,
                status=ListingStatus.ACTIVE,
                discovered_at=row.detected_at,
                observed_at=row.detected_at,
            )
            opportunity = Opportunity(
                key=row.key,
                kind=row.kind,
                listing=listing,
                reference_price=Decimal(row.reference_price) if row.reference_price is not None else None,
                gross_spread=Decimal(row.gross_spread) if row.gross_spread is not None else None,
                gross_margin_percent=(
                    Decimal(row.gross_margin_percent)
                    if row.gross_margin_percent is not None
                    else None
                ),
                estimated_net=Decimal(row.estimated_net) if row.estimated_net is not None else None,
                discount_percent=(
                    Decimal(row.discount_percent) if row.discount_percent is not None else None
                ),
                note=row.note,
            )
            error_code = None
            try:
                text, keyboard = format_opportunity(opportunity)
                await bot.send_message(delivery.chat_id, text, reply_markup=keyboard)
            except Exception as exc:
                error_code = type(exc).__name__
                logger.exception("Notification delivery failed for chat %s", delivery.chat_id)
            async with self.database.sessions() as session:
                await Repository(session).record_notification_attempt(
                    delivery.chat_id, row.key, error_code
                )
                await session.commit()

async def scheduled_scan(scanner: Scanner, bot: Bot) -> None:
    try:
        result = await scanner.scan(bot)
        logger.info(
            "Scan finished: listings=%s fresh=%s opportunities=%s",
            result.fetched_listings,
            result.fresh_listings,
            result.new_opportunities,
        )
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.exception("Scheduled scan failed")
