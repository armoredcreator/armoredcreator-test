import tempfile
import unittest
from pathlib import Path

from armored_core.database import Database
from armored_core.models import State
from armored_core.services import SyncService
from armored_core.storage import Storage


class IntegrityAndConcurrencyTests(unittest.TestCase):
    def test_ingest_records_sha256_and_never_uses_pending_reservation(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            storage = Storage(root)
            db = Database(storage.database / "db.sqlite")
            source = root / "source.mp4"
            source.write_bytes(b"immutable-video")
            item_id = SyncService(db, storage).ingest(source, "telegram-1")
            row = db.conn.execute(
                "SELECT original_path, original_size, original_sha256 FROM items WHERE id=?",
                (item_id,),
            ).fetchone()
            self.assertTrue(Path(row["original_path"]).is_file())
            self.assertEqual(row["original_size"], len(b"immutable-video"))
            self.assertEqual(len(row["original_sha256"]), 64)
            self.assertFalse((storage.database / "pending-original.mp4.reservation").exists())
            self.assertFalse((storage.videos / "_incoming").exists())
            db.close()

    def test_invalid_state_transition_is_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            storage = Storage(root)
            db = Database(storage.database / "db.sqlite")
            item = db.create_item("telegram-2", storage.original(1))
            with self.assertRaises(ValueError):
                db.transition(item, State.PUBLISHED, "invalid")
            self.assertEqual(db.get(item).state, State.RECEIVED)
            db.close()

    def test_published_item_cannot_be_claimed_by_another_worker(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            storage = Storage(root)
            db = Database(storage.database / "db.sqlite")
            item = db.create_item("telegram-3", storage.original(1))
            self.assertTrue(db.claim(item, "worker-a"))
            self.assertFalse(db.claim(item, "worker-b"))
            db.release(item, "worker-a")
            self.assertTrue(db.claim(item, "worker-b"))
            db.close()

    def test_stale_claim_can_be_recovered(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            storage = Storage(root)
            db = Database(storage.database / "db.sqlite")
            item = db.create_item("telegram-stale", storage.original(1))
            self.assertTrue(db.claim(item, "dead-worker"))
            db.conn.execute("UPDATE items SET claimed_at=datetime('now','-3600 seconds') WHERE id=?", (item,))
            db.conn.commit()
            self.assertTrue(db.claim(item, "recovery-worker", lease_seconds=300))
            self.assertEqual(db.conn.execute("SELECT claimed_by FROM items WHERE id=?", (item,)).fetchone()[0], "recovery-worker")
            db.close()

if __name__ == "__main__":
    unittest.main()
