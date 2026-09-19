import tempfile
import unittest
from pathlib import Path

from armored_core.database import Database
from armored_core.models import PublicationCheck, State
from armored_core.pipeline import Pipeline
from armored_core.services import PublicationResult, StudioResult, SyncService, VisionResult
from armored_core.storage import Storage

class Vision:
    def identify(self, item):
        return VisionResult("produto-final", "https://example.invalid/a")

class Studio:
    def __init__(self, storage):
        self.storage = storage

    def process(self, item):
        w = self.storage.working(item.item_id)
        w.write_bytes(item.original_path.read_bytes())
        r = self.storage.result(item.item_id, item.affiliate_name)
        r.write_bytes(w.read_bytes())
        return StudioResult(w, r)

class Publisher:
    def __init__(self):
        self.count = 0
        self.ids = set()

    def check_publication(self, item):
        return PublicationCheck.CONFIRMED if item.item_id in self.ids else PublicationCheck.ABSENT

    def publish(self, item):
        self.count += 1
        self.ids.add(item.item_id)
        return PublicationResult(True, str(self.count))

class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        root = Path(self.td.name)
        self.storage = Storage(root)
        self.db = Database(self.storage.database / "armoredcreator.db")
        src = root / "source.mp4"
        src.write_bytes(b"VIDEO")
        self.item = SyncService(self.db, self.storage).ingest(src, "msg-1")
        self.pub = Publisher()

    def tearDown(self):
        self.db.close()
        self.td.cleanup()

    def test_happy_path_keeps_only_original(self):
        Pipeline(self.db, self.storage, Vision(), Studio(self.storage), self.pub).run(self.item)
        row = self.db.get(self.item)
        self.assertEqual(row.state, State.PUBLISHED)
        self.assertEqual(row.original_path.read_bytes(), b"VIDEO")
        self.assertEqual([p.name for p in row.workspace.iterdir()], [row.original_path.name])
        self.assertEqual(self.pub.count, 1)
        self.assertTrue(row.working_path is not None)
        self.assertTrue(row.result_path is not None)

    def test_second_run_does_not_republish(self):
        p = Pipeline(self.db, self.storage, Vision(), Studio(self.storage), self.pub)
        p.run(self.item)
        p.run(self.item)
        self.assertEqual(self.pub.count, 1)

if __name__ == "__main__":
    unittest.main()
