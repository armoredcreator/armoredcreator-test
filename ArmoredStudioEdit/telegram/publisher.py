"""Publicação Telegram no mesmo tópico do ArmoredStudioEdit."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from telethon import TelegramClient

from .listener import _env, resolve_session


class TelegramPublisher:
    def __init__(self, chat_id: int, topic_id: int):
        self.chat_id = int(chat_id)
        self.topic_id = int(topic_id)
        self.client = TelegramClient(
            resolve_session(),
            int(_env("TELEGRAM_API_ID")),
            _env("TELEGRAM_API_HASH"),
        )

    async def connect(self) -> None:
        await self.client.start()

    async def disconnect(self) -> None:
        await self.client.disconnect()

    async def publish(self, video: str | Path, caption: str | None = None) -> Any:
        return await self.client.send_file(
            self.chat_id,
            str(video),
            caption=caption,
            reply_to=self.topic_id,
        )
