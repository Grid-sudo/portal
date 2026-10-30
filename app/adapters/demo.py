from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from app.adapters.base import AdapterState, AdapterStatus
from app.domain.models import Listing, ListingStatus, Marketplace, RunMode


class DemoAdapter:
    """Deterministic synthetic marketplace sample; never makes network requests."""

    marketplace = Marketplace.DEMO

    async def status(self) -> AdapterStatus:
        return AdapterStatus(
            marketplace=Marketplace.DEMO,
            state=AdapterState.DEMO,
            message="Synthetic listings only. Prices and IDs are invented for local testing.",
        )

    async def fetch_listings(self) -> list[Listing]:
        now = datetime.now(UTC)
        sample = [
            (Marketplace.MRKT, "mrkt-demo-001", "demo-gift-001", "35"),
            (Marketplace.PORTALS, "portals-demo-001", "demo-gift-001", "42"),
            (Marketplace.TONNEL, "tonnel-demo-001", "demo-gift-001", "40"),
            (Marketplace.PORTALS, "portals-demo-002", "demo-gift-002", "50"),
            (Marketplace.TONNEL, "tonnel-demo-003", "demo-gift-003", "51"),
        ]
        return [
            Listing(
                marketplace=venue,
                listing_id=listing_id,
                nft_id=nft_id,
                collection="DEMO Vintage Cigar",
                price=Decimal(price),
                currency="TON",
                url=None,
                mode=RunMode.DEMO,
                model="Demo Blue Hue",
                backdrop="Demo Pacific Cyan",
                symbol="Demo Ball",
                status=ListingStatus.ACTIVE,
                discovered_at=now,
                observed_at=now,
            )
            for venue, listing_id, nft_id, price in sample
        ]
