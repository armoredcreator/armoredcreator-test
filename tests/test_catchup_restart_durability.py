import tempfile
import unittest
from pathlib import Path

from armored_core.coordinator import Coordinator
from armored_core.database import Database
from armored_core.models import PublicationCheck
from armored_core.services import PublicationResult, StudioResult, VisionResult
from armored_core.storage import Storage


class _Vision:
    def identify(self, item):
        return VisionResult("affiliate", "https://example.invalid/affiliate")


class _Studio:
    def __init__(self, storage):
        self.storage = storage

    def process(self, item):
        result = self.storage.result(
            item.content_id,
            affiliate_url=item.affiliate_url,
            affiliate_name=item.affiliate_name,
        )
        result.write_bytes(item.original_path.read_bytes() + b"-final")
        return StudioResult(None, result)


class _Publisher:
    def __init__(self):
        self.published = []

    def check_publication(self, item):
        return PublicationCheck.ABSENT

    def publish(self, item):
        self.published.append(item.content_id)
        return PublicationResult(True, "pub-" + item.content_id)


class _CrashAfterFirst:
    def __init__(self):
        self.calls = 0

    def check_publication(self, item):
        return PublicationCheck.ABSENT

    def publish(self, item):
        self.calls += 1
        if self.calls == 1:
            raise RuntimeError("simulated-process-crash")
        return PublicationResult(True, "pub-" + item.content_id)


class _BatchSource:
    def __init__(self, db, files):
        self.db = db
        self.files = files
        self.connects = 0
        self.disconnects = 0
        self.materializations = 0
        self.checkpoints = None

    async def collect_historical_batch_async(self):
        self.connects += 1
        messages = []
        for message_id, path in self.files.items():
            async def materialize(target, path=path):
                self.materializations += 1
                target.write_bytes(path.read_bytes())
            messages.append(type("Message", (), {
                "telegram_message_id": message_id,
                "source_id": "telegram",
                "topic_id": 10,
                "topic_name": "topic",
                "original_url": "https://shopee.example/" + message_id,
                "materialize": materialize,
            })())
        return messages, {10: max(int(value) for value in self.files)}

    async def disconnect(self):
        self.disconnects += 1

    def mark_ingested(self, message_id):
        pass

    def commit_live_checkpoints(self, checkpoints):
        self.checkpoints = dict(checkpoints)
        for topic_id, message_id in checkpoints.items():
            self.db.set_sync_topic_checkpoint(topic_id, "topic", message_id)


class CatchUpRestartTests(unittest.TestCase):
    def test_restart_after_catchup_processing_failure_reuses_durable_originals(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            storage = Storage(root)
            db = Database(storage.database / "db.sqlite")

            files = {}
            for message_id in ("301", "302"):
                path = root / f"{message_id}.mp4"
                path.write_bytes(message_id.encode())
                files[message_id] = path

            source = _BatchSource(db, files)
            crashing = Coordinator(
                db, storage, _Vision(), _Studio(storage), _CrashAfterFirst(), source
            )

            with self.assertRaises(RuntimeError):
                crashing.run_catch_up()

            # All historical originals were materialized before processing.
            # Checkpoints may already be persisted, but the canonical originals
            # are durable and must not be downloaded again after restart.
            self.assertEqual(source.materializations, 2)
            self.assertEqual(source.checkpoints, {10: 302})
            self.assertTrue(db.get("301").original_path.is_file())
            self.assertTrue(db.get("302").original_path.is_file())
            self.assertEqual(db.get("301").state.value, "FAILED")
            crashing.close()

            restarted_db = Database(storage.database / "db.sqlite")
            restarted_source = _BatchSource(restarted_db, files)
            publisher = _Publisher()
            restarted = Coordinator(
                restarted_db,
                storage,
                _Vision(),
                _Studio(storage),
                publisher,
                restarted_source,
            )

            # FAILED is deliberately manual-recovery state. Recover the item
            # explicitly, then let the same CATCH-UP continue with the remaining
            # durable item. Neither path may redownload an existing original.
            restarted.recover("301")
            processed = restarted.run_catch_up()

            self.assertEqual(processed, ["301", "302"])
            self.assertEqual(restarted_source.materializations, 0)
            self.assertEqual(restarted_source.connects, 1)
            self.assertEqual(
                [restarted_db.get(item).state.value for item in ("301", "302")],
                ["PUBLISHED", "PUBLISHED"],
            )
            self.assertEqual(publisher.published, ["301"])
            restarted.close()


if __name__ == "__main__":
    unittest.main()
