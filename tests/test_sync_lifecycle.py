import tempfile
import unittest
from pathlib import Path

from armored_core.database import Database
from armored_core.models import PublicationCheck
from armored_core.services import PublicationResult, StudioResult, VisionResult
from armored_core.storage import Storage
from armored_core.coordinator import Coordinator
from ArmoredSync.service import SyncMessage, TelegramSource


class FakeMessage:
    def __init__(self, message_id, video=False, text=""):
        self.id = message_id
        self.video = video
        self.message = text
        self.entities = []


class FakeReader:
    async def connect(self):
        pass

    async def disconnect(self):
        pass


class CandidateSource(TelegramSource):
    async def _discover_topics(self, source):
        return [(10, "topic")]

    async def _topic_messages(self, source, topic_id):
        messages = [
            FakeMessage(1, video=True),
            FakeMessage(2, text="https://shopee.com.br/x/abc"),
            FakeMessage(3, video=True, text="https://shopee.com.br/x/def"),
        ]
        for message in messages:
            yield message


class Vision:
    def identify(self, item):
        return VisionResult("affiliate", "https://example.invalid/affiliate")


class Studio:
    def __init__(self, storage):
        self.storage = storage

    def process(self, item):
        result = self.storage.result(item.content_id, affiliate_name=item.affiliate_name)
        result.write_bytes(item.original_path.read_bytes() + b"-processed")
        return StudioResult(None, result)


class Publisher:
    def __init__(self):
        self.published = []
        self.count = 0

    def check_publication(self, item):
        return PublicationCheck.ABSENT

    def publish(self, item):
        self.count += 1
        self.published.append(item.content_id)
        return PublicationResult(True, f"published-{item.content_id}")


class LifecycleSource:
    def __init__(self):
        self.history = [
            SyncMessage("100", source_id="telegram", source_path=None),
            SyncMessage("101", source_id="telegram", source_path=None),
        ]
        self.live = []
        self.index = 0
        self.live_called = False
        self.completed = False

    async def fetch_next_async(self):
        if self.index < len(self.history):
            value = self.history[self.index]
            self.index += 1
            return value
        self.completed = True
        return None

    async def collect_historical_batch_async(self):
        self.completed = True
        return self.history, {10: 101}

    async def fetch_live_batch_async(self):
        self.live_called = True
        return self.live, {}

    def mark_ingested(self, message_id):
        pass

    def is_historical_complete(self):
        return self.completed

    def commit_live_checkpoints(self, checkpoints):
        pass


class SyncLifecycleTests(unittest.TestCase):
    def test_database_starts_in_catch_up_and_persists_live(self):
        with tempfile.TemporaryDirectory() as td:
            db = Database(Path(td) / "db.sqlite")
            self.assertEqual(db.sync_mode(), "CATCH_UP")
            self.assertFalse(db.historical_complete())
            db.complete_historical_sync()
            db.close()

            reopened = Database(Path(td) / "db.sqlite")
            self.assertEqual(reopened.sync_mode(), "LIVE")
            self.assertTrue(reopened.historical_complete())
            reopened.close()

    def test_topic_checkpoint_is_monotonic(self):
        with tempfile.TemporaryDirectory() as td:
            db = Database(Path(td) / "db.sqlite")
            db.set_sync_topic_checkpoint(10, "topic", 100)
            db.set_sync_topic_checkpoint(10, "topic", 90)
            self.assertEqual(db.sync_topic_checkpoint(10), 100)
            db.set_sync_topic_checkpoint(10, "topic", 120)
            self.assertEqual(db.sync_topic_checkpoint(10), 120)
            db.close()

    def test_historical_candidate_uses_immediately_following_non_video(self):
        source = CandidateSource(Path("."), FakeReader())
        candidates = []

        async def collect():
            async for candidate in source._candidate_iterator("source", [(10, "topic")]):
                candidates.append(candidate)

        import asyncio
        asyncio.run(collect())

        self.assertEqual([candidate[0] for candidate in candidates], [1, 3])
        self.assertEqual(candidates[0][4], "https://shopee.com.br/x/abc")
        self.assertEqual(candidates[1][4], "https://shopee.com.br/x/def")

    def test_coordinator_runs_history_then_live_with_same_source(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            storage = Storage(root)
            db = Database(storage.database / "db.sqlite")

            for message_id in ("100", "101"):
                path = root / f"{message_id}.mp4"
                path.write_bytes(message_id.encode())
                self.assertTrue(path.is_file())

            source = LifecycleSource()
            source.history = [
                SyncMessage("100", source_id="local", source_path=root / "100.mp4",
                            original_url="https://shopee.com.br/x/a"),
                SyncMessage("101", source_id="local", source_path=root / "101.mp4",
                            original_url="https://shopee.com.br/x/b"),
            ]
            coordinator = Coordinator(db, storage, Vision(), Studio(storage), Publisher(), source)

            processed = coordinator.run_catch_up()
            self.assertEqual(processed, ["100", "101"])
            self.assertTrue(db.historical_complete())
            self.assertEqual(processed, ["100", "101"])

            live_path = root / "102.mp4"
            live_path.write_bytes(b"102")
            source.live = [SyncMessage("102", source_id="local", source_path=live_path,
                                       original_url="https://shopee.com.br/x/c")]

            live = coordinator.run_live_once()
            self.assertEqual(live, ["102"])
            self.assertTrue(source.live_called)
            self.assertEqual(db.get("102").telegram_message_id, "102")
            coordinator.close()

    def test_history_and_live_same_id_is_idempotent(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            storage = Storage(root)
            db = Database(storage.database / "db.sqlite")
            path = root / "100.mp4"
            path.write_bytes(b"same")

            from armored_core.services import SyncService
            sync = SyncService(db, storage)
            first = sync.ingest(path, "100", source_id="local",
                                original_url="https://shopee.com.br/x/a")
            second = sync.ingest(path, "100", source_id="local",
                                  original_url="https://shopee.com.br/x/a")
            self.assertEqual(first, second)
            self.assertEqual(len(db.conn.execute("SELECT * FROM items").fetchall()), 1)
            db.close()


if __name__ == "__main__":
    unittest.main()
