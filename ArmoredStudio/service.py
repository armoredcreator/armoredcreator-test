from armored_core.models import Item
from armored_core.services import StudioResult


class ArmoredStudio:
    """Thin adapter: the real Studio implementation stays outside orchestration."""
    def __init__(self, processor):
        self.processor = processor

    def process(self, item: Item) -> StudioResult:
        result = self.processor(item)
        if isinstance(result, StudioResult):
            return result
        if isinstance(result, tuple) and len(result) == 2:
            return StudioResult(result[0], result[1])
        raise TypeError("Studio adapter must return StudioResult or (working_path, result_path)")
