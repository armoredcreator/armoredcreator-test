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
        self._runtime_lock_held = False

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

    async def _ensure_source_connection(self) -> None:
        """Reconnect a real Telegram source before materializing the next item."""
        source = self.source
        reader = getattr(source, "reader", None)
        connect = getattr(reader, "connect", None)
        if connect is not None:
            is_connected = getattr(reader, "is_connected", None)
            if callable(is_connected):
                if is_connected():
                    return
            else:
                client = getattr(reader, "client", None)
                client_is_connected = getattr(client, "is_connected", None)
                if callable(client_is_connected) and client_is_connected():
                    return
            await connect()

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
            return
        disconnect = getattr(source, "disconnect", None)
        if disconnect is not None:
            await disconnect()

    async def ingest_once_async(self):
        if self.source is None:
            raise RuntimeError("Sync source não configurado")
        fetch_async = getattr(self.source, "fetch_next_async", None)
        message = await fetch_async() if fetch_async is not None else self.source.fetch_next()
        if message is None:
            await self._release_source_connection()
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
        """Process historical candidates incrementally, one item at a time."""
        processed: list[str] = []
        source = self.source

        # Stream real Telegram candidates while the Sync connection remains open.
        # Originals are materialized immediately, avoiding a full-history
        # in-memory batch and restoring the legacy Sync's early-download behavior.
        iter_historical = getattr(source, "iter_historical_candidates_async", None)
        if iter_historical is not None:
            processed: list[str] = []
            try:
                async for message in iter_historical():
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
            finally:
                await self._release_source_connection()

            commit = getattr(source, "commit_live_checkpoints", None)
            checkpoints = getattr(source, "_historical_checkpoints", None)
            if commit is not None and checkpoints:
                commit(dict(checkpoints))

            for item_id in processed:
                if self.db.get(str(item_id)).state == State.FAILED:
                    continue
                self.run(str(item_id))

            self.db.complete_historical_sync()
            return processed

        # Compatibility path for sources that still expose the older batch API.
        collect_batch = getattr(source, "collect_historical_batch_async", None)
        if collect_batch is not None:
            processed = []
            messages, checkpoints = await collect_batch()
            try:
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
            finally:
                await self._release_source_connection()

            commit = getattr(source, "commit_live_checkpoints", None)
            if commit is not None:
                commit(checkpoints)

            for item_id in processed:
                if self.db.get(str(item_id)).state == State.FAILED:
                    continue
                self.run(str(item_id))

            self.db.complete_historical_sync()
            return processed

        fetch_async = getattr(source, "fetch_next_async", None)

        if fetch_async is None:
            while True:
                item_id = await self.ingest_once_async()
                if item_id is None:
                    break
                processed.append(str(item_id))
                self.run(item_id)
            self.db.complete_historical_sync()
            return processed

        while True:
            message = await fetch_async()
            if message is None:
                self.db.complete_historical_sync()
                break

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

            # FAILED is a durable terminal/manual-retry state. If Sync
            # rediscovers its Telegram message during CATCH-UP, dedupe returns
            # the same item ID; never feed that FAILED item back into pipeline.
            if self.db.get(str(item_id)).state == State.FAILED:
                continue

            # Release Telegram before Hub opens the same Telethon session.
            # The source iterator is already materialized per topic and can
            # reconnect on the next fetch.
            await self._release_source_connection()
            self.run(str(item_id))

        return processed

    def run_catch_up(self) -> list[str]:
        import asyncio
        processed = asyncio.run(self.run_catch_up_async())
        # Adapters that expose the legacy synchronous/one-at-a-time source
        # contract do not own a Telegram checkpoint table. Their exhaustion
        # is itself the durable end-of-history signal.
        if not self.db.historical_complete():
            self.db.complete_historical_sync()
        return processed

    async def run_live_once_async(self) -> list[str]:
        source = self.source
        fetch_batch = getattr(source, "fetch_live_batch_async", None)
        if fetch_batch is None:
            return []

        # Materialize the complete LIVE batch while the Sync Telegram session
        # is connected. Telethon materializers depend on that live session and
        # must never run after disconnect. Only durable originals may cross the
        # Sync -> Hub boundary.
        messages, checkpoints = await fetch_batch()
        processed: list[str] = []
        try:
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
        finally:
            # Never leave the Sync Telethon session open while Hub/Studio run.
            # If materialization failed, checkpoints are intentionally not
            # committed and the interrupted items remain recoverable.
            await self._release_source_connection()

        # All originals are now durable. Process exactly one item at a time.
        for item_id in processed:
            if self.db.get(str(item_id)).state == State.FAILED.value:
                continue
            self.run(str(item_id))

        if checkpoints and len(processed) == len(messages):
            commit = getattr(source, "commit_live_checkpoints", None)
            if commit is not None:
                commit(checkpoints)
        return processed

    def run_live_once(self) -> list[str]:
        import asyncio
        return asyncio.run(self.run_live_once_async())

    def run_forever(self, poll_seconds: float = 2.0, max_cycles: int | None = None) -> None:
        """Recover, finish catch-up once, then monitor Telegram continuously.

        ``max_cycles`` is an optional deterministic test/service-run bound. The
        production default remains ``None`` (run until interrupted).
        """
        import asyncio
        self.db.acquire_runtime_lock("coordinator")
        self._runtime_lock_held = True
        try:
            # Do not pre-connect and disconnect Telethon here. Each
            # asyncio.run() owns a different event loop, while Telethon binds a
            # client to the loop used by its connection. The Sync adapter now
            # recreates its disconnected client before reconnecting on a new
            # loop. Startup recovery can therefore use the shared persistent
            # session non-interactively, and CATCH-UP/LIVE owns its own Sync
            # connection lifecycle.
            self.recover_pending()
            # SQLite is authoritative for CATCH-UP/LIVE state. This makes a
            # fresh process restart independent of in-memory Sync state.
            if (
                not self.db.historical_complete()
                or not self.db.has_sync_checkpoints()
            ):
                # A stale LIVE flag without checkpoints is not a valid LIVE
                # state. Rebuild CATCH_UP deterministically; existing items
                # are deduplicated by Telegram message ID.
                if self.db.historical_complete() and not self.db.has_sync_checkpoints():
                    self.db.set_sync_mode("CATCH_UP")
                self.run_catch_up()
            cycles = 0
            while max_cycles is None or cycles < max_cycles:
                self.db.heartbeat_runtime_lock("coordinator")
                processed = self.run_live_once()
                cycles += 1
                if not processed and (max_cycles is None or cycles < max_cycles):
                    asyncio.run(asyncio.sleep(float(poll_seconds)))
        finally:
            if self._runtime_lock_held:
                self.db.release_runtime_lock("coordinator")
                self._runtime_lock_held = False

    def run(self, item_id: str) -> None:
        self.pipeline.run(item_id)

    def recover(self, item_id: str) -> None:
        self.recovery.reconcile(item_id)

    def close(self) -> None:
        if self._runtime_lock_held:
            self.db.release_runtime_lock("coordinator")
            self._runtime_lock_held = False
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
            State.PUBLISHING.value, State.RECOVERY.value,
        )
        placeholders = ",".join("?" for _ in states)
        rows = self.db.conn.execute(
            f"SELECT content_id, state FROM items WHERE state IN ({placeholders}) ORDER BY created_at, content_id", states
        ).fetchall()
        recovered = []
        for row in rows:
            item_id = str(row["content_id"])
            # RECEIVED without an immutable original is a durable Telegram
            # reservation whose download was interrupted. The Sync source must
            # rediscover/materialize it; Recovery cannot invent the missing
            # bytes. Keep it in SQLite and let CATCH_UP/LIVE continue it.
            if str(row["state"]) == State.RECEIVED.value:
                item = self.db.get(item_id)
                if not item.original_path.is_file():
                    continue
            try:
                self.recover(item_id)
                recovered.append(item_id)
            except Exception as exc:
                # A single unrecoverable item must not terminate the Coordinator.
                # Pipeline failures are persisted in SQLite; startup continues
                # with the remaining pending items and LIVE discovery.
                import logging
                logging.getLogger(__name__).exception(
                    "Recovery falhou para item %s; Coordinator continuará: %s",
                    item_id,
                    exc,
                )
        return recovered
