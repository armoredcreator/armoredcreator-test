import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from armored_core.coordinator import Coordinator
from armored_core.database import Database
from armored_core.models import PublicationCheck, State
from armored_core.services import PublicationResult, StudioResult, VisionResult
from armored_core.storage import Storage


class _Vision:
    def identify(self, item):
        return VisionResult("affiliate", "https://shopee.example/affiliate")


class _Studio:
    def __init__(self, storage):
        self.storage = storage

    def process(self, item):
        working = self.storage.working(item.content_id)
        result = self.storage.result(
            item.content_id,
            affiliate_url=item.affiliate_url,
            affiliate_name=item.affiliate_name,
        )
        payload = item.original_path.read_bytes()
        working.write_bytes(payload)
        result.write_bytes(payload + b"-final")
        return StudioResult(working, result)


class _Publisher:
    def __init__(self):
        self.published = []

    def check_publication(self, item):
        return PublicationCheck.ABSENT

    def publish(self, item):
        self.published.append(item.content_id)
        return PublicationResult(True, "telegram-" + item.content_id)


class _ReconnectableLiveSource:
    def __init__(self, db):
        self.db = db
        self.connected = False
        self.connect_count = 0
        self.disconnect_count = 0
        self.done = False
        self.checkpoints = None
        self.messages = ("live-1", "live-2")

    async def connect(self):
        self.connected = True
        self.connect_count += 1

    async def fetch_live_batch_async(self):
        await self.connect()
        if self.done:
            return [], {}
        self.done = True

        async def materialize(target, payload=b"LIVE"):
            if not self.connected:
                raise RuntimeError("materialization-requires-live-telegram-session")
            target.write_bytes(payload)

        return [
            SimpleNamespace(
                telegram_message_id="live-1",
                source_id="telegram",
                topic_id=228,
                topic_name="topic",
                original_url="https://shopee.example/source-1",
                materialize=lambda target: materialize(target, b"LIVE-1"),
            ),
            SimpleNamespace(
                telegram_message_id="live-2",
                source_id="telegram",
                topic_id=228,
                topic_name="topic",
                original_url="https://shopee.example/source-2",
                materialize=lambda target: materialize(target, b"LIVE-2"),
            ),
        ], {228: 102}

    async def disconnect(self):
        if self.connected:
            self.disconnect_count += 1
        self.connected = False

    def mark_ingested(self, message_id):
        pass

    def commit_live_checkpoints(self, checkpoints):
        self.checkpoints = dict(checkpoints)
        for topic_id, message_id in checkpoints.items():
            self.db.set_sync_topic_checkpoint(topic_id, "topic", message_id)


class LiveReconnectTests(unittest.TestCase):
    def test_live_batch_reconnects_after_each_sequential_item(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            storage = Storage(root)
            db = Database(storage.database / "db.sqlite")
            db.complete_historical_sync()
            db.set_sync_topic_checkpoint(228, "topic", 100)

            source = _ReconnectableLiveSource(db)
            publisher = _Publisher()
            coordinator = Coordinator(
                db, storage, _Vision(), _Studio(storage), publisher, source
            )

            try:
                processed = coordinator.run_live_once()
                self.assertEqual(processed, ["live-1", "live-2"])
                self.assertEqual(publisher.published, ["live-1", "live-2"])
                self.assertEqual(db.get("live-1").state, State.PUBLISHED)
                self.assertEqual(db.get("live-2").state, State.PUBLISHED)
                self.assertEqual(source.checkpoints, {228: 102})
                self.assertGreaterEqual(source.connect_count, 2)
                self.assertGreaterEqual(source.disconnect_count, 2)
            finally:
                coordinator.close()


if __name__ == "__main__":
    unittest.main()
