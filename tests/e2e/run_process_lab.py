from __future__ import annotations

import argparse
import asyncio
from pathlib import Path
from types import SimpleNamespace

from armored_core.coordinator import Coordinator
from armored_core.database import Database
from armored_core.models import PublicationCheck, State
from armored_core.services import PublicationResult, StudioResult, VisionResult
from armored_core.storage import Storage


class Vision:
    def identify(self, item):
        return VisionResult("affiliate", "https://shopee.example/lab")


class Studio:
    def __init__(self, storage):
        self.storage = storage

    def process(self, item):
        working = self.storage.working(item.content_id)
        result = self.storage.result(
            item.content_id,
            affiliate_url=item.affiliate_url,
            affiliate_name=item.affiliate_name,
        )
        working.write_bytes(item.original_path.read_bytes())
        result.write_bytes(item.original_path.read_bytes() + b"-final")
        return StudioResult(working, result)


class Publisher:
    def __init__(self, storage):
        self.storage = storage
        self.log = storage.root / "lab-publications.txt"

    def check_publication(self, item):
        return PublicationCheck.CONFIRMED if self._published(item.content_id) else PublicationCheck.ABSENT

    def publish(self, item):
        with self.log.open("a", encoding="utf-8") as handle:
            handle.write(f"{item.content_id}\n")
        return PublicationResult(True, f"lab-telegram-{item.content_id}")

    def _published(self, item_id):
        return self.log.exists() and str(item_id) in self.log.read_text(encoding="utf-8").splitlines()


class Source:
    def __init__(self, root):
        self.root = root
        self.db = Database(Storage(root).database / "armoredcreator.db")
        self.sent = False
        self.connected = False

    async def collect_historical_batch_async(self):
        return [], {228: 99}

    async def fetch_live_batch_async(self):
        self.connected = True
        if self.sent:
            return [], {228: 100}
        self.sent = True

        async def materialize(target):
            target.write_bytes(b"LAB-LIVE")

        return [SimpleNamespace(
            telegram_message_id="lab-live-1",
            source_id="telegram-lab",
            topic_id=228,
            topic_name="lab",
            original_url="https://shopee.example/source",
            materialize=materialize,
        )], {228: 100}

    def mark_ingested(self, message_id):
        pass

    def commit_live_checkpoints(self, checkpoints):
        for topic_id, message_id in checkpoints.items():
            self.db.set_sync_topic_checkpoint(topic_id, "lab", message_id)

    async def disconnect(self):
        self.connected = False

    def close(self):
        self.db.close()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True)
    parser.add_argument("--cycles", type=int, default=2)
    args = parser.parse_args()

    root = Path(args.root).resolve()
    storage = Storage(root)
    storage.database.mkdir(parents=True, exist_ok=True)
    db = Database(storage.database / "armoredcreator.db")
    source = Source(root)
    publisher = Publisher(storage)
    coordinator = Coordinator(
        db,
        storage,
        Vision(),
        Studio(storage),
        publisher,
        source,
    )
    try:
        if not db.historical_complete():
            db.complete_historical_sync()
            db.set_sync_topic_checkpoint(228, "lab", 99)
        coordinator.run_forever(max_cycles=args.cycles, poll_seconds=0)
        item = db.get("lab-live-1")
        print(f"STATE={item.state}")
        print(f"CHECKPOINT={db.sync_topic_checkpoint(228)}")
        print(f"PUBLICATIONS={publisher.log.read_text(encoding='utf-8').splitlines() if publisher.log.exists() else []}")
        return 0 if item.state == State.PUBLISHED else 1
    finally:
        coordinator.close()
        source.close()


if __name__ == "__main__":
    raise SystemExit(main())
