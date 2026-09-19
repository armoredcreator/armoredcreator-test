import tempfile
import unittest
from pathlib import Path

from armored_core.database import Database
from armored_core.models import PublicationCheck, State
from armored_core.pipeline import Pipeline
from armored_core.services import PublicationResult, StudioResult, SyncService, VisionResult
from armored_core.storage import Storage

class V:
    def identify(self, item):
        return VisionResult("affiliate-name", "https://example.invalid")

class S:
    def __init__(self, storage):
        self.storage = storage

    def process(self, item):
        w = self.storage.working(item.item_id)
        w.write_bytes(b"WORK")
        r = self.storage.result(item.item_id, item.affiliate_name)
        r.write_bytes(b"RESULT")
        return StudioResult(w, r)

class P:
    def __init__(self):
        self.published = set()
        self.calls = 0

    def is_published(self, item):
        return item.item_id in self.published

    def publish(self, item):
        self.calls += 1
        self.published.add(item.item_id)
        return PublicationResult(True, "telegram-id")

class InvariantTests(unittest.TestCase):
    def test_original_is_never_overwritten(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            st = Storage(root)
            db = Database(st.database / "db.sqlite")
            src = root / "source.mp4"
            src.write_bytes(b"IMMUTABLE")
            i = SyncService(db, st).ingest(src, "telegram-1")
            original = db.get(i).original_path
            Pipeline(db, st, V(), S(st), P()).run(i)
            self.assertEqual(original.read_bytes(), b"IMMUTABLE")
            self.assertEqual(list(original.parent.iterdir()), [original])
            db.close()

    def test_duplicate_ingest_returns_same_item(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            st = Storage(root)
            db = Database(st.database / "db.sqlite")
            src = root / "source.mp4"
            src.write_bytes(b"X")
            sync = SyncService(db, st)
            a = sync.ingest(src, "telegram-1")
            b = sync.ingest(src, "telegram-1")
            self.assertEqual(a, b)
            db.close()

    def test_missing_original_blocks_processing(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            st = Storage(root)
            db = Database(st.database / "db.sqlite")
            src = root / "source.mp4"
            src.write_bytes(b"X")
            i = SyncService(db, st).ingest(src, "telegram-1")
            original = db.get(i).original_path
            original.unlink()
            with self.assertRaises(FileNotFoundError):
                Pipeline(db, st, V(), S(st), P()).run(i)
            self.assertEqual(db.get(i).state, State.FAILED)
            db.close()

if __name__ == "__main__":
    unittest.main()
