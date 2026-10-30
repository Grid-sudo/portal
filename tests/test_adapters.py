import asyncio
from dataclasses import replace
from decimal import Decimal
from types import SimpleNamespace
import unittest

from app.adapters.base import AdapterState, AdapterUnavailableError
from app.adapters.demo import DemoAdapter
from app.domain.analysis import (
    deduplicate_listings,
    detect_arbitrage_opportunities,
    detect_floor_opportunities,
)
from app.adapters.mrkt import MRKTAdapter
from app.adapters.portals import PortalsAdapter
from app.adapters.registry import build_adapters
from app.adapters.tonnel import TonnelAdapter
from app.domain.models import Marketplace
from app.domain.models import RunMode


class AdapterAvailabilityTests(unittest.TestCase):
    def test_live_registry_never_includes_the_demo_source(self) -> None:
        live = build_adapters(SimpleNamespace(app_mode=RunMode.LIVE))
        demo = build_adapters(SimpleNamespace(app_mode=RunMode.DEMO))
        self.assertNotIn(Marketplace.DEMO, [adapter.marketplace for adapter in live])
        self.assertIn(Marketplace.DEMO, [adapter.marketplace for adapter in demo])

    def test_sources_are_separate_and_explicitly_unavailable(self) -> None:
        async def inspect() -> None:
            adapters = [MRKTAdapter(), PortalsAdapter(), TonnelAdapter()]
            self.assertEqual(
                [adapter.marketplace for adapter in adapters],
                [Marketplace.MRKT, Marketplace.PORTALS, Marketplace.TONNEL],
            )
            for adapter in adapters:
                status = await adapter.status()
                self.assertIn(status.state, (AdapterState.NOT_CONFIGURED, AdapterState.UNAVAILABLE))
                self.assertFalse(status.enabled)
                self.assertTrue(status.message)
                with self.assertRaises(AdapterUnavailableError):
                    await adapter.fetch_listings()

        asyncio.run(inspect())

    def test_demo_has_explicit_synthetic_data_and_produces_comparisons(self) -> None:
        async def inspect() -> None:
            adapter = DemoAdapter()
            status = await adapter.status()
            listings = await adapter.fetch_listings()
            self.assertEqual(status.state, AdapterState.DEMO)
            self.assertEqual(len(listings), 5)
            self.assertEqual(len(deduplicate_listings([*listings, *listings])), 5)
            self.assertTrue(all(row.mode == RunMode.DEMO for row in listings))
            self.assertTrue(all(row.url is None for row in listings))
            self.assertTrue(all(row.collection.startswith("DEMO ") for row in listings))
            floors = detect_floor_opportunities(
                listings, min_discount_percent=Decimal("10")
            )
            arbitrage = detect_arbitrage_opportunities(
                listings, fees={}, max_age_seconds=300, now_epoch=listings[0].observed_at.timestamp()
            )
            self.assertTrue(any(row.listing.mode == RunMode.DEMO for row in floors))
            self.assertEqual(len(arbitrage), 3)
            self.assertEqual(
                {row.gross_spread for row in arbitrage},
                {Decimal("7"), Decimal("5"), Decimal("2")},
            )
            margin = next(row for row in arbitrage if row.gross_spread == Decimal("7"))
            self.assertEqual(margin.gross_margin_percent, Decimal("7") * 100 / Decimal("42"))
            # A different run mode with a deliberately low price must not affect DEMO floors or arb.
            live_copy = replace(
                listings[0],
                listing_id="live-copy",
                price=Decimal("1"),
                mode=RunMode.LIVE,
            )
            combined = [*listings, live_copy]
            combined_floors = detect_floor_opportunities(combined, Decimal("10"))
            demo_cheap = next(
                row for row in combined_floors
                if row.listing.mode == RunMode.DEMO and row.listing.listing_id == "mrkt-demo-001"
            )
            self.assertEqual(demo_cheap.reference_price, Decimal("50"))
            combined_arbitrage = detect_arbitrage_opportunities(
                combined, fees={}, max_age_seconds=300,
                now_epoch=listings[0].observed_at.timestamp(),
            )
            self.assertEqual(len(combined_arbitrage), 3)
            self.assertTrue(all(row.listing.mode == RunMode.DEMO for row in combined_arbitrage))

        asyncio.run(inspect())


if __name__ == "__main__":
    unittest.main()
