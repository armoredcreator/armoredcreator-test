from __future__ import annotations

import importlib
import os
from pathlib import Path
from typing import Any

from ..production_contracts import SourceMessage


def _module(name: str):
    root = os.getenv("ARMORED_LEGACY_ROOT")
    if root:
        root_path = str(Path(root).resolve())
        import sys
        if root_path not in sys.path:
            sys.path.insert(0, root_path)
    return importlib.import_module(name)


class LegacySourceAdapter:
    """Bridge para o produtor real do ArmoredSync.

    A fábrica deve devolver um objeto com fetch_next(). O método pode ser
    síncrono ou assíncrono; a ponte normaliza ambos para SourceMessage.
    """

    def __init__(self, source: Any):
        self.source = source

    async def fetch_next(self) -> SourceMessage | None:
        value = self.source.fetch_next()
        if hasattr(value, "__await__"):
            value = await value
        if value is None:
            return None
        if isinstance(value, SourceMessage):
            return value
        source_path = getattr(value, "source_path", None)
        message_id = getattr(value, "telegram_message_id", None)
        if isinstance(value, dict):
            source_path = value.get("source_path", source_path)
            message_id = value.get("telegram_message_id", value.get("message_id", message_id))
        if source_path is None or message_id is None:
            raise RuntimeError("Sync source must expose source_path and telegram_message_id")
        return SourceMessage(Path(str(source_path)), str(message_id))


class LegacyVisionAdapter:
    def __init__(self, vision: Any):
        self.vision = vision

    def identify(self, item):
        result = self.vision.process_record(_item_to_legacy_record(item))
        status = result.get("status")
        if status == "ready_for_hub":
            return result
        if status == "already_processed":
            raise RuntimeError(f"Vision item already processed: {item.id}")
        if status == "duplicate":
            raise RuntimeError(f"Vision duplicate: {result.get('duplicate_of')}")
        raise RuntimeError(result.get("error") or f"Vision returned {status}")


class LegacyStudioAdapter:
    def __init__(self, controller: Any, temp_root: Path):
        self.controller = controller
        self.temp_root = temp_root

    def process(self, item):
        source = Path(item.working_path or item.original_path)
        if not source.exists():
            raise FileNotFoundError(source)
        job = self.controller.create_job(source)
        processed = self.controller.process_job(job)
        output = Path(processed.output_file)
        if not output.exists() or output.stat().st_size <= 0:
            raise RuntimeError("Studio produced no valid output")
        affiliate = item.affiliate_name or "affiliate"
        return type("StudioResult", (), {
            "working_path": str(source),
            "result_path": str(output),
            "affiliate_name": affiliate,
        })()


class LegacyHubAdapter:
    def __init__(self, publisher: Any):
        self.publisher = publisher

    def check_publication(self, item):
        from ..models import PublicationCheck
        state = self.publisher.check_publication(item)
        if state is True or state == "confirmed":
            return PublicationCheck.CONFIRMED
        if state is False or state == "absent":
            return PublicationCheck.ABSENT
        return PublicationCheck.UNKNOWN

    def publish(self, item):
        result = self.publisher.publish(item)
        if hasattr(result, "__await__"):
            raise RuntimeError("Async legacy publisher must be wrapped by an async adapter")
        if not result or not getattr(result, "confirmed", False):
            raise RuntimeError("Legacy publisher did not confirm publication")
        return result


def _item_to_legacy_record(item) -> dict[str, Any]:
    return {
        "source_id": item.source_id,
        "message_id": item.telegram_message_id,
        "topic_id": item.metadata.get("topic_id") if isinstance(item.metadata, dict) else None,
        "topic_name": item.metadata.get("topic_name") if isinstance(item.metadata, dict) else None,
        "video_file": item.original_path,
        "shopee_link": item.original_url,
    }
