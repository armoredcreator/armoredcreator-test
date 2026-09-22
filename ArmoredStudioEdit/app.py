"""CLI e ponto de entrada do ArmoredStudioEdit."""
from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path

from .pipeline.orchestrator import ArmoredStudioEditPipeline
from .telegram.listener import TelegramListener
from .telegram.publisher import TelegramPublisher


CONFIG_PATH = Path(__file__).parent / "config" / "config.json"


def load_config() -> dict:
    return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))


def telegram_config(config: dict) -> tuple[int, int]:
    tg = config["telegram"]
    return (
        int(os.environ[tg["chat_id_env"]]),
        int(os.environ[tg["topic_id_env"]]),
    )


async def run_telegram_once(config: dict) -> None:
    chat_id, topic_id = telegram_config(config)
    root = Path(config["storage"]["root"])
    incoming = root / "incoming"
    outgoing = root / "outgoing"
    incoming.mkdir(parents=True, exist_ok=True)
    outgoing.mkdir(parents=True, exist_ok=True)

    listener = TelegramListener(chat_id, topic_id)
    publisher = TelegramPublisher(chat_id, topic_id)
    pipeline = ArmoredStudioEditPipeline(config)

    await listener.connect()
    await publisher.connect()
    try:
        async for message in listener.iter_videos(limit=20):
            source = incoming / f"{message.id}.mp4"
            await listener.download_video(message, source)
            output = outgoing / f"{message.id}_edited.mp4"
            pipeline.run(source, output)
            await publisher.publish(output)
    finally:
        await listener.disconnect()
        await publisher.disconnect()


def main() -> int:
    parser = argparse.ArgumentParser(description="ArmoredStudioEdit")
    parser.add_argument("video", nargs="?", help="Vídeo local para processar")
    parser.add_argument("--output", help="Arquivo final")
    parser.add_argument(
        "--telegram-once",
        action="store_true",
        help="Processa vídeos existentes no tópico e publica no mesmo tópico",
    )
    args = parser.parse_args()

    config = load_config()
    pipeline = ArmoredStudioEditPipeline(config)
    print("ArmoredStudioEdit")
    print("Operações:", ", ".join(pipeline.selected_operations()) or "nenhuma")

    if args.telegram_once:
        asyncio.run(run_telegram_once(config))
        return 0

    if not args.video:
        return 0

    report = pipeline.run(args.video, args.output)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
