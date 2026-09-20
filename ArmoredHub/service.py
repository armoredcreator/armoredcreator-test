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

        # Any existing publication attempt is an external side-effect window.
        # It must be reconciled against Telegram before another send is allowed.
        message_id = record["published_message_id"]
        if message_id:
            status = self._verify_telegram_message(str(message_id), item)
            if status is True:
                self.db.publication_confirmed(item.content_id, str(message_id))
                return PublicationCheck.CONFIRMED
            if status is False:
                return PublicationCheck.ABSENT
            return PublicationCheck.UNKNOWN

        matches = self._find_telegram_publications(item)
        if matches is None:
            return PublicationCheck.UNKNOWN
        if len(matches) == 1:
            self.db.publication_confirmed(item.content_id, matches[0])
            return PublicationCheck.CONFIRMED
        if len(matches) == 0:
            return PublicationCheck.ABSENT
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

    @staticmethod
    def _topic_id(message) -> int | None:
        reply_to = getattr(message, "reply_to", None)
        if reply_to is None:
            return None
        value = getattr(reply_to, "reply_to_top_id", None)
        if value is None:
            value = getattr(reply_to, "reply_to_msg_id", None)
        try:
            return int(value) if value is not None else None
        except (TypeError, ValueError):
            return None

    def _telegram_publication_matches(self, message, item: Item, topic_id: int) -> bool:
        """Require exact caption, exact topic and actual video media."""
        if self._topic_id(message) != int(topic_id):
            return False

        caption = str(getattr(message, "message", "") or "").strip()
        expected = str(item.affiliate_url or "").strip()
        if caption != expected:
            return False

        if not getattr(message, "video", None) and not getattr(message, "document", None):
            return False

        # If Telegram exposes a filename, validate it when it is available.
        expected_name = Path(item.result_path or "").name
        if expected_name:
            document = getattr(message, "document", None)
            if document is not None:
                filename = None
                for attribute in getattr(document, "attributes", []) or []:
                    filename = getattr(attribute, "file_name", None)
                    if filename:
                        break
                if filename and filename != expected_name:
                    return False
        return True

    def _find_telegram_publications(self, item: Item) -> list[str] | None:
        """Search MTProto by affiliate-link tail and require exact topic/caption/media."""
        api_id = os.getenv("TELEGRAM_API_ID")
        api_hash = os.getenv("TELEGRAM_API_HASH")
        topic_id = (os.getenv("ARMORED_HUB_TOPIC_ID") or "228").strip()
        chat_id = self._resolve_destination_chat_id(topic_id)
        if not api_id or not api_hash or not chat_id or not topic_id:
            return None

        affiliate = str(item.affiliate_url or "").strip()
        query = affiliate.rstrip("/").rsplit("/", 1)[-1].strip() if affiliate else ""
        if not query:
            return None

        async def find():
            try:
                from telethon import TelegramClient, functions
                from telethon.tl.types import InputMessagesFilterEmpty
            except ImportError:
                return None

            session = self._telegram_session_path()
            session.parent.mkdir(parents=True, exist_ok=True)
            client = TelegramClient(str(session), int(api_id), api_hash, request_retries=0, connection_retries=0)
            try:
                await client.start()
                entity = await client.get_entity(int(chat_id))
                result = await client(functions.messages.SearchRequest(
                    peer=entity,
                    q=query,
                    from_id=None,
                    top_msg_id=int(topic_id),
                    filter=InputMessagesFilterEmpty(),
                    min_date=None,
                    max_date=None,
                    offset_id=0,
                    add_offset=0,
                    limit=100,
                    max_id=0,
                    min_id=0,
                    hash=0,
                ))
                matches = []
                for message in getattr(result, "messages", []) or []:
                    if self._telegram_publication_matches(message, item, int(topic_id)):
                        matches.append(str(getattr(message, "id", "")))
                return sorted({value for value in matches if value})
            finally:
                if client.is_connected():
                    await client.disconnect()

        try:
            return __import__("asyncio").run(find())
        except Exception:
            return None

    def _verify_telegram_message(self, message_id: str, item: Item) -> bool | None:
        """Verify the exact destination message; None means UNKNOWN."""
        api_id = os.getenv("TELEGRAM_API_ID")
        api_hash = os.getenv("TELEGRAM_API_HASH")
        topic_id = (os.getenv("ARMORED_HUB_TOPIC_ID") or "228").strip()
        chat_id = self._resolve_destination_chat_id(topic_id)
        if not message_id or not api_id or not api_hash or not chat_id:
            return None

        async def verify():
            try:
                from telethon import TelegramClient
            except ImportError:
                return None
            session = self._telegram_session_path()
            session.parent.mkdir(parents=True, exist_ok=True)
            client = TelegramClient(str(session), int(api_id), api_hash, request_retries=0, connection_retries=0)
            try:
                await client.start()
                message = await client.get_messages(int(chat_id), ids=int(message_id))
                if message is None:
                    return False
                return self._telegram_publication_matches(message, item, int(topic_id))
            finally:
                if client.is_connected():
                    await client.disconnect()

        try:
            return __import__("asyncio").run(verify())
        except Exception:
            return None

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

        if os.getenv("ARMORED_HUB_DRY_RUN", "0") == "1":
            raise RuntimeError(
                "ARMORED_HUB_DRY_RUN=1: publicação real bloqueada; "
                "nenhum item pode ser marcado como PUBLISHED"
            )

        return self._publish_telegram(item, output)

    def _publish_telegram(self, item: Item, output: Path) -> PublicationResult:
        token = os.getenv("ARMORED_CREATOR_BOT_TOKEN")
        topic_id = (os.getenv("ARMORED_HUB_TOPIC_ID") or "228").strip()
        if not token or not topic_id:
            raise RuntimeError(
                "Telegram Hub exige ARMORED_CREATOR_BOT_TOKEN e ARMORED_HUB_TOPIC_ID"
            )
        chat_id = self._resolve_destination_chat_id(topic_id)
        self.db.publication_started(
            item.item_id,
            destination_chat_id=chat_id,
            destination_topic_id=int(topic_id),
        )

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

        # This is the critical crash window: persist the real Telegram ID
        # before any post-send code can fail. The row remains unconfirmed.
        self.db.publication_message_sent(item.item_id, str(message_id))

        if os.getenv("ARMORED_TEST_CRASH_AFTER_TELEGRAM_SEND", "0") == "1":
            raise RuntimeError("TEST_CRASH_AFTER_TELEGRAM_SEND")

        # Telegram accepted the video, but SQLite must not call it PUBLISHED
        # until an independent MTProto read-back confirms exact chat/topic/
        # caption/media identity.
        status = self.check_publication(item)
        if status != PublicationCheck.CONFIRMED:
            raise RuntimeError(f"publication-verification-{status.value.lower()}")

        return PublicationResult(True, str(message_id))


def build(root: Path, db: Database | None = None, **_kwargs):
    return ArmoredHub(root, db)
