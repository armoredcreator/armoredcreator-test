import tempfile
import unittest
from pathlib import Path

from ArmoredHub.service import ArmoredHub
from ArmoredStudio.service import ArmoredStudio
from armored_core.database import Database
from armored_core.models import PublicationCheck, State
from armored_core.pipeline import Pipeline
from armored_core.services import (
    PublicationResult,
    StudioResult,
    SyncService,
    VisionResult,
)
from armored_core.storage import Storage


class FakeVision:
    def identify(self, item):
        assert item.original_path and item.original_path.is_file()
        return VisionResult("e2e-affiliate", "https://example.invalid/affiliate")


class FakeStudioProcessor:
    def __init__(self, storage):
        self.storage = storage

    def __call__(self, item):
        assert item.original_path and item.original_path.is_file()
        working = self.storage.working(item.item_id)
        result = self.storage.result(item.item_id, item.affiliate_name or "e2e-affiliate")
        working.write_bytes(item.original_path.read_bytes())
        result.write_bytes(working.read_bytes())
        return StudioResult(working, result)


class FakeTelegramPublisher:
    def __init__(self):
        self.published = {}
        self.publish_calls = 0

    def check_publication(self, item):
        return (
            PublicationCheck.CONFIRMED
            if item.item_id in self.published
            else PublicationCheck.ABSENT
        )

    def publish(self, item):
        self.publish_calls += 1
        message_id = f"telegram-e2e-{item.item_id}"
        self.published[item.item_id] = message_id
        return PublicationResult(True, message_id)


class EndToEndPipelineTests(unittest.TestCase):
    """Deterministic full-chain lab test: Telegram ingest -> Vision -> Studio -> Hub -> PUBLISHED."""

    def test_full_pipeline_through_adapters_and_cleanup(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            storage = Storage(root)
            db = Database(storage.database / "armoredcreator.db")
            try:
                source = root / "telegram-input.mp4"
                source.write_bytes(b"REALISTIC-E2E-VIDEO-BYTES")

                # Telegram/ArmoredSync boundary: one incoming message becomes one canonical item.
                item_id = SyncService(db, storage).ingest(
                    source, telegram_message_id="telegram-e2e-message-001"
                )
                item = db.get(item_id)
                self.assertEqual(item.state, State.RECEIVED)
                self.assertTrue(item.original_path.is_file())

                publisher = FakeTelegramPublisher()
                vision = FakeVision()
                studio = ArmoredStudio(FakeStudioProcessor(storage))
                hub = ArmoredHub(publisher)

                # The pipeline consumes the real canonical adapters, not test-only shortcuts.
                pipeline = Pipeline(
                    db,
                    storage,
                    vision,
                    studio,
                    hub,
                )
                pipeline.run(item_id, worker_id="e2e-worker")

                final = db.get(item_id)
                self.assertEqual(final.state, State.PUBLISHED)
                self.assertEqual(publisher.publish_calls, 1)
                self.assertEqual(
                    publisher.published[item_id],
                    f"telegram-e2e-{item_id}",
                )

                # PUBLISHED is terminal and cleanup leaves only the immutable original.
                self.assertTrue(final.original_path.is_file())
                self.assertEqual(
                    sorted(p.name for p in final.workspace.iterdir()),
                    [final.original_path.name],
                )

                # A second execution must not publish again.
                pipeline.run(item_id, worker_id="e2e-worker-2")
                self.assertEqual(publisher.publish_calls, 1)
                self.assertEqual(db.get(item_id).state, State.PUBLISHED)
            finally:
                db.close()


if __name__ == "__main__":
    unittest.main()
