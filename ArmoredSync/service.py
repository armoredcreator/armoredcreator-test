from __future__ import annotations

import asyncio
import os
import re
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
    """Deterministic offline source used by the lab and E2E tests."""

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
            return SyncMessage(
                path,
                message_id,
                "local",
                original_url=os.getenv("ARMORED_TEST_ORIGINAL_URL"),
            )
        return None


class TelegramReader:
    """Small path-safe Telethon adapter owned by ArmoredSync."""

    def __init__(self, root: Path, api_id: int, api_hash: str):
        try:
            from telethon import TelegramClient
        except ImportError as exc:
            raise RuntimeError("Dependência Telethon ausente; instale as dependências do Sync.") from exc
        session = Path(root) / "credentials" / "telegram" / "session" / "armoredsync"
        session.parent.mkdir(parents=True, exist_ok=True)
        self.client = TelegramClient(str(session), api_id, api_hash)

    async def connect(self):
        await self.client.start()

    async def disconnect(self):
        if self.client.is_connected():
            await self.client.disconnect()

    async def get_messages(self, source, limit=None):
        return self.client.iter_messages(source, limit=limit)


class TelegramSource:
    """Real Telegram video source.

    It downloads exactly one candidate at a time into the isolated workspace
    and returns a SyncMessage. SQLite/SyncService remains the canonical ingest
    boundary. No absolute project path is embedded in the source.
    """

    URL_RE = re.compile(r"https?://[^\s]+", re.IGNORECASE)

    def __init__(self, root: Path, reader: Any):
        self.root = Path(root)
        self.reader = reader
        self._iterator = None
        self._seen: set[int] = set()

    async def fetch_next_async(self) -> SyncMessage | None:
        source = os.getenv("ARMORED_SYNC_SOURCE")
        if not source:
            raise RuntimeError("ARMORED_SYNC_SOURCE não configurado")

        if self._iterator is None:
            await self.reader.connect()
            self._iterator = self.reader.get_messages(source, limit=None)

        async for message in self._iterator:
            message_id = int(getattr(message, "id", 0) or 0)
            if not message_id or message_id in self._seen:
                continue
            if not getattr(message, "video", None):
                continue

            self._seen.add(message_id)
            target = self.root / "storage" / "sync" / f"{message_id}.mp4"
            target.parent.mkdir(parents=True, exist_ok=True)
            if not target.exists() or target.stat().st_size <= 0:
                await message.download_media(file=str(target))
            if not target.exists() or target.stat().st_size <= 0:
                raise RuntimeError(f"Telegram não baixou o vídeo {message_id}")

            text = str(getattr(message, "message", "") or "")
            urls = self.URL_RE.findall(text)
            original_url = urls[0] if urls else None

            topic_id = getattr(message, "reply_to_top_id", None)
            if topic_id is None:
                reply = getattr(message, "reply_to", None)
                topic_id = getattr(reply, "reply_to_top_id", None) if reply else None

            topic_name = os.getenv("ARMORED_SYNC_TOPIC_NAME")
            return SyncMessage(
                target,
                str(message_id),
                "telegram",
                int(topic_id) if topic_id else None,
                topic_name,
                original_url,
            )
        return None

    def fetch_next(self) -> SyncMessage | None:
        return asyncio.run(self.fetch_next_async())


class ArmoredSync:
    """Source + canonical ingestion boundary."""

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
