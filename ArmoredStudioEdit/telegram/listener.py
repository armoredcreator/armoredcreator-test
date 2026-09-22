"""Listener Telegram contínuo e efêmero do ArmoredStudioEdit."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, AsyncIterator

from telethon import TelegramClient, events


def _env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"Variável de ambiente obrigatória ausente: {name}")
    return value


def resolve_session() -> str:
    explicit = os.getenv("TELEGRAM_SESSION", "").strip()
    if explicit:
        return explicit
    for path in (
        Path("credentials/telegram/session/armoredcreator.session"),
        Path("credentials/telegram/session/armoredsync.session"),
        Path("armoredcreator.session"),
        Path("armoredsync.session"),
    ):
        if path.exists():
            return str(path)
    raise RuntimeError(
        "Sessão Telegram não encontrada. Defina TELEGRAM_SESSION "
        "ou disponibilize um .session no ambiente do projeto."
    )


class TelegramListener:
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

    @staticmethod
    def is_video(message: Any) -> bool:
        return bool(getattr(message, "video", None))

    def is_topic_message(self, message: Any) -> bool:
        if int(getattr(message, "id", 0) or 0) == self.topic_id:
            return True
        reply = getattr(message, "reply_to", None)
        if reply is None:
            return False
        return (
            int(getattr(reply, "reply_to_top_id", 0) or 0) == self.topic_id
            or int(getattr(reply, "reply_to_msg_id", 0) or 0) == self.topic_id
        )

    async def download_video(self, message: Any, destination: Path) -> Path:
        destination.parent.mkdir(parents=True, exist_ok=True)
        result = await self.client.download_media(message, file=str(destination))
        if not result:
            raise RuntimeError("Telegram não retornou arquivo de mídia.")
        return Path(result)

    def register_video_handler(self, callback) -> None:
        @self.client.on(events.NewMessage(chats=self.chat_id))
        async def _handler(event):
            message = event.message
            if self.is_topic_message(message) and self.is_video(message):
                await callback(message)

    async def run_forever(self) -> None:
        await self.client.run_until_disconnected()
