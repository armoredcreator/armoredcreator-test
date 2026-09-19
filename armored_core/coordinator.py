from __future__ import annotations
from pathlib import Path
from typing import Any
from .database import Database
from .models import State
from .pipeline import Pipeline
from .recovery import Recovery
from .services import Publisher, StudioService, SyncService, VisionService
from .storage import Storage

class Coordinator:
    """Production composition root.

    Dependency injection remains available for deterministic tests, while
    production can be assembled once through build().
    """

    def __init__(
        self,
        db: Database,
        storage: Storage,
        vision: VisionService,
        studio: StudioService,
        publisher: Publisher,
        source: Any | None = None,
    ) -> None:
        self.db = db
        self.storage = storage
        self.sync = SyncService(db, storage)
        self.pipeline = Pipeline(db, storage, vision, studio, publisher)
        self.recovery = Recovery(db, storage, vision, studio, publisher)
        self.source = source

    @classmethod
    def build(cls, root: Path | None = None, bindings: Any | None = None) -> "Coordinator":
        from .runtime import build_production_bindings
        storage = Storage(root)
        db = Database(storage.database / "armoredcreator.db")
        b = bindings or build_production_bindings(db=db, storage=storage)
        return cls(db, storage, b.vision, b.studio, b.publisher, b.source)

    def ingest_once(self) -> int | None:
        if self.source is None:
            raise RuntimeError("source adapter is not configured")
        message = self.source.fetch_next()
        if hasattr(message, "__await__"):
            import asyncio
            message = asyncio.run(message)
        if message is None:
            return None
        source_path = Path(message.source_path)
        message_id = str(message.telegram_message_id)
        return self.sync.ingest(source_path, message_id, getattr(message, "source_id", "telegram"), getattr(message, "topic_id", None), getattr(message, "topic_name", None), getattr(message, "original_url", None))

    def run(self, item_id: int) -> None:
        self.pipeline.run(item_id)

    def recover(self, item_id: int) -> None:
        self.recovery.reconcile(item_id)

    def close(self) -> None:
        self.db.close()

    def process_next(self) -> int | None:
        item_id = self.ingest_once()
        if item_id is None:
            return None
        self.run(item_id)
        return item_id

    def recover_pending(self) -> list[int]:
        rows = self.db.conn.execute(
            "SELECT id FROM items WHERE state IN (?, ?)",
            (State.FAILED.value, State.RECOVERY.value),
        ).fetchall()
        recovered: list[int] = []
        for row in rows:
            self.recover(int(row["id"]))
            recovered.append(int(row["id"]))
        return recovered
