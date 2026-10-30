from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.models import FloorSnapshot, Listing, Opportunity, RunMode
from app.storage.models import (
    FloorSnapshotRow,
    ListingRow,
    NotificationDeliveryRow,
    OpportunityRow,
    SubscriberRow,
)


def _listing_values(item: Listing) -> dict:
    return {
        "mode": item.mode.value,
        "marketplace": item.marketplace.value,
        "listing_id": item.listing_id,
        "nft_id": item.nft_id,
        "collection": item.collection,
        "model": item.model,
        "backdrop": item.backdrop,
        "symbol": item.symbol,
        "price": item.price,
        "currency": item.currency.upper(),
        "url": item.url,
        "status": item.status.value,
        "discovered_at": item.discovered_at,
        "observed_at": item.observed_at,
    }


def _as_listing(row: ListingRow) -> Listing:
    from app.domain.models import ListingStatus, Marketplace

    return Listing(
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
        status=ListingStatus(row.status),
        discovered_at=row.discovered_at,
        observed_at=row.observed_at,
    )


class Repository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def upsert_listings(self, listings: list[Listing]) -> None:
        now = datetime.now(UTC)
        for item in listings:
            if item.observed_at > now:
                raise ValueError("observed_at cannot be in the future")
        if not listings:
            return

        rows_by_key = {item.natural_key: item for item in listings}
        values = [_listing_values(item) for item in rows_by_key.values()]
        statement = pg_insert(ListingRow).values(values)
        updates = {
            field: getattr(statement.excluded, field)
            for field in values[0]
            if field not in {"mode", "marketplace", "listing_id", "discovered_at"}
        }
        await self.session.execute(
            statement.on_conflict_do_update(
                index_elements=["mode", "marketplace", "listing_id"],
                set_=updates,
            )
        )

    async def load_active_listings(self, max_age_seconds: int, mode: RunMode) -> list[Listing]:
        cutoff = datetime.now(UTC) - timedelta(seconds=max_age_seconds)
        rows = await self.session.scalars(
            select(ListingRow).where(
                ListingRow.status == "active",
                ListingRow.observed_at >= cutoff,
                ListingRow.mode == mode.value,
            )
        )
        return [_as_listing(row) for row in rows]

    async def add_floor_snapshots(self, snapshots: list[FloorSnapshot]) -> None:
        self.session.add_all(
            [
                FloorSnapshotRow(
                    mode=row.mode.value,
                    collection=row.collection,
                    dimension=row.dimension,
                    value=row.value,
                    currency=row.currency,
                    floor=row.floor,
                    listing_count=row.listing_count,
                    recorded_at=row.recorded_at,
                )
                for row in snapshots
            ]
        )

    async def latest_floors(self, mode: RunMode, limit: int = 30) -> list[FloorSnapshotRow]:
        latest_ids = (
            select(func.max(FloorSnapshotRow.id))
            .where(FloorSnapshotRow.mode == mode.value)
            .group_by(
                FloorSnapshotRow.mode,
                FloorSnapshotRow.collection,
                FloorSnapshotRow.dimension,
                FloorSnapshotRow.value,
                FloorSnapshotRow.currency,
            )
        )
        statement = (
            select(FloorSnapshotRow)
            .where(FloorSnapshotRow.id.in_(latest_ids), FloorSnapshotRow.mode == mode.value)
            .order_by(FloorSnapshotRow.collection, FloorSnapshotRow.dimension, FloorSnapshotRow.value)
            .limit(limit)
        )
        return list(await self.session.scalars(statement))

    async def save_new_opportunities(self, opportunities: list[Opportunity]) -> list[Opportunity]:
        unique = {item.key: item for item in opportunities}
        if not unique:
            return []
        now = datetime.now(UTC)
        values = []
        for item in unique.values():
            row = item.listing
            values.append(
                {
                    "key": item.key,
                    "mode": row.mode.value,
                    "kind": item.kind,
                    "marketplace": row.marketplace.value,
                    "listing_id": row.listing_id,
                    "nft_id": row.nft_id,
                    "collection": row.collection,
                    "model": row.model,
                    "backdrop": row.backdrop,
                    "symbol": row.symbol,
                    "price": row.price,
                    "reference_price": item.reference_price,
                    "gross_spread": item.gross_spread,
                    "gross_margin_percent": item.gross_margin_percent,
                    "estimated_net": item.estimated_net,
                    "currency": row.currency,
                    "discount_percent": item.discount_percent,
                    "note": item.note,
                    "url": row.url,
                    "detected_at": now,
                }
            )
        statement = (
            pg_insert(OpportunityRow)
            .values(values)
            .on_conflict_do_nothing(index_elements=["key"])
            .returning(OpportunityRow.key)
        )
        inserted_keys = set(await self.session.scalars(statement))
        return [item for key, item in unique.items() if key in inserted_keys]

    async def queue_notifications(
        self, opportunity_keys: list[str], chat_ids: list[int]
    ) -> None:
        if not opportunity_keys or not chat_ids:
            return
        now = datetime.now(UTC)
        opportunities = await self.session.execute(
            select(OpportunityRow.key, OpportunityRow.mode).where(
                OpportunityRow.key.in_(set(opportunity_keys))
            )
        )
        modes = dict(opportunities.all())
        values = [
            {
                "chat_id": chat_id,
                "opportunity_key": key,
                "mode": modes[key],
                "status": "pending",
                "attempts": 0,
                "created_at": now,
            }
            for key in modes
            for chat_id in set(chat_ids)
        ]
        if values:
            statement = pg_insert(NotificationDeliveryRow).values(values)
            await self.session.execute(
                statement.on_conflict_do_nothing(
                    index_elements=["chat_id", "opportunity_key"]
                )
            )

    async def queue_recent_for_chat(self, chat_id: int, mode: RunMode, limit: int = 10) -> None:
        keys = list(
            await self.session.scalars(
                select(OpportunityRow.key)
                .where(OpportunityRow.mode == mode.value)
                .order_by(OpportunityRow.detected_at.desc())
                .limit(limit)
            )
        )
        await self.queue_notifications(keys, [chat_id])

    async def pending_notifications(
        self, mode: RunMode, limit: int = 100
    ) -> list[tuple[NotificationDeliveryRow, OpportunityRow]]:
        rows = await self.session.execute(
            select(NotificationDeliveryRow, OpportunityRow)
            .join(OpportunityRow, OpportunityRow.key == NotificationDeliveryRow.opportunity_key)
            .where(
                NotificationDeliveryRow.mode == mode.value,
                NotificationDeliveryRow.status == "pending",
                NotificationDeliveryRow.attempts < 5,
            )
            .order_by(NotificationDeliveryRow.created_at)
            .limit(limit)
        )
        return list(rows.tuples())

    async def record_notification_attempt(
        self, chat_id: int, opportunity_key: str, error_code: str | None = None
    ) -> None:
        row = await self.session.get(NotificationDeliveryRow, (chat_id, opportunity_key))
        if row is None:
            return
        row.attempts += 1
        row.last_attempt_at = datetime.now(UTC)
        row.last_error = error_code[:96] if error_code else None
        if error_code is None:
            row.status = "sent"
            row.sent_at = row.last_attempt_at

    async def recent_opportunities(self, mode: RunMode, limit: int = 10) -> list[OpportunityRow]:
        return list(
            await self.session.scalars(
                select(OpportunityRow)
                .where(OpportunityRow.mode == mode.value)
                .order_by(OpportunityRow.detected_at.desc())
                .limit(limit)
            )
        )

    async def recent_listings(self, mode: RunMode, limit: int = 20) -> list[ListingRow]:
        return list(
            await self.session.scalars(
                select(ListingRow)
                .where(ListingRow.mode == mode.value)
                .order_by(ListingRow.observed_at.desc())
                .limit(limit)
            )
        )

    async def subscribe(self, chat_id: int) -> None:
        # ON CONFLICT keeps concurrent /start updates from the same chat from racing on the primary key.
        statement = (
            pg_insert(SubscriberRow)
            .values(chat_id=chat_id, created_at=datetime.now(UTC))
            .on_conflict_do_nothing(index_elements=[SubscriberRow.chat_id])
        )
        await self.session.execute(statement)

    async def unsubscribe(self, chat_id: int) -> None:
        row = await self.session.get(SubscriberRow, chat_id)
        if row is not None:
            await self.session.delete(row)
        pending = await self.session.scalars(
            select(NotificationDeliveryRow).where(
                NotificationDeliveryRow.chat_id == chat_id,
                NotificationDeliveryRow.status == "pending",
            )
        )
        for delivery in pending:
            await self.session.delete(delivery)

    async def subscribers(self) -> list[int]:
        return list(await self.session.scalars(select(SubscriberRow.chat_id)))

    async def statistics(self, mode: RunMode) -> dict[str, int | Decimal | None]:
        listing_count = await self.session.scalar(
            select(func.count()).select_from(ListingRow).where(ListingRow.mode == mode.value)
        )
        opportunity_count = await self.session.scalar(
            select(func.count()).select_from(OpportunityRow).where(OpportunityRow.mode == mode.value)
        )
        avg_discount = await self.session.scalar(
            select(func.avg(OpportunityRow.discount_percent)).where(
                OpportunityRow.discount_percent.is_not(None), OpportunityRow.mode == mode.value
            )
        )
        return {
            "listings": int(listing_count or 0),
            "opportunities": int(opportunity_count or 0),
            "average_discount": Decimal(avg_discount) if avg_discount is not None else None,
        }

    async def listing_distribution(self, mode: RunMode) -> list[tuple[str, int]]:
        rows = await self.session.execute(
            select(ListingRow.marketplace, func.count())
            .select_from(ListingRow)
            .where(ListingRow.mode == mode.value)
            .group_by(ListingRow.marketplace)
            .order_by(ListingRow.marketplace)
        )
        return [(marketplace, int(count)) for marketplace, count in rows]
