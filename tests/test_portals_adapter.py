from __future__ import annotations

import unittest
from decimal import Decimal
from unittest.mock import patch

from app.adapters.base import AdapterState, AdapterUnavailableError
from app.adapters.portals import PortalsAPIError, PortalsAdapter
from app.domain.models import ListingStatus, Marketplace, RunMode


class FakeResponse:
    def __init__(self, status: int, payload: object):
        self.status = status
        self.payload = payload

class FakeRequester:
    def __init__(self, pages: list[FakeResponse]):
        self.pages = iter(pages)
        self.calls: list[tuple[str, dict[str, object], dict[str, str]]] = []

    async def __call__(
        self, url: str, params: dict[str, object], headers: dict[str, str]
    ) -> tuple[int, object]:
        self.calls.append((url, params, headers))
        response = next(self.pages)
        return response.status, response.payload


class FakeHTTPResponse:
    status = 200

    def __enter__(self) -> FakeHTTPResponse:
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None

    def read(self) -> bytes:
        return b'{"ok": true}'


class FakeOpener:
    def __init__(self):
        self.request = None

    def open(self, request, *, timeout: int) -> FakeHTTPResponse:
        self.request = request
        return FakeHTTPResponse()


def listing(
    listing_id: str,
    *,
    price: str = "12.5",
    status: str = "listed",
) -> dict[str, object]:
    return {
        "id": listing_id,
        "tg_id": f"telegram-gift-{listing_id}",
        "collection_id": "collection-uuid",
        "external_collection_number": 17,
        "name": "Plush Pepe",
        "price": price,
        "status": status,
        "listed_at": "2026-10-30T12:00:00Z",
        "attributes": [
            {"type": "model", "value": "Rare Pepe", "rarity_per_mille": 1.2},
            {"type": "backdrop", "value": "Blue", "rarity_per_mille": 4.2},
            {"type": "symbol", "value": "Star", "rarity_per_mille": 2.0},
        ],
    }


class PortalsAdapterTests(unittest.IsolatedAsyncioTestCase):
    async def test_without_key_is_not_configured_and_never_fetches(self) -> None:
        adapter = PortalsAdapter()
        status = await adapter.status()
        self.assertEqual(status.state, AdapterState.NOT_CONFIGURED)
        self.assertFalse(status.enabled)
        with self.assertRaises(AdapterUnavailableError):
            await adapter.fetch_listings()

    async def test_search_uses_official_get_and_paginates_listed_gifts(self) -> None:
        first = FakeResponse(200, {"results": [listing("p-1")], "total_count": 2})
        second = FakeResponse(200, {"results": [listing("p-2", price="14")], "total_count": 2})
        requester = FakeRequester([first, second])
        delays: list[float] = []

        async def fake_sleep(seconds: float) -> None:
            delays.append(seconds)

        adapter = PortalsAdapter(
            "test-key",
            max_pages=5,
            request_interval_seconds=1.05,
            requester=requester,
            sleep=fake_sleep,
        )
        status = await adapter.status()
        results = await adapter.fetch_listings()

        self.assertEqual(status.state, AdapterState.READY)
        self.assertEqual(len(results), 2)
        self.assertEqual([row.listing_id for row in results], ["p-1", "p-2"])
        self.assertEqual(results[0].marketplace, Marketplace.PORTALS)
        self.assertEqual(results[0].mode, RunMode.LIVE)
        self.assertEqual(results[0].price, Decimal("12.5"))
        self.assertEqual(results[0].currency, "GRAM")
        self.assertEqual(results[0].nft_id, "telegram-gift-p-1")
        self.assertEqual(results[0].collection, "Plush Pepe")
        self.assertEqual(results[0].model, "Rare Pepe")
        self.assertEqual(results[0].backdrop, "Blue")
        self.assertEqual(results[0].symbol, "Star")
        self.assertEqual(results[0].url, None)
        self.assertEqual(results[0].status, ListingStatus.ACTIVE)
        self.assertEqual(requester.calls[0][2]["Authorization"], "partners test-key")
        self.assertEqual(requester.calls[0][1], {
            "status": "listed", "sort_by": "listed_at desc", "offset": 0
        })
        self.assertEqual(requester.calls[1][1]["offset"], 1)
        self.assertEqual({call[0] for call in requester.calls}, {
            "https://portal-market.com/partners/nfts/search"
        })
        self.assertEqual(delays, [1.05])

    async def test_http_rate_limit_is_reported_without_secrets(self) -> None:
        requester = FakeRequester([FakeResponse(429, {"message": "too many requests"})])
        adapter = PortalsAdapter("do-not-log", requester=requester)
        with self.assertRaisesRegex(PortalsAPIError, "HTTP 429") as error:
            await adapter.fetch_listings()
        self.assertNotIn("do-not-log", str(error.exception))

    async def test_documented_access_error_code_is_preserved_safely(self) -> None:
        requester = FakeRequester([
            FakeResponse(403, {"code": "PARTNER_SUBSCRIPTION_REQUIRED", "message": "choose a plan"})
        ])
        adapter = PortalsAdapter("do-not-log", requester=requester)
        with self.assertRaisesRegex(PortalsAPIError, "PARTNER_SUBSCRIPTION_REQUIRED") as error:
            await adapter.fetch_listings()
        self.assertNotIn("do-not-log", str(error.exception))

    async def test_cloudflare_1010_marks_portals_unavailable_and_prevents_retries(self) -> None:
        requester = FakeRequester([
            FakeResponse(403, {
                "cloudflare_error": True,
                "error_code": 1010,
                "error_name": "browser_signature_banned",
            })
        ])
        adapter = PortalsAdapter("test-key", requester=requester)
        with self.assertRaisesRegex(PortalsAPIError, "browser_signature_banned"):
            await adapter.fetch_listings()

        status = await adapter.status()
        self.assertEqual(status.state, AdapterState.UNAVAILABLE)
        self.assertIn("contact Portals support", status.message)
        with self.assertRaises(AdapterUnavailableError):
            await adapter.fetch_listings()
        self.assertEqual(len(requester.calls), 1)

    async def test_default_transport_uses_only_documented_get(self) -> None:
        opener = FakeOpener()
        with patch("app.adapters.portals.build_opener", return_value=opener):
            status, payload = PortalsAdapter._request_json_sync(
                "https://portal-market.com/partners/nfts/search",
                {"status": "listed", "offset": 0},
                {"Authorization": "partners test-key"},
            )

        self.assertEqual(status, 200)
        self.assertEqual(payload, {"ok": True})
        self.assertEqual(opener.request.get_method(), "GET")
        self.assertIn("/partners/nfts/search?", opener.request.full_url)
        self.assertEqual(opener.request.data, None)
        self.assertEqual(opener.request.get_header("Authorization"), "partners test-key")

    async def test_search_rejects_non_listed_items(self) -> None:
        requester = FakeRequester([
            FakeResponse(200, {"results": [listing("p-1", status="unlisted")], "total_count": 1})
        ])
        adapter = PortalsAdapter("test-key", requester=requester)
        with self.assertRaisesRegex(PortalsAPIError, "non-listed"):
            await adapter.fetch_listings()


if __name__ == "__main__":
    unittest.main()
