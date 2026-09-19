import tempfile
import threading
import unittest
from pathlib import Path

from armored_core.database import Database
from armored_core.models import PublicationCheck, State
from armored_core.pipeline import Pipeline
from armored_core.recovery import Recovery
from armored_core.services import PublicationResult, StudioResult, SyncService, VisionResult
from armored_core.storage import Storage


class Vision:
    def identify(self, item):
        return VisionResult("final", "https://example.invalid/final")


class Studio:
    def __init__(self, storage):
        self.storage = storage

    def process(self, item):
        working = self.storage.working(item.item_id)
        working.write_bytes(item.original_path.read_bytes())
        result = self.storage.result(item.item_id, item.affiliate_name or "final")
        result.write_bytes(working.read_bytes())
        return StudioResult(working, result)


class Publisher:
    def __init__(self, mode="normal"):
        self.mode = mode
        self.count = 0
        self.published = set()

    def check_publication(self, item):
        if self.mode == "unknown":
            return PublicationCheck.UNKNOWN
        return PublicationCheck.CONFIRMED if item.item_id in self.published else PublicationCheck.ABSENT

    def publish(self, item):
        self.count += 1
        self.published.add(item.item_id)
        return PublicationResult(True, str(self.count))


class CrashAfterExternalPublish(Publisher):
    def publish(self, item):
        self.count += 1
        self.published.add(item.item_id)
        raise RuntimeError("crash-after-external-publication")


class HardeningTests(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        root = Path(self.td.name)
        self.storage = Storage(root)
        self.db = Database(self.storage.database / "armoredcreator.db")
        source = root / "source.mp4"
        source.write_bytes(b"VIDEO")
        self.item = SyncService(self.db, self.storage).ingest(source, "telegram-1")

    def tearDown(self):
        self.db.close()
        self.td.cleanup()

    def test_duplicate_telegram_message_is_idempotent(self):
        source = self.storage.root / "another.mp4"
        source.write_bytes(b"OTHER")
        duplicate = SyncService(self.db, self.storage).ingest(source, "telegram-1")
        self.assertEqual(duplicate, self.item)
        self.assertEqual(len(list(self.storage.videos.iterdir())), 1)

    def test_tampered_original_blocks_pipeline(self):
        original = self.db.get(self.item).original_path
        original.write_bytes(b"TAMPERED")
        with self.assertRaises(IOError):
            Pipeline(self.db, self.storage, Vision(), Studio(self.storage), Publisher()).run(self.item)
        self.assertEqual(self.db.get(self.item).state, State.FAILED)

    def test_unknown_publication_never_publishes(self):
        with self.assertRaisesRegex(RuntimeError, "uncertain"):
            Pipeline(self.db, self.storage, Vision(), Studio(self.storage), Publisher("unknown")).run(self.item)
        self.assertEqual(self.db.get(self.item).state, State.FAILED)

    def test_crash_after_external_publish_is_recovered_without_duplicate(self):
        with self.assertRaises(RuntimeError):
            Pipeline(self.db, self.storage, Vision(), Studio(self.storage), CrashAfterExternalPublish()).run(self.item)

        # Reuse the same external publisher state: the publication already exists remotely.
        publisher = Publisher()
        publisher.published.add(self.item)
        Recovery(self.db, self.storage, Vision(), Studio(self.storage), publisher).reconcile(self.item)

        self.assertEqual(self.db.get(self.item).state, State.PUBLISHED)
        self.assertEqual(publisher.count, 0)

    def test_two_workers_only_one_claims(self):
        barrier = threading.Barrier(2)
        results = []

        def claim(worker):
            barrier.wait()
            results.append(self.db.claim(self.item, worker, 300))

        threads = [threading.Thread(target=claim, args=(f"worker-{i}",)) for i in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        self.assertEqual(sum(results), 1)


if __name__ == "__main__":
    unittest.main()
