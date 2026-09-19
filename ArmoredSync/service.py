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

    def get_messages(self, source, limit=None):
        return self.client.iter_messages(source, limit=limit)


class TelegramSource:
    """Fonte real do ArmoredSync, preservando a coleta validada do backup.

    A fonte oficial é um supergrupo Telegram com fórum. O Sync descobre os
    tópicos dinamicamente e lê cada tópico via GetRepliesRequest, em vez de
    varrer o grupo inteiro como uma lista plana.

    Regras preservadas:
    - vídeo + link Shopee na própria mensagem; ou
    - vídeo + mensagem imediatamente seguinte contendo o link Shopee;
    - o message_id canônico é o da mensagem do vídeo;
    - um candidato é baixado por vez;
    - SQLite/SyncService continua sendo a fronteira canônica de ingestão.

    A identidade da fonte continua configurável por ambiente e não depende de
    caminho local ou checkout antigo.
    """

    URL_RE = re.compile(r"https?://[^\\s]+", re.IGNORECASE)
    SHOPEE_DOMAINS = ("shopee.com.br", "shopee.co", "shopee.ee")

    def __init__(self, root: Path, reader: Any):
        self.root = Path(root)
        self.reader = reader
        self._seen: set[int] = set()
        self._topic_iterator = None

    @staticmethod
    def _shopee_url(message: Any) -> str | None:
        if not message:
            return None
        text = str(getattr(message, "message", "") or "")
        for url in TelegramSource.URL_RE.findall(text):
            cleaned = url.rstrip(".,!?;:" + chr(34) + "'()[]{}<>")
            lowered = cleaned.lower()
            if any(domain in lowered for domain in TelegramSource.SHOPEE_DOMAINS):
                return cleaned
        for entity in (getattr(message, "entities", None) or []):
            url = getattr(entity, "url", None)
            if url and any(domain in str(url).lower() for domain in TelegramSource.SHOPEE_DOMAINS):
                return str(url)
        return None

    @staticmethod
    def _topic_id(message: Any) -> int | None:
        topic_id = getattr(message, "reply_to_top_id", None)
        if topic_id is None:
            reply = getattr(message, "reply_to", None)
            topic_id = getattr(reply, "reply_to_top_id", None) if reply else None
        return int(topic_id) if topic_id else None

    async def _discover_topics(self, source: str) -> list[tuple[int, str]]:
        from telethon import functions

        result = await self.reader.client(
            functions.messages.GetForumTopicsRequest(
                peer=source,
                q=None,
                offset_date=None,
                offset_id=0,
                offset_topic=0,
                limit=100,
            )
        )

        topics: list[tuple[int, str]] = []
        for topic in getattr(result, "topics", []) or []:
            topic_id = getattr(topic, "id", None)
            if topic_id is None:
                continue
            title = str(getattr(topic, "title", None) or topic_id).strip()
            topics.append((int(topic_id), title))
        return topics

    async def _topic_messages(self, source: str, topic_id: int):
        from telethon import functions

        offset_id = 0
        while True:
            result = await self.reader.client(
                functions.messages.GetRepliesRequest(
                    peer=source,
                    msg_id=topic_id,
                    offset_id=offset_id,
                    offset_date=None,
                    add_offset=0,
                    limit=100,
                    max_id=0,
                    min_id=0,
                    hash=0,
                )
            )
            messages = list(getattr(result, "messages", []) or [])
            if not messages:
                return
            for message in messages:
                yield message
            ids = [int(getattr(m, "id", 0) or 0) for m in messages]
            ids = [value for value in ids if value > 0]
            if not ids:
                return
            oldest = min(ids)
            if oldest == offset_id:
                return
            offset_id = oldest

    async def fetch_next_async(self) -> SyncMessage | None:
        source = os.getenv("ARMORED_SYNC_SOURCE", "-1003788989075")
        source_id = os.getenv("ARMORED_SYNC_SOURCE_ID", source)

        if self._topic_iterator is None:
            await self.reader.connect()
            topics = await self._discover_topics(source)
            if not topics:
                raise RuntimeError(f"Nenhum tópico de fórum encontrado na fonte Telegram {source}.")
            self._topic_iterator = self._candidate_iterator(source, topics)

        async for candidate in self._topic_iterator:
            message_id, topic_id, topic_name, message, original_url = candidate
            if message_id in self._seen:
                continue

            target = self.root / "storage" / "sync" / f"{message_id}.mp4"
            target.parent.mkdir(parents=True, exist_ok=True)
            if not target.exists() or target.stat().st_size <= 0:
                await message.download_media(file=str(target))
            if not target.exists() or target.stat().st_size <= 0:
                raise RuntimeError(f"Telegram não baixou o vídeo {message_id}")

            self._seen.add(message_id)
            return SyncMessage(
                target,
                str(message_id),
                source_id,
                topic_id,
                topic_name,
                original_url,
            )
        return None

    async def _candidate_iterator(self, source: str, topics: list[tuple[int, str]]):
        for topic_id, topic_name in topics:
            messages = [message async for message in self._topic_messages(source, topic_id)]
            for index, message in enumerate(messages):
                if not getattr(message, "video", None):
                    continue

                original_url = self._shopee_url(message)

                if original_url is None and index + 1 < len(messages):
                    next_message = messages[index + 1]
                    if not getattr(next_message, "video", None):
                        original_url = self._shopee_url(next_message)

                if original_url is None:
                    continue

                yield (
                    int(getattr(message, "id", 0) or 0),
                    int(topic_id),
                    topic_name,
                    message,
                    original_url,
                )

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
