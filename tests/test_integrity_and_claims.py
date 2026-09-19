from pathlib import Path
import tempfile
import threading
import unittest

from armored_core.database import Database
from armored_core.models import State
from armored_core.services import SyncService
from armored_core.storage import Storage


class IntegrityAndConcurrencyTests(unittest.TestCase):
    def test_ingest_records_sha256_and_never_uses_pending_reservation(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); storage = Storage(root); db = Database(storage.database / "db.sqlite")
            source = root / "source.mp4"; source.write_bytes(b"immutable-video")
            item_id = SyncService(db, storage).ingest(source, "telegram-1")
            row = db.conn.execute("SELECT original_path, original_size, original_sha256 FROM items WHERE id=?", (item_id,)).fetchone()
            self.assertTrue(Path(row["original_path"]).is_file())
            self.assertEqual(row["original_size"], len(b"immutable-video"))
            self.assertEqual(len(row["original_sha256"]), 64)
            db.close()

    def test_create_item_has_no_fake_original_path(self):
        with tempfile.TemporaryDirectory() as td:
            storage = Storage(Path(td)); db = Database(storage.database / "db.sqlite")
            item = db.create_item("telegram-empty")
            row = db.conn.execute("SELECT original_path FROM items WHERE id=?", (item,)).fetchone()
            self.assertIsNone(row["original_path"])
            self.assertIsNone(db.get(item).original_path)
            db.close()

    def test_state_transition_is_compare_and_set(self):
        with tempfile.TemporaryDirectory() as td:
            storage = Storage(Path(td)); db = Database(storage.database / "db.sqlite")
            item = db.create_item("telegram-cas")
            db.transition(item, State.VISION, "first")
            db.transition(item, State.STUDIO, "second")
            self.assertEqual(db.get(item).state, State.STUDIO)
            db.close()

    def test_duplicate_ingest_concurrency_has_one_item(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); storage = Storage(root)
            src = root / "source.mp4"; src.write_bytes(b"VIDEO")
            db1 = None; db2 = None
            ids=[]; errors=[]
            def run():
                db = Database(storage.database / "db.sqlite")
                try: ids.append(SyncService(db, storage).ingest(src, "same-message"))
                finally: db.close()
                except Exception as exc: errors.append(exc)
            a=threading.Thread(target=run); b=threading.Thread(target=run)
            a.start(); b.start(); a.join(); b.join()
            self.assertFalse(errors, errors)
            self.assertEqual(ids[0], ids[1])
            check = Database(storage.database / "db.sqlite")
            self.assertEqual(check.conn.execute("SELECT COUNT(*) FROM items").fetchone()[0], 1)
            check.close()

    def test_invalid_state_transition_is_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            storage = Storage(Path(td)); db = Database(storage.database / "db.sqlite")
            item = db.create_item("telegram-2")
            with self.assertRaises(ValueError): db.transition(item, State.PUBLISHED, "invalid")
            self.assertEqual(db.get(item).state, State.RECEIVED); db.close()

    def test_published_item_is_terminal_for_claims(self):
        with tempfile.TemporaryDirectory() as td:
            storage = Storage(Path(td)); db = Database(storage.database / "db.sqlite")
            item = db.create_item("telegram-3")
            db.transition(item, State.VISION, "x"); db.transition(item, State.STUDIO, "x")
            db.transition(item, State.PUBLISHING, "x"); db.transition(item, State.PUBLISHED, "x")
            self.assertFalse(db.claim(item, "worker-a")); db.close()

    def test_stale_claim_can_be_recovered(self):
        with tempfile.TemporaryDirectory() as td:
            storage = Storage(Path(td)); db = Database(storage.database / "db.sqlite")
            item = db.create_item("telegram-stale"); self.assertTrue(db.claim(item, "dead-worker"))
            db.conn.execute("UPDATE items SET claimed_at=datetime('now','-3600 seconds') WHERE id=?", (item,)); db.conn.commit()
            self.assertTrue(db.claim(item, "recovery-worker", lease_seconds=300)); db.close()

    def test_tampered_original_is_detected(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td); storage=Storage(root); db=Database(storage.database / "db.sqlite")
            src=root/"source.mp4"; src.write_bytes(b"original"); item=SyncService(db,storage).ingest(src,"telegram-tamper")
            db.get(item).original_path.write_bytes(b"tampered")
            self.assertFalse(db.original_intact(item)); db.close()

    def test_failed_state_cannot_be_failed_again(self):
        with tempfile.TemporaryDirectory() as td:
            storage=Storage(Path(td)); db=Database(storage.database/"db.sqlite"); item=db.create_item("telegram-fail")
            db.fail(item,"first")
            with self.assertRaises(ValueError): db.fail(item,"second")
            db.close()


if __name__ == "__main__":
    unittest.main()
