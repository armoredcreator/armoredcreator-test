from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path

from armored_core.models import Item, PublicationCheck
from armored_core.services import PublicationResult


class ArmoredHub:
    """Idempotent publication boundary.

    Dry-run remains the default. Setting ARMORED_HUB_DRY_RUN=0 enables the
    real Telegram Bot API publisher. Any unresolved external outcome is
    represented as UNKNOWN so the core refuses a second send.
    """

    def __init__(self, root: Path):
        self.root = Path(root)
        self.state_file = self.root / "hub" / "publications.json"
        self.state_file.parent.mkdir(parents=True, exist_ok=True)

    def _load(self):
        if not self.state_file.exists():
            return {}
        try:
            return json.loads(self.state_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def _save(self, data):
        tmp = self.state_file.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, self.state_file)

    def _key(self, item: Item) -> str:
        return str(item.id)

    def check_publication(self, item: Item) -> PublicationCheck:
        record = self._load().get(self._key(item))
        if not record:
            return PublicationCheck.ABSENT
        status = record.get("status")
        if status == "published":
            return PublicationCheck.CONFIRMED
        if status == "unknown" and os.getenv("ARMORED_HUB_VERIFY_TELEGRAM", "0") == "1":
            if self._verify_telegram_message(record):
                record["status"] = "published"
                data = self._load()
                data[self._key(item)] = record
                self._save(data)
                return PublicationCheck.CONFIRMED
        return PublicationCheck.UNKNOWN

    def _verify_telegram_message(self, record: dict) -> bool:
        message_id = record.get("message_id")
        api_id = os.getenv("TELEGRAM_API_ID")
        api_hash = os.getenv("TELEGRAM_API_HASH")
        chat_id = os.getenv("ARMORED_CREATOR_GROUP_ID", "-1004341972306")
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
        state = self._load()
        key = self._key(item)
        existing = state.get(key)
        if existing and existing.get("status") == "published":
            return PublicationResult(True, existing.get("message_id"))

        output = Path(item.result_path or "")
        if not output.exists() or output.stat().st_size <= 0:
            raise RuntimeError("Hub recebeu resultado inexistente/vazio")

        if os.getenv("ARMORED_HUB_DRY_RUN", "1") == "1":
            digest = hashlib.sha256(output.read_bytes()).hexdigest()
            message_id = f"dry-{item.id}"
            state[key] = {
                "status": "published",
                "message_id": message_id,
                "sha256": digest,
                "published_at": datetime.now(timezone.utc).isoformat(),
            }
            self._save(state)
            return PublicationResult(True, message_id)

        return self._publish_telegram(item, state, key, output)

    def _publish_telegram(self, item: Item, state: dict, key: str, output: Path) -> PublicationResult:
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

        state[key] = {
            "status": "publishing",
            "output_sha256": hashlib.sha256(output.read_bytes()).hexdigest(),
            "started_at": datetime.now(timezone.utc).isoformat(),
        }
        self._save(state)

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
            state[key] = {
                "status": "unknown",
                "error": f"{type(exc).__name__}: {exc}",
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
            self._save(state)
            raise RuntimeError("Telegram publication outcome is UNKNOWN") from exc
        except Exception:
            state.pop(key, None)
            self._save(state)
            raise

        message_id = getattr(message, "message_id", None)
        if message_id is None:
            state[key] = {"status": "unknown", "updated_at": datetime.now(timezone.utc).isoformat()}
            self._save(state)
            raise RuntimeError("Telegram não retornou message_id")

        if os.getenv("ARMORED_TEST_CRASH_AFTER_TELEGRAM_SEND", "0") == "1":
            state[key] = {
                "status": "unknown",
                "message_id": str(message_id),
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
            self._save(state)
            raise RuntimeError("TEST_CRASH_AFTER_TELEGRAM_SEND")

        state[key] = {
            "status": "published",
            "message_id": str(message_id),
            "published_at": datetime.now(timezone.utc).isoformat(),
        }
        self._save(state)
        return PublicationResult(True, str(message_id))


def build(root: Path, **_kwargs):
    return ArmoredHub(root)
