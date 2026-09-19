import json
import tempfile
import unittest
from pathlib import Path

from armored_core.config import Settings
from armored_core.services import SyncService
from armored_core.storage import Storage
from armored_core.database import Database


class CleanConfigurationTests(unittest.TestCase):
    def test_settings_are_relative_and_non_secret(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            config = root / "config.json"
            config.write_text(json.dumps({"storage": "storage", "database": "storage/database"}), encoding="utf-8")
            settings = Settings.load(config)
            self.assertEqual(settings.storage, root / "storage")
            self.assertEqual(settings.database, root / "storage/database")
            self.assertNotIn("token", config.read_text(encoding="utf-8").lower())

    def test_ingest_does_not_create_workspace_zero(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            storage = Storage(root)
            db = Database(storage.database / "db.sqlite")
            source = root / "video.mp4"
            source.write_bytes(b"VIDEO")
            item_id = SyncService(db, storage).ingest(source, "message-1")
            self.assertFalse((storage.videos / "0").exists())
            self.assertTrue(db.get(item_id).original_path.exists())
            db.close()


if __name__ == "__main__":
    unittest.main()
