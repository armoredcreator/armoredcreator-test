from __future__ import annotations

import asyncio
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from armored_core.database import Database
from armored_core.services import IngestMessage, SyncService


@dataclass(frozen=True)
class SyncMessage:
    telegram_message_id: str
    source_id: str = "telegram"
    topic_id: int | None = None
    topic_name: str | None = None
    original_url: str | None = None
    source_path: Path | None = None
    materialize: Any | None = None


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
                telegram_message_id=message_id,
                source_id="local",
                original_url=os.getenv("ARMORED_TEST_ORIGINAL_URL"),
                source_path=path,
            )
        return None


class TelegramReader:
    """Small path-safe Telethon adapter owned by ArmoredSync."""

    def __init__(self, root: Path, api_id: int, api_hash: str):
        try:
            from telethon import TelegramClient
        except ImportError as exc:
            raise RuntimeError("Dependência Telethon ausente; instale as dependências do Sync.") from exc

        self._session = Path(root) / "credentials" / "telegram" / "session" / "armoredsync"
        self._api_id = api_id
        self._api_hash = api_hash
        self._TelegramClient = TelegramClient
        self._build_client()

    def _build_client(self) -> None:
        self._session.parent.mkdir(parents=True, exist_ok=True)
        self.client = self._TelegramClient(str(self._session), self._api_id, self._api_hash)

    async def connect(self):
        # Telethon binds a client to the event loop used by its first
        # connection. Coordinator intentionally runs bounded async operations
        # with separate asyncio.run() calls, so a disconnected client must be
        # recreated before reconnecting on a new loop.
        if self.client.is_connected():
            return
        self._build_client()
        await self.client.start()

    async def disconnect(self):
        if self.client.is_connected():
            await self.client.disconnect()

    def get_messages(self, source, limit=None):
        return self.client.iter_messages(source, limit=limit)


