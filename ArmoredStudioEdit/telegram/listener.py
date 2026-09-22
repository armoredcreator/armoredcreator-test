"""Listener Telegram isolado do ArmoredStudioEdit."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from telethon import TelegramClient


def _env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"Variável de ambiente obrigatória ausente: {name}")
    return value


def resolve_session() -> str:
    explicit = os.getenv("TELEGRAM_SESSION", "").strip()
    if explicit:
        return explicit
    candidates = [
        Path("credentials/telegram/session/armoredcreator.session"),
        Path("credentials/telegram/session/armoredsync.session"),
        Path("armoredcreator.session"),
        Path("armoredsync.session"),
    ]
    for path in candidates:
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

    async def download_video(self, message: Any, destination: Path) -> Path:
        destination.parent.mkdir(parents=True, exist_ok=True)
        result = await self.client.download_media(message, file=str(destination))
        if not result:
            raise RuntimeError("Telegram não retornou arquivo de mídia.")
        return Path(result)

    async def iter_videos(self, limit: int | None = None):
        async for message in self.client.iter_messages(
            self.chat_id,
            limit=limit,
            reply_to=self.topic_id,
        ):
            if getattr(message, "video", None):
                yield message
