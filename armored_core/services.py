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

    def ingest(self, source: Path, telegram_message_id: str) -> int:
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
            self.storage.pending_original(suffix),
        )
        original = self.storage.original(item_id, suffix)
        original.parent.mkdir(parents=True, exist_ok=True)
        partial = original.with_suffix(original.suffix + ".part")
        shutil.copy2(source, partial)
        if not partial.is_file() or partial.stat().st_size != source.stat().st_size:
            partial.unlink(missing_ok=True)
            raise IOError("original-copy-verification-failed")
        partial.replace(original)

        self.db.conn.execute(
            "UPDATE items SET original_path=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (str(original), item_id),
        )
        self.db.conn.commit()
        return item_id
