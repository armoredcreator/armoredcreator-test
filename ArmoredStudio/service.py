from pathlib import Path
from armored_core.models import Item

class ArmoredStudio:
    """Adapter boundary for the existing V1 detector/planner/validator/executor."""
    def __init__(self, processor): self.processor=processor
    def process(self, item: Item) -> Path:
        return self.processor(item)
