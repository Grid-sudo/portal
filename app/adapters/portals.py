from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Awaitable, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import HTTPRedirectHandler, Request, build_opener

from pydantic import SecretStr

from app.adapters.base import AdapterState, AdapterStatus, AdapterUnavailableError
from app.domain.models import Listing, ListingStatus, Marketplace, RunMode

API_BASE_URL = "https://portal-market.com/partners"
API_DOCS_URL = "https://docs.portals.tg/ru/developers/api/"
SEARCH_PATH = "/nfts/search"
REQUEST_INTERVAL_SECONDS = 1.05
REQUEST_TIMEOUT_SECONDS = 20
KNOWN_ACCESS_ERROR_CODES = {
    "PARTNER_INVALID_KEY",
    "PARTNER_KEY_DISABLED",
    "PARTNER_USER_BANNED",
    "PARTNER_SUBSCRIPTION_REQUIRED",
    "PARTNER_SUBSCRIPTION_EXPIRED",
}

JsonRequester = Callable[
    [str, dict[str, object], dict[str, str]], Awaitable[tuple[int, object]]
]


class PortalsAPIError(RuntimeError):
    """A safe-to-log error from the documented Portals read API."""


class _NoRedirectHandler(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class PortalsAdapter:
    """Read-only Portals listing search through the official Partner API."""

    marketplace = Marketplace.PORTALS

    def __init__(
        self,
        api_key: SecretStr | str | None = None,
        *,
        max_pages: int = 5,
        request_interval_seconds: float = REQUEST_INTERVAL_SECONDS,
        requester: JsonRequester | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        if isinstance(api_key, SecretStr):
            api_key = api_key.get_secret_value()
        self._api_key = api_key.strip() if api_key else None
        self.max_pages = max_pages
        self.request_interval_seconds = request_interval_seconds
        self._requester = requester or self._request_json
        self._sleep = sleep
        self._blocked_reason: str | None = None

    async def status(self) -> AdapterStatus:
        if not self._api_key:
            return AdapterStatus(
                Marketplace.PORTALS,
                AdapterState.NOT_CONFIGURED,
                "Set PORTALS_API_KEY to use the official Partner API listing search.",
                API_DOCS_URL,
            )
        if self._blocked_reason:
            return AdapterStatus(
                Marketplace.PORTALS,
                AdapterState.UNAVAILABLE,
                self._blocked_reason,
                API_DOCS_URL,
            )
        return AdapterStatus(
            Marketplace.PORTALS,
            AdapterState.READY,
            f"Official Partner API configured; GET only, up to {self.max_pages} search pages per scan.",
            API_DOCS_URL,
        )

    async def fetch_listings(self) -> list[Listing]:
        if not self._api_key:
            raise AdapterUnavailableError("Portals adapter requires PORTALS_API_KEY")
        if self._blocked_reason:
            raise AdapterUnavailableError(self._blocked_reason)

        headers = {
            "Authorization": f"partners {self._api_key}",
            "Accept": "application/json",
        }
        listings: list[Listing] = []
        offset = 0
        for page_index in range(self.max_pages):
            if page_index:
                await self._sleep(self.request_interval_seconds)
            params: dict[str, object] = {
                "status": "listed",
                "sort_by": "listed_at desc",
                "offset": offset,
            }
            status, payload = await self._requester(
                f"{API_BASE_URL}{SEARCH_PATH}", params, headers
            )
            if status == 429:
                raise PortalsAPIError("Portals API rate limit reached (HTTP 429).")
            if status != 200:
                if (
                    status == 403
                    and isinstance(payload, dict)
                    and payload.get("cloudflare_error") is True
                    and payload.get("error_code") == 1010
                    and payload.get("error_name") == "browser_signature_banned"
                ):
                    self._blocked_reason = (
                        "Portals edge protection blocked this client (Cloudflare 1010 "
                        "browser_signature_banned); contact Portals support for the approved API access path."
                    )
                    raise PortalsAPIError(self._blocked_reason)
                code = payload.get("code") if isinstance(payload, dict) else None
                if isinstance(code, str) and code in KNOWN_ACCESS_ERROR_CODES:
                    raise PortalsAPIError(f"Portals API returned HTTP {status} ({code}).")
                raise PortalsAPIError(f"Portals API returned HTTP {status}.")

            rows = payload.get("results") if isinstance(payload, dict) else None
            total_count = payload.get("total_count") if isinstance(payload, dict) else None
            if not isinstance(rows, list):
                raise PortalsAPIError("Portals API response is missing its results list.")
            if not rows:
                break
            if type(total_count) is not int or total_count < 0:
                raise PortalsAPIError("Portals API response is missing a valid total_count.")

            listings.extend(self._to_listing(row) for row in rows)
            offset += len(rows)
            if offset >= total_count:
                break

        self._blocked_reason = None
        return listings

    async def _request_json(
        self,
        url: str,
        params: dict[str, object],
        headers: dict[str, str],
    ) -> tuple[int, object]:
        return await asyncio.to_thread(self._request_json_sync, url, params, headers)

    @staticmethod
    def _request_json_sync(
        url: str,
        params: dict[str, object],
        headers: dict[str, str],
    ) -> tuple[int, object]:
        request_url = f"{url}?{urlencode(params)}"
        request = Request(request_url, headers=headers, method="GET")
        opener = build_opener(_NoRedirectHandler)
        try:
            with opener.open(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
                status = response.status
                body = response.read()
        except HTTPError as exc:
            status = exc.code
            body = exc.read()
        except URLError as exc:
            reason = exc.reason
            raise PortalsAPIError(
                f"Portals API request failed ({type(reason).__name__})."
            ) from exc
        try:
            return status, json.loads(body)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise PortalsAPIError("Portals API returned invalid JSON.") from exc

    @staticmethod
    def _to_listing(row: dict[str, Any]) -> Listing:
        if not isinstance(row, dict):
            raise PortalsAPIError("Portals API returned a malformed listing.")
        try:
            if row["status"] != "listed":
                raise PortalsAPIError("Portals search returned a non-listed gift.")
            price = Decimal(str(row["price"]))
            if not price.is_finite() or price <= 0:
                raise PortalsAPIError("Portals listing has an invalid price.")
            listing_id = str(row["id"])
            collection_id = str(row["collection_id"])
            collection = str(row.get("name") or collection_id)
            nft_id = str(
                row.get("tg_id")
                or f"{collection_id}:{row['external_collection_number']}"
            )
        except (KeyError, TypeError, ValueError, InvalidOperation) as exc:
            raise PortalsAPIError("Portals API returned an incomplete listing.") from exc

        attributes = row.get("attributes") or []
        if not isinstance(attributes, list):
            raise PortalsAPIError("Portals listing has malformed attributes.")
        traits: dict[str, str] = {}
        for attribute in attributes:
            if isinstance(attribute, dict):
                kind = attribute.get("type")
                value = attribute.get("value")
                if kind in {"model", "backdrop", "symbol"} and isinstance(value, str):
                    traits[kind] = value

        now = datetime.now(UTC)
        discovered_at = now
        listed_at = row.get("listed_at")
        if isinstance(listed_at, str):
            try:
                parsed = datetime.fromisoformat(listed_at.replace("Z", "+00:00"))
                discovered_at = parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed
            except ValueError:
                pass

        return Listing(
            marketplace=Marketplace.PORTALS,
            listing_id=listing_id,
            nft_id=nft_id,
            collection=collection,
            price=price,
            currency="GRAM",
            url=None,
            mode=RunMode.LIVE,
            model=traits.get("model"),
            backdrop=traits.get("backdrop"),
            symbol=traits.get("symbol"),
            status=ListingStatus.ACTIVE,
            discovered_at=discovered_at,
            observed_at=now,
        )
