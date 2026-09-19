from armored_core.models import Item
from armored_core.services import PublicationResult

class ArmoredHub:
    """Idempotent publication adapter. Telegram is an external side effect."""
    def __init__(self, publisher): self.publisher=publisher
    def is_published(self, item: Item) -> bool:
        return self.publisher.is_published(item)
    def publish(self, item: Item) -> PublicationResult:
        return self.publisher.publish(item)
