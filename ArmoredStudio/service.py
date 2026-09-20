from __future__ import annotations

from pathlib import Path

from armored_core.models import Item
from armored_core.services import StudioResult
from armored_core.storage import Storage

from .unified import UnifiedStudio


class ArmoredStudio:
    """Canonical Studio boundary: mandatory analysis followed by processing."""

    def __init__(self, root: Path):
        self.root = Path(root).resolve()
        self.storage = Storage(self.root)
        self.engine = UnifiedStudio(self.root, self.storage)

    def process(self, item: Item) -> StudioResult:
        result_path, _details = self.engine.process(item)
        return StudioResult(
            self.storage.working(item.item_id),
            Path(result_path),
        )


def build(root: Path, **_kwargs):
    return ArmoredStudio(root)
