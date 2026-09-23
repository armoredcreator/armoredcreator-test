import tempfile
import unittest
from pathlib import Path

from armored_core.coordinator import Coordinator
from armored_core.database import Database
from armored_core.models import PublicationCheck
from armored_core.services import PublicationResult, StudioResult, VisionResult
from armored_core.storage import Storage


class _Source:
    async def collect_historical_batch_async(self):
        return [], {228: 99}

    async def disconnect(self):
        pass

    async def fetch_live_batch_async(self):
        return [], {}

    async def disconnect(self):
        pass


class _Vision:
    def identify(self, item):
        return VisionResult("affiliate", "https://example.invalid/a")


class _Studio:
    def process(self, item):
        raise AssertionError("studio should not run")


class _Publisher:
    def check_publication(self, item):
        return PublicationCheck.ABSENT

    def publish(self, item):
        return PublicationResult(True, "unused")


class CoordinatorOperationalSafetyTests(unittest.TestCase):
    def test_run_forever_releases_runtime_lock_after_live_failure(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            storage = Storage(root)
            db = Database(storage.database / "db.sqlite")
            db.complete_historical_sync()
            db.set_sync_topic_checkpoint(228, "topic", 99)

            class FailingSource(_Source):
                async def fetch_live_batch_async(self, limit=1):
                    raise RuntimeError("simulated-live-source-failure")

            coordinator = Coordinator(
                db, storage, _Vision(), _Studio(), _Publisher(), FailingSource()
            )
            try:
                coordinator.run_forever(max_cycles=1, poll_seconds=0)

                self.assertIsNone(
                    db.conn.execute(
                        "SELECT 1 FROM runtime_locks WHERE name='coordinator'"
                    ).fetchone()
                )
            finally:
                coordinator.close()

    def test_run_forever_releases_runtime_lock_after_catchup_failure(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            storage = Storage(root)
            db = Database(storage.database / "db.sqlite")

            class FailingSource(_Source):
                async def fetch_next_async(self):
                    raise RuntimeError("simulated-catchup-source-failure")

            coordinator = Coordinator(
                db, storage, _Vision(), _Studio(), _Publisher(), FailingSource()
            )
            try:
                with self.assertRaisesRegex(RuntimeError, "simulated-catchup-source-failure"):
                    coordinator.run_forever(max_cycles=1, poll_seconds=0)

                self.assertIsNone(
                    db.conn.execute(
                        "SELECT 1 FROM runtime_locks WHERE name='coordinator'"
                    ).fetchone()
                )
            finally:
                coordinator.close()


if __name__ == "__main__":
    unittest.main()
