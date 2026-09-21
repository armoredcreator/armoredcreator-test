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


class _LiveSourceWithTransientMaterializationFailure(_LiveSource):
    def __init__(self, db):
        super().__init__(db)
        self.attempts = 0

    async def fetch_live_batch_async(self):
        self.connected = True
        self.attempts += 1

        async def materialize(target):
            if self.attempts == 1:
                raise TimeoutError("download Telegram excedeu o timeout")
            target.write_bytes(b"LIVE-RETRY")

        from types import SimpleNamespace
        return [SimpleNamespace(
            telegram_message_id="live-fail-then-retry",
            source_id="telegram",
            topic_id=228,
            topic_name="topic",
            original_url="https://shopee.example/source",
            materialize=materialize,
        )], {228: 101 if self.attempts == 1 else 102}





class _LiveSourceSerialized(_LiveSource):
    def __init__(self, db, events):
        super().__init__(db)
        self.events = events
        self.index = 0

    async def fetch_live_batch_async(self, limit=None):
        self.connected = True
        from types import SimpleNamespace
        remaining = ["live-1", "live-2"][self.index:]
        if not remaining:
            return [], {}
        if limit is not None:
            remaining = remaining[:limit]
        messages = []
        for message_id in remaining:
            async def materialize(target, message_id=message_id):
                self.events.append("materialize:" + message_id)
                target.write_bytes(message_id.encode())
            messages.append(SimpleNamespace(
                telegram_message_id=message_id,
                source_id="telegram",
                topic_id=228,
                topic_name="topic",
                original_url="https://shopee.example/source",
                materialize=materialize,
            ))
            self.index += 1
        return messages, {228: 99 + self.index}





class _RecordingCoordinator(Coordinator):
    def __init__(self, *args, events):
        super().__init__(*args)
        self._events = events

    def run(self, item_id: str) -> None:
        self._events.append("pipeline:" + str(item_id))
        super().run(item_id)


class ContinuousCoordinatorTests(unittest.TestCase):
    def test_live_materializes_and_processes_strictly_one_at_a_time(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            storage = Storage(root)
            db = Database(storage.database / "db.sqlite")
            db.complete_historical_sync()
            db.set_sync_topic_checkpoint(228, "topic", 99)
            events = []
            source = _LiveSourceSerialized(db, events)
            publisher = _Publisher()

            coordinator = _RecordingCoordinator(
                db, storage, _Vision(), _Studio(storage), publisher, source, events=events
            )

            try:
                coordinator.run_forever(max_cycles=1, poll_seconds=0)
                # LIVE is strictly serialized: discovery itself is limited
                # to one candidate, then that candidate is materialized and
                # completes the pipeline before the next discovery.
                self.assertEqual(events, [
                    "materialize:live-1",
                    "pipeline:live-1",
                ])
                self.assertEqual(publisher.published, ["live-1"])
                self.assertEqual(db.get("live-1").state, State.PUBLISHED)
                self.assertIsNone(db.get("live-2"))
                self.assertEqual(source.checkpoints_committed, {228: 100})
            finally:
                coordinator.close()


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

            restarted = None
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
                if restarted is not None:
                    restarted.close()
                coordinator.close()


    def test_live_materialization_failure_does_not_kill_coordinator(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            storage = Storage(root)
            db = Database(storage.database / "db.sqlite")
            db.complete_historical_sync()
            db.set_sync_topic_checkpoint(228, "topic", 100)
            source = _LiveSourceWithTransientMaterializationFailure(db)
            publisher = _Publisher()
            coordinator = Coordinator(db, storage, _Vision(), _Studio(storage), publisher, source)

            try:
                coordinator.run_forever(max_cycles=2, poll_seconds=0)

                item = db.get("live-fail-then-retry")
                self.assertEqual(item.state, State.PUBLISHED)
                self.assertEqual(publisher.published, ["live-fail-then-retry"])
                self.assertEqual(source.checkpoints_committed, {228: 102})
            finally:
                coordinator.close()



if __name__ == "__main__":
    unittest.main()
