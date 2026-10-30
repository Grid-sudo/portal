from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from app.domain.models import Listing, Marketplace


class AdapterState(StrEnum):
    READY = "READY"
    DEMO = "DEMO"
    NOT_CONFIGURED = "NOT_CONFIGURED"
    UNAVAILABLE = "UNAVAILABLE"


@dataclass(frozen=True, slots=True)
class AdapterStatus:
    marketplace: Marketplace
    state: AdapterState
    message: str
    source_url: str | None = None

    @property
    def enabled(self) -> bool:
        return self.state in (AdapterState.READY, AdapterState.DEMO)


class MarketplaceAdapter(Protocol):
    marketplace: Marketplace

    async def status(self) -> AdapterStatus: ...

    async def fetch_listings(self) -> list[Listing]: ...


class AdapterUnavailableError(RuntimeError):
    """Raised when code tries to fetch from a deliberately disabled source."""


class UnavailableAdapter:
    """Explicitly disabled integration; never makes guessed HTTP requests."""

    def __init__(
        self,
        marketplace: Marketplace,
        reason: str,
        source_url: str | None = None,
        state: AdapterState = AdapterState.UNAVAILABLE,
    ):
        self.marketplace = marketplace
        self.reason = reason
        self.source_url = source_url
        self.state = state

    async def status(self) -> AdapterStatus:
        return AdapterStatus(self.marketplace, self.state, self.reason, self.source_url)

    async def fetch_listings(self) -> list[Listing]:
        raise AdapterUnavailableError(f"{self.marketplace.value} adapter is not configured")
