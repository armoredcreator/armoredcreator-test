import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

from armored_core.database import Database
from armored_core.models import PublicationCheck, State
from armored_core.pipeline import Pipeline
from armored_core.recovery import Recovery
from armored_core.services import PublicationResult, StudioResult, SyncService, VisionResult
from armored_core.storage import Storage


class Vision:
    def identify(self, item):
        return VisionResult("recover-final", "https://example.invalid/a")


class Studio:
    def __init__(self, storage):
        self.storage = storage

    def process(self, item):
        w = self.storage.working(item.item_id)
        w.write_bytes(item.original_path.read_bytes())
        r = self.storage.result(item.item_id, item.affiliate_name or "recover-final")
        r.write_bytes(w.read_bytes())
        return StudioResult(w, r)


class CrashStudio(Studio):
    def process(self, item):
        raise RuntimeError("simulated studio crash")


class Publisher:
    def __init__(self):
        self.ids = set()
        self.count = 0

    def check_publication(self, item):
        return PublicationCheck.CONFIRMED if item.item_id in self.ids else PublicationCheck.ABSENT

    def publish(self, item):
        self.count += 1
        self.ids.add(item.item_id)
        return PublicationResult(True, str(self.count))


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        root = Path(self.td.name)
        self.storage = Storage(root)
        self.db = Database(self.storage.database / "armoredcreator.db")
        src = root / "source.mp4"
        src.write_bytes(b"VIDEO")
        self.item = SyncService(self.db, self.storage).ingest(src, "msg")
        self.pub = Publisher()

    def tearDown(self):
        self.db.close()
        self.td.cleanup()

    def test_rebuilds_after_studio_crash(self):
        with self.assertRaises(RuntimeError):
            Pipeline(self.db, self.storage, Vision(), CrashStudio(self.storage), self.pub).run(self.item)
        self.assertEqual(self.db.get(self.item).state, State.FAILED)
        Recovery(self.db, self.storage, Vision(), Studio(self.storage), self.pub).reconcile(self.item)
        row = self.db.get(self.item)
        self.assertEqual(row.state, State.PUBLISHED)
        self.assertTrue(row.original_path.exists())
        self.assertEqual([p.name for p in row.workspace.iterdir()], [row.original_path.name])

    def test_recovery_rebuilds_working_when_result_missing(self):
        Pipeline(self.db, self.storage, Vision(), Studio(self.storage), self.pub).run(self.item)
        row = self.db.get(self.item)
        row.result_path.unlink(missing_ok=True)
        self.db.transition(self.item, State.STUDIO, "test-result-missing")
        Recovery(self.db, self.storage, Vision(), Studio(self.storage), self.pub).reconcile(self.item)
        self.assertEqual(self.db.get(self.item).state, State.PUBLISHED)
        self.assertEqual(self.pub.count, 1)

    def test_recovery_cannot_run_while_worker_claimed(self):
        worker = "pipeline-worker"
        self.assertTrue(self.db.claim(self.item, worker))
        with self.assertRaises(RuntimeError):
            Recovery(self.db, self.storage, Vision(), Studio(self.storage), self.pub).reconcile(self.item, "recovery-worker")
        self.db.release(self.item, worker)

    def test_cleanup_is_idempotent(self):
        Pipeline(self.db, self.storage, Vision(), Studio(self.storage), self.pub).run(self.item)
        Recovery(self.db, self.storage, Vision(), Studio(self.storage), self.pub).reconcile(self.item)
        self.assertTrue(self.db.get(self.item).original_path.exists())

    def test_cleanup_interruption_is_recoverable(self):
        Pipeline(self.db, self.storage, Vision(), Studio(self.storage), self.pub).run(self.item)
        row = self.db.get(self.item)
        working, result = row.working_path, row.result_path
        working.write_bytes(b"leftover")
        result.write_bytes(b"leftover")
        with patch.object(Path, "unlink", side_effect=OSError("simulated-cleanup-failure")):
            with self.assertRaises(OSError):
                Pipeline(self.db, self.storage, Vision(), Studio(self.storage), self.pub).cleanup(self.item)
        self.assertEqual(self.db.get(self.item).state, State.PUBLISHED)
        self.assertTrue(working.exists())
        self.assertTrue(result.exists())
        Recovery(self.db, self.storage, Vision(), Studio(self.storage), self.pub).reconcile(self.item)
        self.assertFalse(working.exists())
        self.assertFalse(result.exists())
        self.assertTrue(self.db.get(self.item).original_path.exists())

    def test_unknown_publication_never_publishes(self):
        class UnknownPublisher(Publisher):
            def publish(self, item):
                raise AssertionError("publish must not be called")
            def check_publication(self, item):
                return PublicationCheck.UNKNOWN

        with self.assertRaises(RuntimeError):
            Pipeline(self.db, self.storage, Vision(), Studio(self.storage), UnknownPublisher()).run(self.item)
        self.assertEqual(self.db.get(self.item).state, State.FAILED)

    def test_recovery_handles_external_publish_before_db_confirmation(self):
        class CrashAfterExternalPublish(Publisher):
            def publish(self, item):
                self.count += 1
                self.ids.add(item.item_id)
                raise RuntimeError("crash-after-external-publish")

        crashing = CrashAfterExternalPublish()
        with self.assertRaises(RuntimeError):
            Pipeline(self.db, self.storage, Vision(), Studio(self.storage), crashing).run(self.item)
        self.assertEqual(self.db.get(self.item).state, State.FAILED)
        Recovery(self.db, self.storage, Vision(), Studio(self.storage), crashing).reconcile(self.item)
        self.assertEqual(self.db.get(self.item).state, State.PUBLISHED)
        self.assertEqual(crashing.count, 1)

if __name__ == "__main__":
    unittest.main()
