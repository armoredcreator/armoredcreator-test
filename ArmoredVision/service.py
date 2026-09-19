from armored_core.models import Item
from armored_core.services import VisionResult

class ArmoredVision:
    """Adapter boundary for the real product/affiliate identification logic."""
    def __init__(self, resolver): self.resolver=resolver
    def identify(self, item: Item) -> VisionResult:
        return self.resolver(item)
