from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal
from typing import Iterable

from app.domain.models import Listing, Opportunity, RunMode

ZERO = Decimal("0")
HUNDRED = Decimal("100")


def discount_percent(price: Decimal, reference: Decimal) -> Decimal:
    if reference <= ZERO:
        raise ValueError("reference price must be greater than zero")
    return (reference - price) * HUNDRED / reference


def decimal_text(value: Decimal) -> str:
    rendered = format(value, "f")
    return rendered.rstrip("0").rstrip(".") if "." in rendered else rendered


def deduplicate_listings(listings: Iterable[Listing]) -> list[Listing]:
    """Keep one freshest observation for each marketplace listing key."""
    unique: dict[str, Listing] = {}
    for listing in listings:
        previous = unique.get(listing.natural_key)
        if previous is None or listing.observed_at >= previous.observed_at:
            unique[listing.natural_key] = listing
    return list(unique.values())


def same_currency_floor(
    listings: Iterable[Listing],
) -> dict[tuple[RunMode, str, str], Decimal]:
    """Return the lowest active ask by collection and currency, never FX-converted."""
    floors: dict[tuple[RunMode, str, str], Decimal] = {}
    for listing in listings:
        if listing.status.value != "active":
            continue
        key = (listing.mode, listing.collection.casefold(), listing.currency.upper())
        if key not in floors or listing.price < floors[key]:
            floors[key] = listing.price
    return floors


def trait_frequency(
    listings: Iterable[Listing],
) -> dict[tuple[RunMode, str, str, str], tuple[int, int]]:
    """Count observed listings sharing each trait; this is observed supply, not total rarity."""
    unique_rows: dict[tuple[RunMode, str, str], Listing] = {}
    for item in listings:
        if item.status.value != "active":
            continue
        identity = item.nft_id or item.natural_key
        unique_rows.setdefault((item.mode, item.collection.casefold(), identity), item)
    rows = list(unique_rows.values())
    totals: dict[tuple[RunMode, str], int] = defaultdict(int)
    counts: dict[tuple[RunMode, str, str, str], int] = defaultdict(int)
    for row in rows:
        collection = row.collection.casefold()
        totals[(row.mode, collection)] += 1
        for name, value in (("model", row.model), ("backdrop", row.backdrop), ("symbol", row.symbol)):
            if value:
                counts[(row.mode, collection, name, value.casefold())] += 1
    return {key: (count, totals[(key[0], key[1])]) for key, count in counts.items()}


@dataclass(frozen=True, slots=True)
class ProfitEstimate:
    gross: Decimal
    net: Decimal | None


def estimate_profit(
    buy_price: Decimal,
    sell_price: Decimal,
    buy_fee_percent: Decimal | None = None,
    sell_fee_percent: Decimal | None = None,
) -> ProfitEstimate:
    """Calculate TON profit only when prices share a currency; missing fees remain unknown."""
    gross = sell_price - buy_price
    if buy_fee_percent is None or sell_fee_percent is None:
        return ProfitEstimate(gross=gross, net=None)
    if not (ZERO <= buy_fee_percent < HUNDRED and ZERO <= sell_fee_percent < HUNDRED):
        raise ValueError("fee percentages must be in [0, 100)")
    total_cost = buy_price * (Decimal("1") + buy_fee_percent / HUNDRED)
    sale_proceeds = sell_price * (Decimal("1") - sell_fee_percent / HUNDRED)
    return ProfitEstimate(gross=gross, net=sale_proceeds - total_cost)


