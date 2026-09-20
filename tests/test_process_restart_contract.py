import os
import tempfile
import unittest
from pathlib import Path

from armored_core.database import Database
from armored_core.storage import Storage


class ProcessRestartContractTests(unittest.TestCase):
    def test_reopened_database_preserves_live_mode_checkpoint_and_pending_item(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            storage = Storage(root)
            path = storage.original("telegram-500", original_url="https://shopee.com.br/p/abc")
            path.write_bytes(b"ORIGINAL")

            db = Database(storage.database / "armoredcreator.db")
            db.complete_historical_sync()
            db.set_sync_topic_checkpoint(228, "Telegram 228", 500)
            item = db.create_item("telegram-500", path, topic_id=228, topic_name="Telegram 228")
            db.conn.execute(
                "UPDATE items SET state='STUDIO', working_path=? WHERE content_id=?",
                (str(storage.working(item)), item),
            )
            db.conn.commit()
            db.close()

            restarted = Database(storage.database / "armoredcreator.db")
            self.assertTrue(restarted.historical_complete())
            self.assertEqual(restarted.sync_topic_checkpoint(228), 500)
            row = restarted.get("telegram-500")
            self.assertEqual(row.state.value, "STUDIO")
            self.assertTrue(Path(row.original_path).exists())
            restarted.close()


if __name__ == "__main__":
    unittest.main()
