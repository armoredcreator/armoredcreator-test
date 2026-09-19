from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from armored_core.services import SyncService


@dataclass(frozen=True)
class SyncMessage:
    source_path: Path
    telegram_message_id: str
    source_id: str = "telegram"
    topic_id: int | None = None
    topic_name: str | None = None
    original_url: str | None = None


class LocalSource:
    """Deterministic source used by the isolated lab and E2E tests."""

    def __init__(self, root: Path):
        self.root = Path(root)
        self._seen: set[str] = set()

    def fetch_next(self) -> SyncMessage | None:
        self.root.mkdir(parents=True, exist_ok=True)
        for path in sorted(self.root.iterdir()):
            if not path.is_file() or path.suffix.lower() not in {".mp4", ".mov", ".mkv"}:
                continue
            message_id = path.stem
            if message_id in self._seen:
                continue
            self._seen.add(message_id)
            return SyncMessage(path, message_id, "local", original_url=os.getenv("ARMORED_TEST_ORIGINAL_URL"))
        return None


class TelegramSource:
    """Optional real Telegram source. Imported lazily so the lab stays offline-safe."""

    def __init__(self, reader: Any):
        self.reader = reader
        self._iterator = None

    async def fetch_next_async(self) -> SyncMessage | None:
        if self._iterator is None:
            source = os.getenv("ARMORED_SYNC_SOURCE")
            if not source:
                raise RuntimeError("ARMORED_SYNC_SOURCE não configurado")
            self._iterator = self.reader.get_messages(source, limit=None)
        async for message in self._iterator:
            if not getattr(message, "video", None):
                continue
            raise RuntimeError(
                "TelegramSource precisa de um downloader explícito para preservar "
                "o contrato imutável do Sync; use LocalSource no laboratório."
            )
        return None

    def fetch_next(self) -> SyncMessage | None:
        return asyncio.run(self.fetch_next_async())


class ArmoredSync:
    """Fonte + ingestão canônica. SQLite continua sendo a fonte de verdade."""

    def __init__(self, sync: SyncService, source: Any):
        self.sync = sync
        self.source = source

    def fetch_next(self) -> SyncMessage | None:
        value = self.source.fetch_next()
        if hasattr(value, "__await__"):
            value = asyncio.run(value)
        return value

    def ingest(self, message: SyncMessage) -> int:
        return self.sync.ingest(
            message.source_path,
            message.telegram_message_id,
            message.source_id,
            message.topic_id,
            message.topic_name,
            message.original_url,
        )
