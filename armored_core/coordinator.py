from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from .database import Database
from .models import State
from .pipeline import Pipeline
from .recovery import Recovery
from .services import IngestMessage, SyncService
from .storage import Storage


class Coordinator:
    """Single composition root for the isolated ArmoredCreator pipeline."""

    def __init__(self, db, storage, vision, studio, publisher, source=None):
        self.db = db
        self.storage = storage
        self.sync = SyncService(db, storage)
        self.pipeline = Pipeline(db, storage, vision, studio, publisher)
        self.recovery = Recovery(db, storage, vision, studio, publisher)
        self.source = source

    @classmethod
    def build(cls, root: Path | None = None, bindings: Any | None = None):
        storage = Storage(root)
        for credential_file in (
            storage.root / "credentials" / "telegram" / "user.env",
            storage.root / "credentials" / "telegram" / "bot.env",
            storage.root / "credentials" / "shopee" / "affiliate.env",
            storage.root / ".env",
        ):
            if credential_file.exists():
                load_dotenv(credential_file, override=False)

        db = Database(storage.database / "armoredcreator.db")
        if bindings is None:
            from ArmoredHub.service import ArmoredHub
            from ArmoredStudio.service import ArmoredStudio
            from ArmoredVision.service import ArmoredVision
            from ArmoredSync.service import LocalSource, TelegramReader, TelegramSource

            vision = ArmoredVision()
            studio = ArmoredStudio(storage.root)
            publisher = ArmoredHub(storage.root, db)
            if os.getenv("ARMORED_REAL_TELEGRAM", "0") == "1":
                api_id = os.getenv("TELEGRAM_API_ID")
                api_hash = os.getenv("TELEGRAM_API_HASH")
                if not api_id or not api_hash:
                    raise RuntimeError("TELEGRAM_API_ID e TELEGRAM_API_HASH são obrigatórios")
                reader = TelegramReader(storage.root, int(api_id), api_hash)
                source = TelegramSource(storage.root, reader, db)
            else:
                source = LocalSource(storage.root / "input")
            return cls(db, storage, vision, studio, publisher, source)
        return cls(db, storage, bindings.vision, bindings.studio, bindings.publisher, bindings.source)

    async def _release_source_connection(self) -> None:
        """Release a real Telegram Sync session before Hub opens the same session.

        Telethon stores the user session in SQLite. Keeping ArmoredSync connected
        while ArmoredHub opens that same session causes a Windows SQLite lock.
        Ingestion/materialization is complete before this method is called, so
        releasing the connection does not interrupt the canonical pipeline.
        """
        source = self.source
        reader = getattr(source, "reader", None)
        disconnect = getattr(reader, "disconnect", None)
        if disconnect is not None:
            await disconnect()

    async def ingest_once_async(self):
        if self.source is None:
            raise RuntimeError("Sync source não configurado")
        fetch_async = getattr(self.source, "fetch_next_async", None)
        message = await fetch_async() if fetch_async is not None else self.source.fetch_next()
        if message is None:
            return None
        item_id = await self.sync.ingest_message_async(IngestMessage(
            telegram_message_id=str(message.telegram_message_id),
            source_id=getattr(message, "source_id", "telegram"),
            topic_id=getattr(message, "topic_id", None),
            topic_name=getattr(message, "topic_name", None),
            original_url=getattr(message, "original_url", None),
            source_path=getattr(message, "source_path", None),
            materialize=getattr(message, "materialize", None),
        ))
        marker = getattr(self.source, "mark_ingested", None)
        if marker is not None:
            marker(str(message.telegram_message_id))
        await self._release_source_connection()
        return item_id

    def ingest_once(self):
        if self.source is None:
            raise RuntimeError("Sync source não configurado")
        if getattr(self.source, "fetch_next_async", None) is not None:
            import asyncio
            return asyncio.run(self.ingest_once_async())
        message = self.source.fetch_next()
        if message is None:
            return None
        item_id = self.sync.ingest_message(IngestMessage(
            telegram_message_id=str(message.telegram_message_id),
            source_id=getattr(message, "source_id", "telegram"),
            topic_id=getattr(message, "topic_id", None),
            topic_name=getattr(message, "topic_name", None),
            original_url=getattr(message, "original_url", None),
            source_path=getattr(message, "source_path", None),
            materialize=getattr(message, "materialize", None),
        ))
        marker = getattr(self.source, "mark_ingested", None)
        if marker is not None:
            marker(str(message.telegram_message_id))
        reader = getattr(self.source, "reader", None)
        disconnect = getattr(reader, "disconnect", None)
        if disconnect is not None:
            import asyncio
            asyncio.run(disconnect())
        return item_id

    async def run_catch_up_async(self) -> list[str]:
        """Drain Telegram history, processing each item sequentially.

        Sync may discover the next historical item only after the previous
        item has been ingested; the pipeline itself remains strictly one-item
        active at a time.
        """
        processed: list[str] = []
        while True:
            item_id = await self.ingest_once_async()
            if item_id is None:
                break
            self.run(item_id)
            processed.append(str(item_id))
        return processed

    def run_catch_up(self) -> list[str]:
        import asyncio
        return asyncio.run(self.run_catch_up_async())

    async def run_live_once_async(self) -> list[str]:
        source = self.source
        fetch_batch = getattr(source, "fetch_live_batch_async", None)
        if fetch_batch is None:
            return []
        messages, checkpoints = await fetch_batch()
        processed: list[str] = []
        for message in messages:
            item_id = await self.sync.ingest_message_async(IngestMessage(
                telegram_message_id=str(message.telegram_message_id),
                source_id=getattr(message, "source_id", "telegram"),
                topic_id=getattr(message, "topic_id", None),
                topic_name=getattr(message, "topic_name", None),
                original_url=getattr(message, "original_url", None),
                source_path=getattr(message, "source_path", None),
                materialize=getattr(message, "materialize", None),
            ))
            marker = getattr(source, "mark_ingested", None)
            if marker is not None:
                marker(str(message.telegram_message_id))
            processed.append(str(item_id))

        commit = getattr(source, "commit_live_checkpoints", None)
        if commit is not None:
            commit(checkpoints)
        await self._release_source_connection()

        for item_id in processed:
            self.run(item_id)
        return processed

    def run_live_once(self) -> list[str]:
        import asyncio
        return asyncio.run(self.run_live_once_async())

    def run_forever(self, poll_seconds: float = 2.0) -> None:
        """Recover, finish catch-up once, then monitor Telegram continuously."""
        import asyncio
        self.recover_pending()
        if not getattr(self.source, "is_historical_complete", lambda: False)():
            self.run_catch_up()
        while True:
            processed = self.run_live_once()
            if not processed:
                asyncio.run(asyncio.sleep(float(poll_seconds)))

    def run(self, item_id: str) -> None:
        self.pipeline.run(item_id)

    def recover(self, item_id: str) -> None:
        self.recovery.reconcile(item_id)

    def close(self) -> None:
        self.db.close()

    def process_next(self):
        item_id = self.ingest_once()
        if item_id is None:
            return None
        self.run(item_id)
        return item_id

    def recover_pending(self):
        states = (
            State.RECEIVED.value, State.VISION.value, State.STUDIO.value,
            State.PUBLISHING.value, State.RECOVERY.value, State.FAILED.value,
        )
        placeholders = ",".join("?" for _ in states)
        rows = self.db.conn.execute(
            f"SELECT content_id FROM items WHERE state IN ({placeholders}) ORDER BY created_at, content_id", states
        ).fetchall()
        recovered = []
        for row in rows:
            item_id = str(row["content_id"])
            self.recover(item_id)
            recovered.append(item_id)
        return recovered
