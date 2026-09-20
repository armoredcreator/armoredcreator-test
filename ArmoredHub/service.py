from __future__ import annotations

import hashlib
import os
from datetime import datetime, timezone
from pathlib import Path

from armored_core.database import Database
from armored_core.models import Item, PublicationCheck
from armored_core.services import PublicationResult


class ArmoredHub:
    """Telegram publication boundary; SQLite is the only durable state."""

    def __init__(self, root: Path, db: Database | None = None):
        self.root = Path(root)
        self.db = db

    def _publication(self, item: Item):
        return self.db.publication(item.content_id) if self.db is not None else None

    def check_publication(self, item: Item) -> PublicationCheck:
        record = self._publication(item)
        if record is None:
            return PublicationCheck.ABSENT
        if record["confirmed"]:
            return PublicationCheck.CONFIRMED

        # A publication row without confirmation means a previous attempt had
        # an unresolved external outcome. Never send again blindly.
        if os.getenv("ARMORED_HUB_VERIFY_TELEGRAM", "0") == "1":
            message_id = record["published_message_id"]
            if message_id and self._verify_telegram_message(message_id):
                self.db.publication_confirmed(item.content_id, str(message_id))
                return PublicationCheck.CONFIRMED
        return PublicationCheck.UNKNOWN

    def _telegram_session_path(self) -> Path:
        """Reuse the existing ArmoredSync user session for destination discovery."""
        return self.root / "credentials" / "telegram" / "session" / "armoredsync"

    def _resolve_destination_chat_id(self, topic_id: str | int | None = None) -> str | None:
        """Resolve the destination forum's parent chat when its ID is not configured.

        The backup contract fixes the publication topic as 228, but the parent
        group ID is not stored in the repository. When ARMORED_CREATOR_GROUP_ID
        is absent, inspect the already-authenticated Telegram user session and
        find the unique forum containing the configured topic.
        """
        configured = (os.getenv("ARMORED_CREATOR_GROUP_ID") or "").strip()
        if configured:
            return configured

        topic_value = str(topic_id or (os.getenv("ARMORED_HUB_TOPIC_ID") or "228")).strip()
        if not topic_value or not topic_value.lstrip("-").isdigit():
            raise RuntimeError("ARMORED_HUB_TOPIC_ID inválido para descoberta automática")

        api_id = os.getenv("TELEGRAM_API_ID")
        api_hash = os.getenv("TELEGRAM_API_HASH")
        if not api_id or not api_hash:
            raise RuntimeError(
                "Descoberta automática do grupo exige TELEGRAM_API_ID e TELEGRAM_API_HASH"
            )

        async def discover() -> str | None:
            try:
                from telethon import TelegramClient, functions
            except ImportError as exc:
                raise RuntimeError("Dependência Telethon ausente para descoberta do Hub") from exc

            session = self._telegram_session_path()
            session.parent.mkdir(parents=True, exist_ok=True)
            client = TelegramClient(str(session), int(api_id), api_hash)
            matches: list[str] = []
            try:
                await client.start()
                async for dialog in client.iter_dialogs():
                    entity = getattr(dialog, "entity", None)
                    if entity is None:
                        continue

                    # Telegram forum topics belong to supergroups/channels
                    # represented by Channel entities. Avoid scanning users,
                    # private chats and ordinary groups.
                    if not bool(getattr(entity, "megagroup", False) or getattr(entity, "forum", False)):
                        continue

                    try:
                        result = await client(
                            functions.messages.GetForumTopicsRequest(
                                peer=entity,
                                q=None,
                                offset_date=None,
                                offset_id=0,
                                offset_topic=0,
                                limit=100,
                            )
                        )
                    except Exception:
                        # Some dialogs visible to the user may not expose forum
                        # topics to this account. They are simply not candidates.
                        continue

                    for topic in getattr(result, "topics", []) or []:
                        if str(getattr(topic, "id", "")) == topic_value:
                            matches.append(str(dialog.id))
                            break

                unique_matches = sorted(set(matches))
                if len(unique_matches) == 1:
                    return unique_matches[0]
                if not unique_matches:
                    raise RuntimeError(
                        f"Nenhum grupo-fórum com tópico {topic_value} foi encontrado "
                        "pela sessão Telegram existente"
                    )
                raise RuntimeError(
                    f"Tópico {topic_value} encontrado em múltiplos grupos: "
                    + ", ".join(unique_matches)
                )
            finally:
                if client.is_connected():
                    await client.disconnect()

        return __import__("asyncio").run(discover())

    def _verify_telegram_message(self, message_id: str) -> bool:
        api_id = os.getenv("TELEGRAM_API_ID")
        api_hash = os.getenv("TELEGRAM_API_HASH")
        chat_id = self._resolve_destination_chat_id()
        if not message_id or not api_id or not api_hash or not chat_id:
            return False

        async def verify():
            try:
                from telethon import TelegramClient
            except ImportError:
                return False
            session = self._telegram_session_path()
            session.parent.mkdir(parents=True, exist_ok=True)
            client = TelegramClient(str(session), int(api_id), api_hash)
            try:
                await client.start()
                message = await client.get_messages(int(chat_id), ids=int(message_id))
                return message is not None and bool(getattr(message, "id", None))
            finally:
                if client.is_connected():
                    await client.disconnect()

        return __import__("asyncio").run(verify())

    def publish(self, item: Item) -> PublicationResult:
        if self.db is None:
            raise RuntimeError("ArmoredHub exige Database para publicação idempotente")

        existing = self.db.publication(item.item_id)
        if existing and existing["confirmed"]:
            return PublicationResult(True, existing["published_message_id"])

        if existing is None:
            self.db.publication_started(item.item_id)

        output = Path(item.result_path or "")
        if not output.is_file() or output.stat().st_size <= 0:
            raise RuntimeError("Hub recebeu resultado inexistente/vazio")

        if os.getenv("ARMORED_HUB_DRY_RUN", "1") == "1":
            digest = hashlib.sha256(output.read_bytes()).hexdigest()
            message_id = f"dry-{item.item_id}"
            self.db.publication_confirmed(item.item_id, message_id)
            return PublicationResult(True, message_id)

        return self._publish_telegram(item, output)

    def _publish_telegram(self, item: Item, output: Path) -> PublicationResult:
        token = os.getenv("ARMORED_CREATOR_BOT_TOKEN")
        topic_id = (os.getenv("ARMORED_HUB_TOPIC_ID") or "228").strip()
        if not token or not topic_id:
            raise RuntimeError(
                "Telegram Hub exige ARMORED_CREATOR_BOT_TOKEN e ARMORED_HUB_TOPIC_ID"
            )
        chat_id = self._resolve_destination_chat_id(topic_id)

        try:
            from telegram import Bot
            from telegram.error import NetworkError, TimedOut
            from telegram.request import HTTPXRequest
        except ImportError as exc:
            raise RuntimeError(
                "Dependência python-telegram-bot ausente; instale as dependências do Hub."
            ) from exc

        async def send():
            request = HTTPXRequest(
                connection_pool_size=int(os.getenv("ARMORED_TELEGRAM_CONNECTION_POOL_SIZE", "4")),
                connect_timeout=float(os.getenv("ARMORED_TELEGRAM_CONNECT_TIMEOUT", "15")),
                read_timeout=float(os.getenv("ARMORED_TELEGRAM_READ_TIMEOUT", "60")),
                write_timeout=float(os.getenv("ARMORED_TELEGRAM_WRITE_TIMEOUT", "180")),
                pool_timeout=float(os.getenv("ARMORED_TELEGRAM_POOL_TIMEOUT", "15")),
            )
            bot = Bot(token=token, request=request)
            try:
                with output.open("rb") as handle:
                    return await bot.send_video(
                        chat_id=int(chat_id),
                        message_thread_id=int(topic_id),
                        video=handle,
                        caption=item.affiliate_url or "",
                        supports_streaming=True,
                    )
            finally:
                await bot.shutdown()

        try:
            message = __import__("asyncio").run(send())
        except (TimedOut, NetworkError) as exc:
            # publication_started() has already created the unresolved row.
            raise RuntimeError("Telegram publication outcome is UNKNOWN") from exc

        message_id = getattr(message, "message_id", None)
        if message_id is None:
            raise RuntimeError("Telegram não retornou message_id")

        self.db.publication_confirmed(item.item_id, str(message_id))

        if os.getenv("ARMORED_TEST_CRASH_AFTER_TELEGRAM_SEND", "0") == "1":
            raise RuntimeError("TEST_CRASH_AFTER_TELEGRAM_SEND")

        return PublicationResult(True, str(message_id))


def build(root: Path, db: Database | None = None, **_kwargs):
    return ArmoredHub(root, db)
