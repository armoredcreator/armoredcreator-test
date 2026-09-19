from __future__ import annotations
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Protocol
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

@dataclass(frozen=True)
class IngestMessage:
    telegram_message_id: str
    source_id: str = "telegram"
    topic_id: int | None = None
    topic_name: str | None = None
    original_url: str | None = None
    source_path: Path | None = None
    materialize: Callable[[Path], None] | None = None

class SyncService:
    def __init__(self, db: Database, storage: Storage) -> None:
        self.db, self.storage = db, storage

    def ingest_message(self, message: IngestMessage) -> int:
        if message.source_path is None and message.materialize is None:
            raise ValueError("ingest-message-requires-source-path-or-materializer")

        existing = self.db.conn.execute(
            "SELECT id FROM items WHERE telegram_message_id=?",
            (message.telegram_message_id,),
        ).fetchone()
        if existing:
            return int(existing["id"])

        suffix = (
            message.source_path.suffix
            if message.source_path is not None and message.source_path.suffix
            else ".mp4"
        )

        item_id = self.db.reserve_item(
            message.telegram_message_id,
            source_id=message.source_id,
            topic_id=message.topic_id,
            topic_name=message.topic_name,
            original_url=message.original_url,
        )
        original = self.storage.original(item_id, suffix)
        partial = original.with_suffix(original.suffix + ".part")

        try:
            if partial.exists():
                partial.unlink()

            if message.materialize is not None:
                message.materialize(partial)
            else:
                source = message.source_path.resolve()
                if not source.is_file():
                    raise FileNotFoundError(source)
                shutil.copy2(source, partial)

            if not partial.is_file() or partial.stat().st_size <= 0:
                raise IOError("original-materialization-empty")

            partial.replace(original)
            self.db.finalize_original_path(item_id, original)
            return item_id
        except Exception:
            if partial.exists():
                partial.unlink()
            if original.exists():
                original.unlink()
            self.db.rollback_ingest()
            raise

    def ingest(
        self,
        source: Path,
        telegram_message_id: str,
        source_id: str = 'telegram',
        topic_id: int | None = None,
        topic_name: str | None = None,
        original_url: str | None = None,
    ) -> int:
        return self.ingest_message(
            IngestMessage(
                telegram_message_id=telegram_message_id,
                source_id=source_id,
                topic_id=topic_id,
                topic_name=topic_name,
                original_url=original_url,
                source_path=Path(source),
            )
        )
