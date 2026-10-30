from datetime import UTC, datetime, timedelta
from decimal import Decimal
import unittest

from app.domain.analysis import (
    decimal_text,
    deduplicate_listings,
    detect_arbitrage_opportunities,
    detect_floor_opportunities,
    discount_percent,
    estimate_profit,
    same_currency_floor,
    trait_frequency,
)
from app.domain.models import Listing, ListingStatus, Marketplace, RunMode


def listing(
    market: Marketplace,
    listing_id: str,
    nft_id: str,
    price: str,
    currency: str = "TON",
    *,
    collection: str = "Vintage Cigar",
    model: str | None = "Blue Hue",
    observed_at: datetime | None = None,
    status: ListingStatus = ListingStatus.ACTIVE,
) -> Listing:
    now = datetime.now(UTC)
    return Listing(
        marketplace=market,
        listing_id=listing_id,
        nft_id=nft_id,
        collection=collection,
        price=Decimal(price),
        currency=currency,
        url=None,
        model=model,
        backdrop="Pacific Cyan",
        symbol="Ball",
        status=status,
        discovered_at=now,
        observed_at=observed_at or now,
    )


class AnalysisTests(unittest.TestCase):
    def test_discount_percent_is_exact_decimal(self) -> None:
        self.assertEqual(discount_percent(Decimal("35"), Decimal("42")), Decimal("700") / Decimal("42"))

    def test_decimal_text_does_not_use_exponents_or_strip_integer_zeroes(self) -> None:
        self.assertEqual(decimal_text(Decimal("100.000000")), "100")
        self.assertEqual(decimal_text(Decimal("0.0500")), "0.05")

    def test_deduplication_keeps_latest_observation_per_marketplace_listing(self) -> None:
        now = datetime.now(UTC)
        rows = [
            listing(
                Marketplace.MRKT,
                "same-listing",
                "gift-1",
                "35",
                observed_at=now - timedelta(seconds=5),
            ),
            listing(
                Marketplace.MRKT,
                "same-listing",
                "gift-1",
                "34",
                observed_at=now,
            ),
            listing(Marketplace.PORTALS, "same-listing", "gift-1", "36", observed_at=now),
        ]
        unique = deduplicate_listings(rows)
        self.assertEqual(len(unique), 2)
        mrkt = next(row for row in unique if row.marketplace == Marketplace.MRKT)
        self.assertEqual(mrkt.price, Decimal("34"))

    def test_floor_keeps_currencies_separate_and_ignores_sold(self) -> None:
        rows = [
            listing(Marketplace.MRKT, "1", "1", "3.5", "TON"),
            listing(Marketplace.PORTALS, "2", "2", "4", "TON"),
            listing(Marketplace.TONNEL, "3", "3", "2", "GRAM"),
            listing(Marketplace.MRKT, "4", "4", "1", "TON", status=ListingStatus.SOLD),
        ]
        self.assertEqual(
            same_currency_floor(rows),
            {
                (RunMode.LIVE, "vintage cigar", "TON"): Decimal("3.5"),
                (RunMode.LIVE, "vintage cigar", "GRAM"): Decimal("2"),
            },
        )

    def test_below_comparable_excludes_other_listing_for_same_nft(self) -> None:
        candidate = listing(Marketplace.MRKT, "a", "gift-1", "35")
        same_nft = listing(Marketplace.PORTALS, "b", "gift-1", "39")
        comparable = listing(Marketplace.TONNEL, "c", "gift-2", "42")
        results = detect_floor_opportunities(
            [candidate, same_nft, comparable], Decimal("10"), min_trait_comparables=1
        )
        found = [row for row in results if row.listing.nft_id == "gift-1"]
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0].listing.marketplace, Marketplace.MRKT)
        self.assertEqual(found[0].reference_price, Decimal("42"))
        self.assertEqual(found[0].discount_percent, Decimal("700") / Decimal("42"))

    def test_trait_floor_uses_only_matching_trait_when_enough_samples_exist(self) -> None:
        rows = [
            listing(Marketplace.MRKT, "a", "gift-1", "35", model="Rare"),
            listing(Marketplace.PORTALS, "b", "gift-2", "42", model="Rare"),
            listing(Marketplace.TONNEL, "c", "gift-3", "20", model="Common"),
        ]
        results = detect_floor_opportunities(rows, Decimal("10"), min_trait_comparables=1)
        rare = next(row for row in results if row.listing.nft_id == "gift-1")
        self.assertEqual(rare.reference_price, Decimal("42"))
        self.assertIn("model floor", rare.note)

    def test_duplicate_marketplace_rows_do_not_satisfy_trait_sample_minimum(self) -> None:
        rows = [
            listing(Marketplace.MRKT, "candidate", "gift-1", "35", model="Rare"),
            listing(Marketplace.PORTALS, "a", "gift-2", "42", model="Rare"),
            listing(Marketplace.TONNEL, "a-copy", "gift-2", "43", model="Rare"),
            listing(Marketplace.PORTALS, "b", "gift-3", "45", model="Rare"),
            listing(Marketplace.MRKT, "c", "gift-4", "20", model="Common"),
        ]
        results = detect_floor_opportunities(rows, Decimal("10"), min_trait_comparables=3)
        self.assertNotIn("gift-1", {row.listing.nft_id for row in results})

    def test_different_currency_cannot_create_comparable(self) -> None:
        rows = [
            listing(Marketplace.MRKT, "a", "gift-1", "1", "TON"),
            listing(Marketplace.TONNEL, "b", "gift-2", "999", "GRAM"),
        ]
        self.assertEqual(detect_floor_opportunities(rows, Decimal("0")), [])

    def test_rarity_is_reported_as_observed_listings_not_collection_supply(self) -> None:
        rows = [
            listing(Marketplace.MRKT, "a", "1", "1", model="Rare"),
            listing(Marketplace.PORTALS, "a-copy", "1", "2", model="Rare"),
            listing(Marketplace.MRKT, "b", "2", "2", model="Common"),
            listing(Marketplace.MRKT, "c", "3", "3", model="Common"),
        ]
        observed = trait_frequency(rows)
        self.assertEqual(observed[(RunMode.LIVE, "vintage cigar", "model", "rare")], (1, 3))

    def test_profit_is_unknown_when_fees_are_unknown(self) -> None:
        estimate = estimate_profit(Decimal("35"), Decimal("42"))
        self.assertEqual(estimate.gross, Decimal("7"))
        self.assertIsNone(estimate.net)

    def test_profit_applies_buy_and_sell_fees(self) -> None:
        estimate = estimate_profit(Decimal("35"), Decimal("42"), Decimal("2"), Decimal("5"))
        self.assertEqual(estimate.net, Decimal("4.20"))

    def test_arbitrage_requires_same_nft_same_currency_and_fresh_listing(self) -> None:
        now = datetime.now(UTC)
        rows = [
            listing(Marketplace.MRKT, "a", "gift-1", "35", observed_at=now),
            listing(Marketplace.PORTALS, "b", "gift-1", "42", observed_at=now),
            listing(Marketplace.TONNEL, "c", "gift-2", "80", observed_at=now),
            listing(Marketplace.TONNEL, "d", "gift-1", "200", "GRAM", observed_at=now),
            listing(
                Marketplace.MRKT,
                "old",
                "gift-1",
                "999",
                observed_at=now - timedelta(hours=1),
            ),
        ]
        results = detect_arbitrage_opportunities(
            rows, fees={}, max_age_seconds=300, now_epoch=now.timestamp()
        )
        self.assertEqual(len(results), 1)
        self.assertTrue(all(row.estimated_net is None for row in results))
        self.assertTrue(all(row.gross_spread == Decimal("7") for row in results))
        self.assertEqual(results[0].gross_margin_percent, Decimal("7") * 100 / Decimal("42"))


if __name__ == "__main__":
    unittest.main()
