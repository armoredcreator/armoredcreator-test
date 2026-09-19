from __future__ import annotations
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol
from .database import Database
from .models import Item, PublicationCheck
from .storage import Storage

@dataclass(frozen=True)
class VisionResult:
    affiliate_name: str
    affiliate_url: str

@dataclass(frozen=True)
class StudioResult:
    working_path: Path
    result_path: Path

@dataclass(frozen=True)
class PublicationResult:
    confirmed: bool
    message_id: str | None

class VisionService(Protocol):
    def identify(self, item: Item) -> VisionResult: ...

class StudioService(Protocol):
    def process(self, item: Item) -> StudioResult: ...

class Publisher(Protocol):
    def check_publication(self, item: Item) -> PublicationCheck: ...
    def publish(self, item: Item) -> PublicationResult: ...

class SyncService:
    def __init__(self, db: Database, storage: Storage) -> None:
        self.db, self.storage = db, storage

    def ingest(self, source: Path, telegram_message_id: str, source_id: str = 'telegram', topic_id: int | None = None, topic_name: str | None = None, original_url: str | None = None) -> int:
        source = source.resolve()
        if not source.is_file():
            raise FileNotFoundError(source)

        row = self.db.conn.execute(
            "SELECT id FROM items WHERE telegram_message_id=?",
            (telegram_message_id,),
        ).fetchone()
        if row:
            return int(row["id"])

        suffix = source.suffix or ".mp4"
        item_id = self.db.create_item(
            telegram_message_id,
            self.storage.original(item_id=0, suffix=suffix),
            source_id=source_id, topic_id=topic_id, topic_name=topic_name, original_url=original_url,
        )
        original = self.storage.original(item_id, suffix)

        shutil.copy2(source, original)
        if not original.is_file() or original.stat().st_size != source.stat().st_size:
            raise IOError("original-copy-verification-failed")

        self.db.conn.execute(
            "UPDATE items SET original_path=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (str(original), item_id),
        )
        self.db.conn.commit()
        return item_id
