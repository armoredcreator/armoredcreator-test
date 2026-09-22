import tempfile
import unittest
from pathlib import Path

from armored_core.database import Database
from armored_core.models import State
from armored_core.startup_audit import StartupReconciler
from armored_core.storage import Storage


class _Publisher:
    def __init__(self):
        self.checked = []

    def check_publication(self, item):
        self.checked.append(item.content_id)
        raise AssertionError("confirmed publication should not be queried")


class StartupAuditTests(unittest.TestCase):
    def test_startup_audit_reports_inventory_and_does_not_touch_confirmed_publication(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            storage = Storage(root)
            original = storage.original("101", original_url="https://shopee.com.br/101")
            original.write_bytes(b"ORIGINAL")

            db = Database(storage.database / "armoredcreator.db")
            item_id = db.create_item(
                "101",
                original,
                topic_id=228,
                topic_name="topic",
                original_url="https://shopee.com.br/101",
            )
            db.set_result(item_id, storage.result("101", "https://shopee.com.br/101", "produto"))
            db.publication_started(item_id, destination_chat_id="-100", destination_topic_id=228)
            db.publication_confirmed(item_id, "900")
            db.transition(item_id, State.PUBLISHED, "test")
            db.mark_cleanup_completed(item_id)

            orphan = storage.videos / "orphan-999"
            orphan.mkdir(parents=True)

            publisher = _Publisher()
            summary = StartupReconciler(db, storage, publisher).run()

            self.assertEqual(summary["items"], 1)
            self.assertEqual(summary["published"], 1)
            self.assertEqual(summary["publication_confirmed"], 1)
            self.assertEqual(summary["orphans"], ["orphan-999"])
            self.assertEqual(publisher.checked, [])

            db.close()


if __name__ == "__main__":
    unittest.main()
