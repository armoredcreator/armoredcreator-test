import os
import tempfile
import unittest
from pathlib import Path

from armored_core.database import Database
from armored_core.storage import Storage


class RuntimeLockTests(unittest.TestCase):
    def test_dead_runtime_lock_is_reclaimed_after_restart(self):
        with tempfile.TemporaryDirectory() as td:
            db = Database(Storage(Path(td)).database / "db.sqlite")
            try:
                db.conn.execute(
                    "INSERT INTO runtime_locks(name,pid,started_at,heartbeat_at) VALUES(?,?,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)",
                    ("coordinator", 99999999),
                )
                db.conn.commit()

                db.acquire_runtime_lock("coordinator")

                row = db.conn.execute(
                    "SELECT pid FROM runtime_locks WHERE name='coordinator'"
                ).fetchone()
                self.assertEqual(int(row["pid"]), os.getpid())
                db.release_runtime_lock("coordinator")
            finally:
                db.close()

    def test_runtime_lock_is_removed_on_release(self):
        with tempfile.TemporaryDirectory() as td:
            db = Database(Storage(Path(td)).database / "db.sqlite")
            db.acquire_runtime_lock("coordinator")
            db.release_runtime_lock("coordinator")

            row = db.conn.execute(
                "SELECT 1 FROM runtime_locks WHERE name='coordinator'"
            ).fetchone()
            self.assertIsNone(row)
            db.close()


if __name__ == "__main__":
    unittest.main()
