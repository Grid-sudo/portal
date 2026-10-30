from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum


class Marketplace(StrEnum):
    MRKT = "mrkt"
    PORTALS = "portals"
    TONNEL = "tonnel"
    TELEGRAM = "telegram"
    DEMO = "demo"


class RunMode(StrEnum):
    DEMO = "DEMO"
    LIVE = "LIVE"


class ListingStatus(StrEnum):
    ACTIVE = "active"
    SOLD = "sold"
    CANCELLED = "cancelled"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class Listing:
    marketplace: Marketplace
    listing_id: str
    nft_id: str | None
    collection: str
    price: Decimal
    currency: str
    url: str | None
    mode: RunMode = RunMode.LIVE
    model: str | None = None
    backdrop: str | None = None
    symbol: str | None = None
    status: ListingStatus = ListingStatus.ACTIVE
    discovered_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    observed_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def __post_init__(self) -> None:
        if not self.listing_id.strip():
            raise ValueError("listing_id must not be empty")
        if not self.collection.strip():
            raise ValueError("collection must not be empty")
        if self.price <= 0:
            raise ValueError("listing price must be greater than zero")
        if not self.currency.strip():
            raise ValueError("currency must not be empty")
        if self.discovered_at.tzinfo is None or self.observed_at.tzinfo is None:
            raise ValueError("listing timestamps must be timezone-aware")

    @property
    def natural_key(self) -> str:
        """Identify one marketplace listing; the same NFT may have listings elsewhere."""
        return f"{self.mode.value}:{self.marketplace.value}:{self.listing_id}"


@dataclass(frozen=True, slots=True)
class FloorSnapshot:
    collection: str
    dimension: str
    value: str
    currency: str
    floor: Decimal
    listing_count: int
    mode: RunMode = RunMode.LIVE
    recorded_at: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass(frozen=True, slots=True)
class Opportunity:
    key: str
    kind: str
    listing: Listing
    reference_price: Decimal | None
    gross_spread: Decimal | None = None
    gross_margin_percent: Decimal | None = None
    estimated_net: Decimal | None = None
    discount_percent: Decimal | None = None
    note: str = ""

