import os
import tempfile
import unittest
from pathlib import Path

from ArmoredHub.service import ArmoredHub
from armored_core.database import Database
from armored_core.models import PublicationCheck
from armored_core.storage import Storage


class HubContractTests(unittest.TestCase):
    def _item(self, db, storage):
        item_id = db.create_item(
            "hub-test-1",
            storage.original("hub-test-1"),
            original_url="https://shopee.example/p/1",
        )
        original = storage.original(item_id)
        original.write_bytes(b"SOURCE")
        db.conn.execute(
            "UPDATE items SET original_path=?, affiliate_name=?, affiliate_url=? WHERE content_id=?",
            (str(original), "product", "https://shopee.example/abc/finaldomeulinknovo", item_id),
        )
        db.conn.commit()
        item = db.get(item_id)
        result = storage.result(item.content_id, item.affiliate_url, item.affiliate_name)
        result.write_bytes(b"RESULT")
        db.set_result(item.item_id, result)
        return db.get(item.item_id)

    def test_hub_uses_sqlite_not_parallel_state_folder(self):
        old = os.environ.get("ARMORED_HUB_DRY_RUN")
        os.environ["ARMORED_HUB_DRY_RUN"] = "1"
        try:
            with tempfile.TemporaryDirectory() as td:
                root = Path(td)
                storage = Storage(root)
                db = Database(storage.database / "db.sqlite")
                item = self._item(db, storage)
                db.publication_started(item.item_id)
                hub = ArmoredHub(root, db)
                self.assertEqual(hub.check_publication(item), PublicationCheck.UNKNOWN)

                db.conn.execute("DELETE FROM publications WHERE content_id=?", (item.item_id,))
                db.conn.commit()
                item = db.get(item.item_id)
                self.assertEqual(hub.check_publication(item), PublicationCheck.ABSENT)
                with self.assertRaises(RuntimeError):
                    hub.publish(item)
                self.assertEqual(db.publication(item.item_id)["confirmed"], 0)
                self.assertEqual(db.publication(item.item_id)["verification_status"], "PENDING")
                self.assertFalse((root / "hub").exists())
                db.close()
        finally:
            if old is None:
                os.environ.pop("ARMORED_HUB_DRY_RUN", None)
            else:
                os.environ["ARMORED_HUB_DRY_RUN"] = old

    def test_unresolved_publication_reconciles_by_exact_result(self):
        try:
            with tempfile.TemporaryDirectory() as td:
                root = Path(td)
                storage = Storage(root)
                db = Database(storage.database / "db.sqlite")
                item = self._item(db, storage)
                db.publication_started(item.item_id)
                hub = ArmoredHub(root, db)

                hub._find_telegram_publications = lambda current: ["9876"]
                self.assertEqual(hub.check_publication(item), PublicationCheck.CONFIRMED)
                self.assertEqual(db.publication(item.item_id)["published_message_id"], "9876")
                self.assertEqual(db.publication(item.item_id)["confirmed"], 1)
                db.close()
        finally:
            pass


if __name__ == "__main__":
    unittest.main()
