import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from armored_core.coordinator import Coordinator
from armored_core.database import Database
from armored_core.models import PublicationCheck, State
from armored_core.services import PublicationResult, StudioResult, VisionResult
from armored_core.storage import Storage


class IncrementalSource:
    def __init__(self):
        self.messages = ["telegram-1", "telegram-2"]
        self.index = 0
        self.marked = []
        self.historical_complete = False

    async def fetch_next_async(self):
        if self.index >= len(self.messages):
            self.mark_historical_complete()
            return None
        message_id = self.messages[self.index]
        self.index += 1

        async def materialize(target):
            target.write_bytes(message_id.encode())

        return SimpleNamespace(
            telegram_message_id=message_id,
            source_id="telegram",
            topic_id=228,
            topic_name="Telegram 228",
            original_url="https://shopee.com.br/example/final",
            source_path=None,
            materialize=materialize,
        )

    def mark_historical_complete(self):
        self.historical_complete = True

    def mark_ingested(self, message_id):
        self.marked.append(str(message_id))


class Vision:
    def identify(self, item):
        return VisionResult("affiliate", "https://shopee.com.br/affiliate/final")


class Studio:
    def __init__(self, storage):
        self.storage = storage

    def process(self, item):
        working = self.storage.working(item.content_id)
        result = self.storage.result(item.content_id, affiliate_name=item.affiliate_name or "affiliate")
        payload = item.original_path.read_bytes()
        working.write_bytes(payload)
        result.write_bytes(payload + b"-final")
        return StudioResult(working, result)


class Publisher:
    def __init__(self):
        self.published = []

    def check_publication(self, item):
        return PublicationCheck.ABSENT

    def publish(self, item):
        self.published.append(item.item_id)
        return PublicationResult(True, "published-" + item.item_id)


class BatchTelegramSource:
    def __init__(self):
        self.connected = True
        self.marked = []
        self.disconnected = False

    async def collect_historical_batch_async(self):
        async def materialize(target):
            if self.disconnected:
                raise AssertionError("historical materialization occurred after Telegram disconnect")
            target.write_bytes(b"BATCH-TELEGRAM")

        return [
            SimpleNamespace(
                telegram_message_id="telegram-batch-1",
                source_id="telegram",
                topic_id=101,
                topic_name="Batch",
                original_url="https://shopee.com.br/example/batch",
                source_path=None,
                materialize=materialize,
            )
        ], {101: 900}

    def mark_ingested(self, message_id):
        self.marked.append(str(message_id))

    async def disconnect(self):
        self.disconnected = True

    def commit_live_checkpoints(self, checkpoints):
        self.checkpoints = dict(checkpoints)



class IncrementalCoordinatorTests(unittest.TestCase):
    def test_catch_up_processes_each_item_incrementally(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            storage = Storage(root)
            db = Database(storage.database / "db.sqlite")
            source = IncrementalSource()
            publisher = Publisher()
            coordinator = Coordinator(db, storage, Vision(), Studio(storage), publisher, source)

            processed = coordinator.run_catch_up()

            self.assertEqual(processed, ["telegram-1", "telegram-2"])
            self.assertEqual(source.marked, ["telegram-1", "telegram-2"])
            self.assertTrue(db.historical_complete())
            self.assertEqual(db.get("telegram-1").state, State.PUBLISHED)
            self.assertEqual(db.get("telegram-2").state, State.PUBLISHED)
            self.assertEqual(publisher.published, ["telegram-1", "telegram-2"])
            coordinator.close()


    def test_real_source_batch_materializes_before_disconnect_and_then_processes(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            storage = Storage(root)
            db = Database(storage.database / "db.sqlite")
            source = BatchTelegramSource()
            publisher = Publisher()
            coordinator = Coordinator(db, storage, Vision(), Studio(storage), publisher, source)

            try:
                processed = coordinator.run_catch_up()

                self.assertEqual(processed, ["telegram-batch-1"])
                self.assertEqual(source.marked, ["telegram-batch-1"])
                self.assertTrue(source.disconnected)
                self.assertEqual(source.checkpoints, {101: 900})
                self.assertTrue(db.historical_complete())
                item = db.get("telegram-batch-1")
                self.assertEqual(item.state, State.PUBLISHED)
                self.assertTrue(item.original_path.exists())
                self.assertEqual(publisher.published, ["telegram-batch-1"])
            finally:
                coordinator.close()


if __name__ == "__main__":
    unittest.main()
