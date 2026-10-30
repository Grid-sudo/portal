from __future__ import annotations

from typing import TYPE_CHECKING

from app.adapters.base import MarketplaceAdapter
from app.adapters.demo import DemoAdapter
from app.adapters.mrkt import MRKTAdapter
from app.adapters.portals import PortalsAdapter
from app.adapters.tonnel import TonnelAdapter
from app.domain.models import RunMode

if TYPE_CHECKING:
    from app.config import Settings


def build_adapters(_settings: Settings) -> list[MarketplaceAdapter]:
    """Build the selected data source without contacting undocumented services."""
    live_adapters = [
        MRKTAdapter(),
        PortalsAdapter(
            getattr(_settings, "portals_api_key", None),
            max_pages=getattr(_settings, "portals_max_pages", 5),
        ),
        TonnelAdapter(),
    ]
    if _settings.app_mode == RunMode.DEMO:
        return [DemoAdapter(), *live_adapters]
    return live_adapters
