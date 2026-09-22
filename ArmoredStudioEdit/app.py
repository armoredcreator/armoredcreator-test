"""Entrada contínua do ArmoredStudioEdit."""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any

from .pipeline.orchestrator import ArmoredStudioEditPipeline
from .telegram.listener import TelegramListener
from .telegram.publisher import TelegramPublisher


CONFIG_PATH = Path(__file__).parent / "config" / "config.json"


def load_config() -> dict[str, Any]:
    return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))


def save_config(config: dict[str, Any]) -> None:
    CONFIG_PATH.write_text(
        json.dumps(config, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def telegram_config(config: dict[str, Any]) -> tuple[int, int]:
    tg = config["telegram"]
    return int(os.environ[tg["chat_id_env"]]), int(os.environ[tg["topic_id_env"]])


def topic_command(message: Any) -> str | None:
    text = getattr(message, "raw_text", "") or ""
    text = text.strip()
    return text if text.startswith("/") else None


def apply_command(config: dict[str, Any], command: str) -> str:
    parts = command.split()
    name = parts[0].lower()

    if name == "/rvc" and len(parts) == 2 and parts[1].lower() in {"on", "off"}:
        config["rvc"]["enabled"] = parts[1].lower() == "on"
        save_config(config)
        return f"RVC {'ATIVADO' if config['rvc']['enabled'] else 'DESATIVADO'}."

    if name == "/rvc" and len(parts) == 3 and parts[1].lower() == "voice":
        voice = parts[2].strip()
        if not voice:
            return "Voz inválida."
        config["rvc"]["voice"] = voice
        save_config(config)
        return f"Voz RVC alterada para: {voice}."

    if name == "/rvc" and len(parts) == 1:
        state = "ON" if config["rvc"]["enabled"] else "OFF"
        return f"RVC={state} | voz={config['rvc']['voice']}"

    return None


async def main_async() -> None:
    config = load_config()
    chat_id, topic_id = telegram_config(config)
    listener = TelegramListener(chat_id, topic_id)
    publisher = TelegramPublisher(chat_id, topic_id, client=listener.client)
    pipeline = ArmoredStudioEditPipeline(config)
    queue: asyncio.Queue[Any] = asyncio.Queue()

    async def enqueue(message: Any) -> None:
        await queue.put(message)

    listener.register_video_handler(enqueue)
    await listener.connect()

    async def process_queue() -> None:
        while True:
            message = await queue.get()
            workspace = Path(tempfile.mkdtemp(prefix="armoredstudioedit-"))
            try:
                source = workspace / "input.mp4"
                await listener.download_video(message, source)

                # Configuração é capturada para este trabalho; alterações
                # por comando valem para o próximo vídeo.
                job_config = json.loads(json.dumps(config))
                job_pipeline = ArmoredStudioEditPipeline(job_config)
                report = await asyncio.to_thread(
                    job_pipeline.run, source, workspace
                )
                await publisher.publish(report["output"])
                print(f"[DONE] Telegram message_id={message.id}")
            except Exception as exc:
                print(f"[ERROR] Telegram message_id={message.id}: {exc}")
            finally:
                shutil.rmtree(workspace, ignore_errors=True)
                queue.task_done()

    worker = asyncio.create_task(process_queue())

    @listener.client.on(__import__("telethon").events.NewMessage(chats=chat_id))
    async def _commands(event):
        message = event.message
        if not listener.is_topic_message(message):
            return
        command = topic_command(message)
        if not command or listener.is_video(message):
            return
        result = apply_command(config, command)
        if result:
            await listener.client.send_message(chat_id, result, reply_to=topic_id)

    try:
        print("ArmoredStudioEdit LIVE")
        print(
            f"RVC={'ON' if config['rvc']['enabled'] else 'OFF'} "
            f"| voz={config['rvc']['voice']}"
        )
        await listener.run_forever()
    finally:
        worker.cancel()
        await listener.disconnect()


def main() -> int:
    parser = argparse.ArgumentParser(description="ArmoredStudioEdit")
    parser.add_argument("--telegram", action="store_true", help="Inicia o worker Telegram contínuo")
    args = parser.parse_args()

    if not args.telegram:
        parser.error("Use --telegram para iniciar o ArmoredStudioEdit.")
    asyncio.run(main_async())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
