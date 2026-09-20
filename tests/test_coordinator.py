import tempfile
import unittest
from pathlib import Path

from armored_core.coordinator import Coordinator
from armored_core.database import Database
from armored_core.models import PublicationCheck, State
from armored_core.services import PublicationResult, StudioResult, VisionResult
from armored_core.storage import Storage
from armored_core.production_contracts import SourceMessage

class Source:
    def __init__(self, path):
        self.path = path
        self.used = False
    def fetch_next(self):
        if self.used:
            return None
        self.used = True
        return SourceMessage(self.path, "telegram-100")

class Vision:
    def identify(self, item):
        return VisionResult("affiliate", "https://example.invalid/affiliate")

class Studio:
    def __init__(self, storage):
        self.storage = storage
    def process(self, item):
        w = self.storage.working(item.item_id)
        r = self.storage.result(item.item_id, item.affiliate_name or "affiliate")
        payload = item.original_path.read_bytes()
        w.write_bytes(payload)
        r.write_bytes(payload + b"-processed")
        return StudioResult(w, r)

class Publisher:
    def __init__(self):
        self.ids = set()
        self.count = 0
    def check_publication(self, item):
        return PublicationCheck.CONFIRMED if item.item_id in self.ids else PublicationCheck.ABSENT
    def publish(self, item):
        self.count += 1
        self.ids.add(item.item_id)
        return PublicationResult(True, f"telegram-result-{self.count}")



class AsyncSource:
    def __init__(self):
        self.used = False
        self.marked = False

    async def fetch_next_async(self):
        if self.used:
            return None
        self.used = True

        async def materialize(target):
            target.write_bytes(b"ASYNC-TELEGRAM")

        from armored_core.services import IngestMessage
        return IngestMessage(
            telegram_message_id="telegram-async-1",
            source_id="telegram",
            original_url="https://shopee.com.br/example/finaldomeulinknovo",
            materialize=materialize,
        )

    def mark_ingested(self, message_id):
        self.marked = message_id


class CoordinatorTests(unittest.TestCase):
    def test_async_source_materializes_inside_same_event_loop(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            storage = Storage(root)
            db = Database(storage.database / "db.sqlite")
            source = AsyncSource()
            coordinator = Coordinator(db, storage, Vision(), Studio(storage), Publisher(), source)

            item_id = coordinator.ingest_once()

            self.assertEqual(item_id, "telegram-async-1")
            item = db.get(item_id)
            self.assertEqual(item.original_path.name, "telegram-async-1_finaldomeulinknovo.mp4")
            self.assertEqual(item.original_path.read_bytes(), b"ASYNC-TELEGRAM")
            self.assertFalse((root / "storage" / "sync").exists())
            self.assertEqual(source.marked, "telegram-async-1")
            coordinator.close()


    def test_complete_chain_is_composed_and_sequential(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            storage = Storage(root)
            db = Database(storage.database / "db.sqlite")
            src = root / "input.mp4"
            src.write_bytes(b"ORIGINAL")
            pub = Publisher()
            coordinator = Coordinator(db, storage, Vision(), Studio(storage), pub, Source(src))
            item_id = coordinator.process_next()
            self.assertEqual(item_id, "telegram-async-1")
            row = db.get(item_id)
            self.assertEqual(row.state, State.PUBLISHED)
            self.assertEqual(row.original_path.read_bytes(), b"ORIGINAL")
            self.assertEqual(pub.count, 1)
            self.assertIsNone(coordinator.process_next())
            self.assertEqual(pub.count, 1)
            coordinator.close()

if __name__ == "__main__":
    unittest.main()
