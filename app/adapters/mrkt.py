from app.adapters.base import AdapterState, UnavailableAdapter
from app.domain.models import Marketplace


class MRKTAdapter(UnavailableAdapter):
    def __init__(self):
        super().__init__(
            Marketplace.MRKT,
            "Disabled: no public, documented read API for current gift listings was found.",
            "https://mrkt.land/",
            AdapterState.UNAVAILABLE,
        )
