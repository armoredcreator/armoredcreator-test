from pathlib import Path
from armored_core.services import SyncService

class ArmoredSync:
    """Thin adapter: source acquisition stays outside the canonical pipeline."""
    def __init__(self, sync: SyncService): self.sync=sync
    def ingest(self, video: Path, telegram_message_id: str) -> int:
        return self.sync.ingest(video, telegram_message_id)