class TelegramSource:
    """Fonte real do ArmoredSync.

    Importante: o Sync não baixa para uma pasta própria. Ele somente descobre
    o candidato e entrega ao Core um materializer que grava diretamente no
    workspace canônico storage/videos/{item_id}.
    """

    URL_RE = re.compile(r"https?://[^\s]+", re.IGNORECASE)
    SHOPEE_DOMAINS = ("shopee.com.br", "shopee.co", "shopee.ee")

    def __init__(self, root: Path, reader: Any, db: Database | None = None):
        self.root = Path(root)
        self.reader = reader
        self.db = db
        self._seen: set[int] = set()
        self._topic_iterator = None
        self._topics: list[tuple[int, str]] | None = None
        self._historical_complete = False
        self._historical_checkpoints: dict[int, int] = {}

    @property
    def mode(self) -> str:
        return self.db.sync_mode() if self.db is not None else ("LIVE" if self._historical_complete else "CATCH_UP")

    def mark_historical_complete(self) -> None:
        if self.db is not None:
            self.db.complete_historical_sync()
        self._historical_complete = True
        self._historical_checkpoints.clear()

    def is_historical_complete(self) -> bool:
        return self.mode == "LIVE"

    @staticmethod
    def _shopee_url(message: Any) -> str | None:
        if not message:
            return None
        text = str(getattr(message, "message", "") or "")
        for url in TelegramSource.URL_RE.findall(text):
            cleaned = url.rstrip(".,!?;:" + chr(34) + "'()[]{}<>")
            if any(domain in cleaned.lower() for domain in TelegramSource.SHOPEE_DOMAINS):
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
            if topic_id is not None:
                topics.append((int(topic_id), str(getattr(topic, "title", None) or topic_id).strip()))
        return topics

    async def _topic_messages(self, source: str, topic_id: int):
        """Yield historical messages page-by-page.

        The backup processed Telegram pages incrementally. Do the same here:
        never build the complete topic history in memory before candidate
        detection. The caller keeps the Telegram connection open for the whole
        catch-up batch, so candidates can still be materialized safely.
        """
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

    async def _download_to(self, message: Any, target: Path) -> None:
        """Materialize one Telegram video with bounded, observable download.

        The legacy Sync used a 180s download timeout. Keep that protection in
        the unified pipeline so a stalled Telegram transfer cannot make the
        Coordinator appear frozen forever.
        """
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            target.unlink()

        telegram_size = getattr(getattr(message, "document", None), "size", None)
        timeout = max(30, int(os.getenv("ARMORED_SYNC_DOWNLOAD_TIMEOUT", "180")))
        started = asyncio.get_running_loop().time()
        downloaded = 0
        last_report = 0

        async def consume() -> None:
            nonlocal downloaded, last_report
            with target.open("wb") as output:
                async for chunk in self.reader.client.iter_download(
                    message,
                    request_size=1024 * 1024,
                ):
                    if not chunk:
                        continue
                    output.write(chunk)
                    downloaded += len(chunk)
                    now = asyncio.get_running_loop().time()
                    if now - last_report >= 5:
                        last_report = now
                        if telegram_size:
                            pct = downloaded * 100.0 / int(telegram_size)
                            print(
                                f"[SYNC][DOWNLOAD] {getattr(message, 'id', '?')} "
                                f"{downloaded / 1048576:.1f}/{int(telegram_size) / 1048576:.1f} MiB "
                                f"({pct:.0f}%)"
                            )
                        else:
                            print(
                                f"[SYNC][DOWNLOAD] {getattr(message, 'id', '?')} "
                                f"{downloaded / 1048576:.1f} MiB"
                            )

        try:
            await asyncio.wait_for(consume(), timeout=timeout)
        except asyncio.TimeoutError as exc:
            raise TimeoutError(
                f"download Telegram excedeu {timeout}s para mensagem {getattr(message, 'id', '?')}"
            ) from exc

        actual_size = target.stat().st_size if target.exists() else 0
        elapsed = max(asyncio.get_running_loop().time() - started, 0.001)
        if actual_size <= 0:
            raise RuntimeError("download retornou arquivo vazio")
        if telegram_size is not None and actual_size != int(telegram_size):
            raise RuntimeError(f"download incompleto: {actual_size} bytes de {int(telegram_size)}")
        print(
            f"[SYNC][DOWNLOAD] {getattr(message, 'id', '?')} concluído "
            f"{actual_size / 1048576:.1f} MiB em {elapsed:.1f}s"
        )

    async def fetch_next_async(self) -> SyncMessage | None:
        if self.is_historical_complete():
            return None
        source = (os.getenv("ARMORED_SYNC_SOURCE") or "-1003788989075").strip()
        source_id = (os.getenv("ARMORED_SYNC_SOURCE_ID") or source).strip()
        source_ref = int(source) if str(source).lstrip("-").isdigit() else source

        if not self.reader.client.is_connected():
            await self.reader.connect()

        if self._topic_iterator is None:
            topics = await self._discover_topics(source_ref)
            if not topics:
                raise RuntimeError(f"Nenhum tópico de fórum encontrado na fonte Telegram {source}.")
            self._topic_iterator = self._candidate_iterator(source_ref, topics)

        async for candidate in self._topic_iterator:
            message_id, topic_id, topic_name, message, original_url = candidate
            if message_id in self._seen:
                continue
            return SyncMessage(
                telegram_message_id=str(message_id),
                source_id=source_id,
                topic_id=topic_id,
                topic_name=topic_name,
                original_url=original_url,
                materialize=lambda target, m=message: self._download_to(m, target),
            )
        if self.db is not None and self._historical_checkpoints:
            self.commit_live_checkpoints(self._historical_checkpoints)
        self.mark_historical_complete()
        return None

    async def _candidate_iterator(self, source: str, topics: list[tuple[int, str]]):
        for topic_id, topic_name in topics:
            messages = [message async for message in self._topic_messages(source, topic_id)]
            ids = [int(getattr(message, "id", 0) or 0) for message in messages]
            if ids:
                self._historical_checkpoints[topic_id] = max(ids)
            for index, message in enumerate(messages):
                if not getattr(message, "video", None):
                    continue

                # Preserva exatamente o comportamento do backup:
                # 1) vídeo + Shopee na mesma mensagem; ou
                # 2) vídeo + Shopee na mensagem imediatamente seguinte.
                original_url = self._shopee_url(message)

                if original_url is None and index + 1 < len(messages):
                    next_message = messages[index + 1]
                    next_is_video = bool(getattr(next_message, "video", None))
                    if not next_is_video:
                        original_url = self._shopee_url(next_message)

                if original_url is None:
                    continue

                yield (int(getattr(message, "id", 0) or 0), int(topic_id), topic_name, message, original_url)

    async def collect_historical_batch_async(self) -> tuple[list[SyncMessage], dict[int, int]]:
        """Collect the complete historical candidate set while one Telegram
        connection is open, then let the Coordinator process it sequentially.
        """
        if self.is_historical_complete():
            return [], {}

        source = (os.getenv("ARMORED_SYNC_SOURCE") or "-1003788989075").strip()
        source_id = (os.getenv("ARMORED_SYNC_SOURCE_ID") or source).strip()
        source_ref = int(source) if str(source).lstrip("-").isdigit() else source

        await self.reader.connect()
        try:
            topics = await self._discover_topics(source_ref)
            if not topics:
                raise RuntimeError(f"Nenhum tópico de fórum encontrado na fonte Telegram {source}.")

            candidates: list[SyncMessage] = []
            checkpoints: dict[int, int] = {}

            for topic_id, topic_name in topics:
                # Telethon returns this history newest-first. The legacy
                # collector associated a video with the immediately following
                # element in that returned sequence, so keep one look-ahead
                # message while streaming pages.
                pending_video = None
                topic_max_id = 0
                candidate_ids: set[int] = set()

                async for message in self._topic_messages(source_ref, topic_id):
                    message_id = int(getattr(message, "id", 0) or 0)
                    if message_id > topic_max_id:
                        topic_max_id = message_id
                    if message_id <= 0:
                        continue

                    if pending_video is not None:
                        pending_id, pending_message = pending_video
                        if pending_id not in self._seen and pending_id not in candidate_ids:
                            original_url = self._shopee_url(pending_message)
                            if original_url is None and not getattr(message, "video", None):
                                original_url = self._shopee_url(message)
                            if original_url is not None:
                                candidates.append(SyncMessage(
                                    telegram_message_id=str(pending_id),
                                    source_id=source_id,
                                    topic_id=topic_id,
                                    topic_name=topic_name,
                                    original_url=original_url,
                                    materialize=lambda target, m=pending_message: self._download_to(m, target),
                                ))
                                candidate_ids.add(pending_id)
                        pending_video = None

                    if not getattr(message, "video", None):
                        continue

                    # Same-message video + Shopee has priority.
                    original_url = self._shopee_url(message)
                    if original_url is not None:
                        if message_id not in self._seen and message_id not in candidate_ids:
                            candidates.append(SyncMessage(
                                telegram_message_id=str(message_id),
                                source_id=source_id,
                                topic_id=topic_id,
                                topic_name=topic_name,
                                original_url=original_url,
                                materialize=lambda target, m=message: self._download_to(m, target),
                            ))
                            candidate_ids.add(message_id)
                        continue

                    # Otherwise wait for exactly the next message in the
                    # Telegram result sequence, matching the BACKUP behavior.
                    pending_video = (message_id, message)

                if topic_max_id:
                    checkpoints[topic_id] = topic_max_id
            return candidates, checkpoints
        except Exception:
            await self.reader.disconnect()
            raise

    async def fetch_live_batch_async(self) -> tuple[list[SyncMessage], dict[int, int]]:
        """Discover all new candidates since the persisted topic checkpoints."""
        source = (os.getenv("ARMORED_SYNC_SOURCE") or "-1003788989075").strip()
        source_id = (os.getenv("ARMORED_SYNC_SOURCE_ID") or source).strip()
        source_ref = int(source) if str(source).lstrip("-").isdigit() else source

        await self.reader.connect()
        try:
            if self._topics is None:
                self._topics = await self._discover_topics(source_ref)
            if not self._topics:
                raise RuntimeError(f"Nenhum tópico de fórum encontrado na fonte Telegram {source}.")

            candidates: list[SyncMessage] = []
            checkpoints: dict[int, int] = {}
            for topic_id, topic_name in self._topics:
                checkpoint = self.db.sync_topic_checkpoint(topic_id) if self.db is not None else 0
                min_id = max(0, checkpoint - 1)
                messages = []
                async for message in self.reader.client.iter_messages(
                    source_ref, reply_to=topic_id, min_id=min_id, reverse=True
                ):
                    messages.append(message)

                ids = [int(getattr(message, "id", 0) or 0) for message in messages]
                if ids:
                    checkpoints[topic_id] = max(checkpoint, max(ids))

                for index, message in enumerate(messages):
                    message_id = int(getattr(message, "id", 0) or 0)
                    if message_id <= 0 or message_id in self._seen:
                        continue
                    if not getattr(message, "video", None):
                        continue

                    original_url = self._shopee_url(message)
                    if original_url is None and index + 1 < len(messages):
                        next_message = messages[index + 1]
                        if not getattr(next_message, "video", None):
                            original_url = self._shopee_url(next_message)
                    if original_url is None:
                        continue

                    candidates.append(SyncMessage(
                        telegram_message_id=str(message_id),
                        source_id=source_id,
                        topic_id=topic_id,
                        topic_name=topic_name,
                        original_url=original_url,
                        materialize=lambda target, m=message: self._download_to(m, target),
                    ))
            return candidates, checkpoints
        except Exception:
            await self.reader.disconnect()
            raise

    def commit_live_checkpoints(self, checkpoints: dict[int, int]) -> None:
        if self.db is None:
            return
        for topic_id, message_id in checkpoints.items():
            topic_name = next(
                (name for tid, name in (self._topics or []) if tid == topic_id),
                str(topic_id),
            )
            self.db.set_sync_topic_checkpoint(topic_id, topic_name, message_id)

    def fetch_live_batch(self) -> tuple[list[SyncMessage], dict[int, int]]:
        return asyncio.run(self.fetch_live_batch_async())

    def mark_ingested(self, telegram_message_id: str) -> None:
        self._seen.add(int(telegram_message_id))

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
        return self.sync.ingest_message(
            IngestMessage(
                telegram_message_id=message.telegram_message_id,
                source_id=message.source_id,
                topic_id=message.topic_id,
                topic_name=message.topic_name,
                original_url=message.original_url,
                source_path=message.source_path,
                materialize=message.materialize,
            )
        )