def detect_floor_opportunities(
    listings: list[Listing], min_discount_percent: Decimal, min_trait_comparables: int = 3
) -> list[Opportunity]:
    """Flag asks below other active listings in the same collection/currency.

    The listing's own NFT is excluded from its reference price, so relisting the
    same gift on a second marketplace cannot distort its comparable floor.
    """
    groups: dict[tuple[RunMode, str, str], list[Listing]] = defaultdict(list)
    for listing in listings:
        if listing.status.value == "active":
            groups[(listing.mode, listing.collection.casefold(), listing.currency.upper())].append(
                listing
            )

    results: list[Opportunity] = []
    for group in groups.values():
        for candidate in group:
            by_comparable: dict[str, Listing] = {}
            for item in group:
                if item.natural_key == candidate.natural_key or item.price <= 0:
                    continue
                if candidate.nft_id and item.nft_id == candidate.nft_id:
                    continue
                identity = item.nft_id or item.natural_key
                previous = by_comparable.get(identity)
                if previous is None or item.price < previous.price:
                    by_comparable[identity] = item
            comparables = list(by_comparable.values())
            if not comparables:
                continue
            reference = min(item.price for item in comparables)
            observed = len(comparables)
            trait_note = ""
            for attr in ("model", "backdrop", "symbol"):
                value = getattr(candidate, attr)
                if value:
                    matching = [
                        item
                        for item in comparables
                        if getattr(item, attr)
                        and getattr(item, attr).casefold() == value.casefold()
                    ]
                    if len(matching) >= min_trait_comparables:
                        reference = min(item.price for item in matching)
                        observed = len(matching)
                        trait_note = attr
                        break
            pct = discount_percent(candidate.price, reference)
            if pct < min_discount_percent:
                continue
            key = (
                f"floor:{candidate.natural_key}:{decimal_text(candidate.price)}:"
                f"{candidate.currency.upper()}"
            )
            note = "Observed listings only; this is not a guaranteed sale price."
            if trait_note:
                note += f" Reference uses the {trait_note} floor from {observed} matching listings."
            results.append(
                Opportunity(
                    key=key,
                    kind="below_comparable",
                    listing=candidate,
                    reference_price=reference,
                    discount_percent=pct,
                    note=note,
                )
            )
    return results


def detect_arbitrage_opportunities(
    listings: list[Listing],
    fees: dict[str, Decimal],
    max_age_seconds: int,
    now_epoch: float,
) -> list[Opportunity]:
    """Match the same gift ID across marketplaces and omit net estimates if fees are unknown."""
    by_nft: dict[tuple[RunMode, str], list[Listing]] = defaultdict(list)
    for listing in listings:
        if listing.nft_id and listing.status.value == "active":
            by_nft[(listing.mode, listing.nft_id)].append(listing)

    results: list[Opportunity] = []
    for (mode, nft_id), rows in by_nft.items():
        for buy in rows:
            if abs(now_epoch - buy.observed_at.timestamp()) > max_age_seconds:
                continue
            alternatives = [
                sell
                for sell in rows
                if sell.marketplace != buy.marketplace
                and sell.currency.upper() == buy.currency.upper()
                and abs(now_epoch - sell.observed_at.timestamp()) <= max_age_seconds
            ]
            for sell in alternatives:
                if sell.price <= buy.price:
                    continue
                estimate = estimate_profit(
                    buy.price,
                    sell.price,
                    fees.get(f"buy:{buy.marketplace.value}"),
                    fees.get(f"sell:{sell.marketplace.value}"),
                )
                if estimate.net is not None and estimate.net <= ZERO:
                    continue
                net_note = (
                    f"Estimated net: {estimate.net} {buy.currency}; verify both listings and fees."
                    if estimate.net is not None
                    else "Gross price spread only; marketplace fees are not verified."
                )
                results.append(
                    Opportunity(
                        key=(
                            f"arb:{mode.value}:{nft_id}:{buy.marketplace.value}:{decimal_text(buy.price)}:"
                            f"{sell.marketplace.value}:{decimal_text(sell.price)}:{buy.currency.upper()}"
                        ),
                        kind="cross_market",
                        listing=buy,
                        reference_price=sell.price,
                        gross_spread=estimate.gross,
                        gross_margin_percent=estimate.gross * HUNDRED / sell.price,
                        estimated_net=estimate.net,
                        note=f"Potential spread only. {net_note}",
                    )
                )
    return results
