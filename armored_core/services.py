from __future__ import annotations
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol
from .database import Database
from .models import Item
from .storage import Storage

@dataclass(frozen=True)
class VisionResult:
    affiliate_name: str
    affiliate_url: str

@dataclass(frozen=True)
class PublicationResult:
    confirmed: bool
    message_id: str | None

class VisionService(Protocol):
    def identify(self, item: Item) -> VisionResult: ...

class StudioService(Protocol):
    def process(self, item: Item) -> Path: ...

class Publisher(Protocol):
    def is_published(self, item: Item) -> bool: ...
    def publish(self, item: Item) -> PublicationResult: ...

class SyncService:
    def __init__(self, db: Database, storage: Storage) -> None:
        self.db, self.storage = db, storage

    def ingest(self, source: Path, telegram_message_id: str) -> int:
        source = source.resolve()
        if not source.is_file(): raise FileNotFoundError(source)
        row = self.db.conn.execute("SELECT id FROM items WHERE telegram_message_id=?", (telegram_message_id,)).fetchone()
        if row: return int(row["id"])
        # Reserve the DB ID first; the original is copied exactly once into its item workspace.
        item_id = self.db.create_item(telegram_message_id, self.storage.videos / "pending" / "placeholder")
        original = self.storage.original(item_id, source.suffix)
        shutil.copy2(source, original)
        self.db.conn.execute("UPDATE items SET original_path=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
                             (str(original), item_id)); self.db.conn.commit()
        return item_id
