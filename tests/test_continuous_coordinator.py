import tempfile
import unittest
from pathlib import Path

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
        result = self.storage.result(item.content_id, affiliate_url=item.affiliate_url, affiliate_name=item.affiliate_name)
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


class _LiveSource:
    def __init__(self, db):
        self.db = db
        self.connected = False
        self.messages = ["live-1"]
        self.done = False
        self.checkpoints_committed = None

    async def fetch_live_batch_async(self):
        self.connected = True
        if self.done:
            return [], {}
        self.done = True

        async def materialize(target):
            target.write_bytes(b"LIVE")

        from types import SimpleNamespace
        return [SimpleNamespace(
            telegram_message_id="live-1",
            source_id="telegram",
            topic_id=228,
            topic_name="topic",
            original_url="https://shopee.example/source",
            materialize=materialize,
        )], {228: 100}

    async def disconnect(self):
        self.connected = False

    def mark_ingested(self, message_id):
        pass

    def commit_live_checkpoints(self, checkpoints):
        self.checkpoints_committed = dict(checkpoints)
        for topic_id, message_id in checkpoints.items():
            self.db.set_sync_topic_checkpoint(topic_id, "topic", message_id)


class ContinuousCoordinatorTests(unittest.TestCase):
    def test_run_forever_processes_live_then_restarts_without_duplicate(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            storage = Storage(root)
            db = Database(storage.database / "db.sqlite")
            db.complete_historical_sync()
            db.set_sync_topic_checkpoint(228, "topic", 99)
            source = _LiveSource(db)
            publisher = _Publisher()
            coordinator = Coordinator(db, storage, _Vision(), _Studio(storage), publisher, source)

            try:
                coordinator.run_forever(max_cycles=2, poll_seconds=0)
                self.assertEqual(publisher.published, ["live-1"])
                self.assertEqual(db.get("live-1").state, State.PUBLISHED)
                self.assertEqual(source.checkpoints_committed, {228: 100})

                restarted_db = Database(storage.database / "db.sqlite")
            restarted_source = _LiveSource(restarted_db)
            restarted_source.done = True
            restarted = Coordinator(
                restarted_db, storage, _Vision(), _Studio(storage), publisher, restarted_source
            )
                restarted.run_forever(max_cycles=1, poll_seconds=0)

                self.assertEqual(publisher.published, ["live-1"])
                self.assertEqual(restarted_db.get("live-1").state, State.PUBLISHED)
            finally:
                if "restarted" in locals():
                    restarted.close()
                coordinator.close()


if __name__ == "__main__":
    unittest.main()
