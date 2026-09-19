from armored_core.models import Item, PublicationCheck
from armored_core.services import PublicationResult


class ArmoredHub:
    """Thin adapter: publication state is tri-state and publishing is idempotent."""
    def __init__(self, publisher):
        self.publisher = publisher

    def check_publication(self, item: Item) -> PublicationCheck:
        if hasattr(self.publisher, "check_publication"):
            return self.publisher.check_publication(item)
        raise TypeError("Hub publisher must expose check_publication(item)")

    def publish(self, item: Item) -> PublicationResult:
        return self.publisher.publish(item)
