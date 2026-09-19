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
        return self.db.publication(item.item_id) if self.db is not None else None

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
                self.db.publication_confirmed(item.item_id, str(message_id))
                return PublicationCheck.CONFIRMED
        return PublicationCheck.UNKNOWN

    def _verify_telegram_message(self, message_id: str) -> bool:
        api_id = os.getenv("TELEGRAM_API_ID")
        api_hash = os.getenv("TELEGRAM_API_HASH")
        chat_id = os.getenv("ARMORED_CREATOR_GROUP_ID")
        if not message_id or not api_id or not api_hash or not chat_id:
            return False

        async def verify():
            try:
                from telethon import TelegramClient
            except ImportError:
                return False
            session = self.root / "credentials" / "telegram" / "session" / "armoredhub-verify"
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
        chat_id = os.getenv("ARMORED_CREATOR_GROUP_ID")
        topic_id = os.getenv("ARMORED_HUB_TOPIC_ID", "228")
        if not token or not chat_id or not topic_id:
            raise RuntimeError(
                "Telegram Hub exige ARMORED_CREATOR_BOT_TOKEN, "
                "ARMORED_CREATOR_GROUP_ID e ARMORED_HUB_TOPIC_ID"
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

        self.db.publication_confirmed(item.item_id, str(message_id))

        if os.getenv("ARMORED_TEST_CRASH_AFTER_TELEGRAM_SEND", "0") == "1":
            raise RuntimeError("TEST_CRASH_AFTER_TELEGRAM_SEND")

        return PublicationResult(True, str(message_id))


def build(root: Path, db: Database | None = None, **_kwargs):
    return ArmoredHub(root, db)
