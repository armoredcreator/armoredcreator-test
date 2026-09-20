import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from armored_core.coordinator import Coordinator
from armored_core.database import Database
from armored_core.models import PublicationCheck, State
from armored_core.services import PublicationResult, StudioResult, VisionResult
from armored_core.storage import Storage


class LiveSource:
    def __init__(self, messages, db=None):
        self.messages = list(messages)
        self.db = db
        self.calls = 0
        self.disconnected = 0
        self.committed = []

    async def fetch_live_batch_async(self):
        self.calls += 1
        if not self.messages:
            return [], {101: 100 + self.calls}
        messages = self.messages
        self.messages = []
        result = []
        for message_id in messages:
            async def materialize(target, payload=message_id.encode()):
                target.write_bytes(payload)
            result.append(SimpleNamespace(
                telegram_message_id=message_id,
                source_id="telegram",
                topic_id=101,
                topic_name="LIVE",
                original_url="https://shopee.com.br/example/live",
                source_path=None,
                materialize=materialize,
            ))
        return result, {101: 100 + self.calls}

    async def disconnect(self):
        self.disconnected += 1

    def commit_live_checkpoints(self, checkpoints):
        self.committed.append(dict(checkpoints))
        if self.db is not None:
            for topic_id, message_id in checkpoints.items():
                self.db.set_sync_topic_checkpoint(topic_id, "LIVE", message_id)

    def mark_ingested(self, message_id):
        pass


class Vision:
    def identify(self, item):
        return VisionResult("affiliate", "https://shopee.com.br/affiliate/live")


class Studio:
    def __init__(self, storage):
        self.storage = storage

    def process(self, item):
        working = self.storage.working(item.content_id)
        result = self.storage.result(item.content_id, affiliate_name=item.affiliate_name)
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
        return PublicationResult(True, f"published-{item.item_id}")


class CoordinatorContinuousTests(unittest.TestCase):
    def _coordinator(self, root, db, source, publisher=None):
        return Coordinator(
            db,
            Storage(root),
            Vision(),
            Studio(Storage(root)),
            publisher or Publisher(),
            source,
        )

    def test_run_forever_processes_live_cycles_without_second_pipeline(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            storage = Storage(root)
            db = Database(storage.database / "db.sqlite")
            db.complete_historical_sync()
            db.set_sync_topic_checkpoint(101, "LIVE", 99)
            source = LiveSource(["live-1"], db)
            publisher = Publisher()
            coordinator = self._coordinator(root, db, source, publisher)

            try:
                coordinator.run_forever(poll_seconds=0, max_cycles=2)
                self.assertEqual(source.calls, 2)
                self.assertEqual(publisher.published, ["live-1"])
                self.assertEqual(db.get("live-1").state, State.PUBLISHED)
                self.assertEqual(db.sync_topic_checkpoint(101), 102)
                self.assertIsNone(
                    db.conn.execute(
                        "SELECT 1 FROM runtime_locks WHERE name='coordinator'"
                    ).fetchone()
                )
            finally:
                coordinator.close()

    def test_restart_while_live_deduplicates_persisted_item(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            storage = Storage(root)
            db = Database(storage.database / "db.sqlite")
            db.complete_historical_sync()
            db.set_sync_topic_checkpoint(101, "LIVE", 99)

            first_source = LiveSource(["live-restart"], db)
            first_publisher = Publisher()
            first = self._coordinator(root, db, first_source, first_publisher)
            first.run_forever(poll_seconds=0, max_cycles=1)
            first.close()

            restarted_db = Database(storage.database / "db.sqlite")
            second_source = LiveSource(["live-restart"], restarted_db)
            second_publisher = Publisher()
            second = self._coordinator(root, restarted_db, second_source, second_publisher)
            try:
                second.run_forever(poll_seconds=0, max_cycles=1)
                self.assertEqual(restarted_db.get("live-restart").state, State.PUBLISHED)
                self.assertEqual(second_publisher.published, [])
            finally:
                second.close()

    def test_second_coordinator_is_blocked_while_first_holds_runtime_lock(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            storage = Storage(root)
            db1 = Database(storage.database / "db.sqlite")
            db1.complete_historical_sync()
            db1.set_sync_topic_checkpoint(101, "LIVE", 99)
            first = self._coordinator(root, db1, LiveSource([]))

            db2 = Database(storage.database / "db.sqlite")
            second = self._coordinator(root, db2, LiveSource([]))
            try:
                db1.acquire_runtime_lock("coordinator")
                with self.assertRaisesRegex(RuntimeError, "runtime-lock-active"):
                    db2.acquire_runtime_lock("coordinator")
                db1.release_runtime_lock("coordinator")
            finally:
                first.close()
                second.close()


if __name__ == "__main__":
    unittest.main()
