from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, Integer, Numeric, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class ListingRow(Base):
    __tablename__ = "listings"
    __table_args__ = (
        Index("ix_listing_nft_currency", "nft_id", "currency"),
        Index("ix_listing_collection_currency", "collection", "currency"),
    )

    mode: Mapped[str] = mapped_column(String(8), primary_key=True)
    marketplace: Mapped[str] = mapped_column(String(32), primary_key=True)
    listing_id: Mapped[str] = mapped_column(String(256), primary_key=True)
    nft_id: Mapped[str | None] = mapped_column(String(256), nullable=True)
    collection: Mapped[str] = mapped_column(String(256), nullable=False)
    model: Mapped[str | None] = mapped_column(String(256), nullable=True)
    backdrop: Mapped[str | None] = mapped_column(String(256), nullable=True)
    symbol: Mapped[str | None] = mapped_column(String(256), nullable=True)
    price: Mapped[Decimal] = mapped_column(Numeric(30, 9), nullable=False)
    currency: Mapped[str] = mapped_column(String(16), nullable=False)
    url: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    discovered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class FloorSnapshotRow(Base):
    __tablename__ = "floor_snapshots"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    mode: Mapped[str] = mapped_column(String(8), nullable=False)
    collection: Mapped[str] = mapped_column(String(256), nullable=False)
    dimension: Mapped[str] = mapped_column(String(32), nullable=False)
    value: Mapped[str] = mapped_column(String(256), nullable=False)
    currency: Mapped[str] = mapped_column(String(16), nullable=False)
    floor: Mapped[Decimal] = mapped_column(Numeric(30, 9), nullable=False)
    listing_count: Mapped[int] = mapped_column(Integer, nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class OpportunityRow(Base):
    __tablename__ = "opportunities"

    key: Mapped[str] = mapped_column(String(768), primary_key=True)
    mode: Mapped[str] = mapped_column(String(8), nullable=False)
    kind: Mapped[str] = mapped_column(String(40), nullable=False)
    marketplace: Mapped[str] = mapped_column(String(32), nullable=False)
    listing_id: Mapped[str] = mapped_column(String(256), nullable=False)
    nft_id: Mapped[str | None] = mapped_column(String(256), nullable=True)
    collection: Mapped[str] = mapped_column(String(256), nullable=False)
    model: Mapped[str | None] = mapped_column(String(256), nullable=True)
    backdrop: Mapped[str | None] = mapped_column(String(256), nullable=True)
    symbol: Mapped[str | None] = mapped_column(String(256), nullable=True)
    price: Mapped[Decimal] = mapped_column(Numeric(30, 9), nullable=False)
    reference_price: Mapped[Decimal | None] = mapped_column(Numeric(30, 9), nullable=True)
    gross_spread: Mapped[Decimal | None] = mapped_column(Numeric(30, 9), nullable=True)
    gross_margin_percent: Mapped[Decimal | None] = mapped_column(Numeric(10, 4), nullable=True)
    estimated_net: Mapped[Decimal | None] = mapped_column(Numeric(30, 9), nullable=True)
    currency: Mapped[str] = mapped_column(String(16), nullable=False)
    discount_percent: Mapped[Decimal | None] = mapped_column(Numeric(10, 4), nullable=True)
    note: Mapped[str] = mapped_column(Text, nullable=False)
    url: Mapped[str | None] = mapped_column(Text, nullable=True)
    detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class NotificationDeliveryRow(Base):
    __tablename__ = "notification_deliveries"

    chat_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    opportunity_key: Mapped[str] = mapped_column(
        String(768), ForeignKey("opportunities.key", ondelete="CASCADE"), primary_key=True
    )
    mode: Mapped[str] = mapped_column(String(8), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(String(96), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class SubscriberRow(Base):
    __tablename__ = "subscribers"

    chat_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
