from __future__ import annotations
import os
from pathlib import Path
from urllib.parse import unquote, urlparse

def project_root() -> Path:
    configured = os.getenv("ARMORED_ROOT")
    return Path(configured).expanduser().resolve() if configured else Path(__file__).resolve().parents[1]

def affiliate_tail(url: str | None, fallback: str | None = None) -> str:
    value = ""
    if url:
        parsed = urlparse(str(url).strip())
        value = unquote(parsed.path.rstrip("/").split("/")[-1])
    if not value and fallback:
        value = str(fallback).strip()
    safe = "".join(c if c.isalnum() or c in "-_." else "_" for c in value).strip("._-")
    return safe or "unknown"

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

    def original(self, item_id: int, suffix: str = ".mp4", telegram_message_id: str | None = None, original_url: str | None = None) -> Path:
        message_id = str(telegram_message_id or item_id)
        tail = affiliate_tail(original_url)
        return self.workspace(item_id) / f"{message_id}_{tail}{suffix}"

    def working(self, item_id: int, telegram_message_id: str | None = None) -> Path:
        message_id = str(telegram_message_id or item_id)
        return self.workspace(item_id) / f"{message_id}_.mp4"

    def result(self, item_id: int, affiliate_url: str | None = None, affiliate_name: str | None = None, telegram_message_id: str | None = None) -> Path:
        message_id = str(telegram_message_id or item_id)
        tail = affiliate_tail(affiliate_url, affiliate_name)
        return self.workspace(item_id) / f"{message_id}_{tail}.mp4"
