from __future__ import annotations

import os
from pathlib import Path


def project_root() -> Path:
    configured = os.getenv("ARMORED_ROOT")
    return Path(configured).expanduser().resolve() if configured else Path(__file__).resolve().parents[1]


class Storage:
    def __init__(self, root: Path | None = None) -> None:
        self.root = (root or project_root()).resolve()
        self.storage = self.root / "storage"
        self.database = self.storage / "database"
        self.videos = self.storage / "videos"
        self.logs = self.storage / "logs"
        self.backups = self.storage / "backups"
        for path in (self.database, self.videos, self.logs, self.backups):
            path.mkdir(parents=True, exist_ok=True)

    def workspace(self, item_id: int) -> Path:
        path = self.videos / str(item_id)
        path.mkdir(parents=True, exist_ok=True)
        return path

    def pending_original(self, suffix: str = ".mp4") -> Path:
        return self.database / f"pending-original{suffix}.reservation"

    def original(self, item_id: int, suffix: str = ".mp4") -> Path:
        return self.workspace(item_id) / f"{item_id}_linkoriginal{suffix}"

    def working(self, item_id: int) -> Path:
        return self.workspace(item_id) / f"{item_id}_.mp4"

    def result(self, item_id: int, affiliate_name: str) -> Path:
        safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in affiliate_name).strip("_") or "final"
        return self.workspace(item_id) / f"{item_id}_{safe}.mp4"
