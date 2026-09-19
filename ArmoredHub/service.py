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

    Dry-run is the default for the isolated lab. Real Telegram is opt-in and
    deliberately kept outside unit tests.
    """

    def __init__(self, root: Path):
        self.state_file = Path(root) / "hub" / "publications.json"
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

    def check_publication(self, item: Item) -> PublicationCheck:
        record = self._load().get(str(item.id))
        if not record:
            return PublicationCheck.ABSENT
        status = record.get("status")
        if status == "published":
            return PublicationCheck.CONFIRMED
        return PublicationCheck.UNKNOWN

    def publish(self, item: Item) -> PublicationResult:
        state = self._load()
        key = str(item.id)
        existing = state.get(key)
        if existing and existing.get("status") == "published":
            return PublicationResult(True, existing.get("message_id"))

        if os.getenv("ARMORED_HUB_DRY_RUN", "1") != "1":
            raise RuntimeError(
                "Hub Telegram real ainda exige um publisher dedicado; "
                "o laboratório não envia mensagens por acidente."
            )

        output = Path(item.result_path or "")
        if not output.exists() or output.stat().st_size <= 0:
            raise RuntimeError("Hub recebeu resultado inexistente/vazio")

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


def build(root: Path, **_kwargs):
    return ArmoredHub(root)
