from __future__ import annotations

import hashlib
import shutil
import sqlite3
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

    @staticmethod
    def _sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

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
        final = self.storage.videos / "_incoming"
        final.mkdir(parents=True, exist_ok=True)
        partial = final / f"{telegram_message_id}{suffix}.part"
        digest = self._sha256(source)
        item_id = None
        try:
            original_placeholder = final / f"{telegram_message_id}{suffix}.reserved"
            item_id = self.db.create_item(telegram_message_id, original_placeholder)
            original = self.storage.original(item_id, suffix)
            partial = original.with_suffix(original.suffix + ".part")
            shutil.copy2(source, partial)
            if not partial.is_file():
                raise IOError("original-copy-missing")
            if partial.stat().st_size != source.stat().st_size or self._sha256(partial) != digest:
                partial.unlink(missing_ok=True)
                raise IOError("original-copy-verification-failed")
            partial.replace(original)
            self.db.conn.execute(
                "UPDATE items SET original_path=?, original_size=?, original_sha256=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
                (str(original), original.stat().st_size, digest, item_id),
            )
            self.db.conn.commit()
            return item_id
        except sqlite3.IntegrityError:
            if partial.exists():
                partial.unlink(missing_ok=True)
            row = self.db.conn.execute(
                "SELECT id FROM items WHERE telegram_message_id=?",
                (telegram_message_id,),
            ).fetchone()
            if row:
                return int(row["id"])
            raise
        except Exception:
            partial.unlink(missing_ok=True)
            raise
