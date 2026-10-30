from app.adapters.base import AdapterState, UnavailableAdapter
from app.domain.models import Marketplace


class TonnelAdapter(UnavailableAdapter):
    def __init__(self):
        super().__init__(
            Marketplace.TONNEL,
            "Disabled: no official public listing API or usage terms for a read-only integration were found.",
            "https://marketplace.tonnel.network/",
            AdapterState.UNAVAILABLE,
        )
