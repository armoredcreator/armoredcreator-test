from __future__ import annotations

import os
from pathlib import Path

from .models import Item, PublicationCheck
from .services import PublicationResult


class TelegramPublisher:
    """Telethon-backed Telegram Publisher with deterministic idempotency."""

    def __init__(
        self,
        *,
        target: str | int | None = None,
        api_id: int | None = None,
        api_hash: str | None = None,
        session_path: str | Path | None = None,
        bot_token: str | None = None,
    ) -> None:
        self.target = target if target is not None else os.getenv("TELEGRAM_PUBLISH_TARGET")
        self.api_id = api_id if api_id is not None else self._int_env("TELEGRAM_API_ID")
        self.api_hash = api_hash or os.getenv("TELEGRAM_API_HASH")
        self.session_path = Path(session_path or os.getenv("TELEGRAM_SESSION_PATH", "storage/telegram/session"))
        self.bot_token = bot_token or os.getenv("TELEGRAM_BOT_TOKEN")
        missing = []
        if self.target is None:
            missing.append("TELEGRAM_PUBLISH_TARGET")
        if self.api_id is None:
            missing.append("TELEGRAM_API_ID")
        if not self.api_hash:
            missing.append("TELEGRAM_API_HASH")
        if missing:
            raise RuntimeError("missing-telegram-config:" + ",".join(missing))

    @staticmethod
    def _int_env(name: str) -> int | None:
        value = os.getenv(name)
        if not value:
            return None
        try:
            return int(value)
        except ValueError as exc:
            raise RuntimeError(f"invalid-telegram-config:{name}") from exc

    @staticmethod
    def marker(item: Item) -> str:
        return f"ARMOREDCREATOR_ITEM:{item.item_id}"

    def _client(self):
        try:
            from telethon import TelegramClient
        except ImportError as exc:
            raise RuntimeError("telethon-not-installed") from exc
        self.session_path.parent.mkdir(parents=True, exist_ok=True)
        return TelegramClient(str(self.session_path), self.api_id, self.api_hash)

    async def _connected_client(self):
        client = self._client()
        await client.connect()
        if not await client.is_user_authorized():
            if not self.bot_token:
                await client.disconnect()
                raise RuntimeError("telegram-session-not-authorized")
            await client.start(bot_token=self.bot_token)
        return client

    async def _find(self, client, item: Item):
        async for message in client.iter_messages(self.target, search=self.marker(item), limit=20):
            if self.marker(item) in (message.message or ""):
                return message
        return None

    def check_publication(self, item: Item) -> PublicationCheck:
        import asyncio

        async def check():
            client = await self._connected_client()
            try:
                message = await self._find(client, item)
                return PublicationCheck.CONFIRMED if message else PublicationCheck.ABSENT
            finally:
                await client.disconnect()

        try:
            return asyncio.run(check())
        except Exception:
            return PublicationCheck.UNKNOWN

    def publish(self, item: Item) -> PublicationResult:
        import asyncio

        if not item.result_path or not item.result_path.is_file():
            raise FileNotFoundError("publication-result-missing")

        async def send():
            client = await self._connected_client()
            try:
                existing = await self._find(client, item)
                if existing:
                    return PublicationResult(True, str(existing.id))
                message = await client.send_file(
                    self.target,
                    str(item.result_path),
                    caption=self.marker(item),
                    supports_streaming=True,
                )
                return PublicationResult(True, str(message.id))
            finally:
                await client.disconnect()

        try:
            return asyncio.run(send())
        except Exception as exc:
            raise RuntimeError("telegram-publish-failed") from exc
