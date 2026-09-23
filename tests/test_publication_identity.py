import tempfile
import unittest
from pathlib import Path

from armored_core.database import Database
from armored_core.models import PublicationCheck, State
from armored_core.pipeline import Pipeline
from armored_core.recovery import Recovery
from armored_core.services import PublicationResult, StudioResult, SyncService, VisionResult
from armored_core.storage import Storage


class Vision:
    def identify(self, item):
        return VisionResult("product", "https://example.invalid/affiliate")


class Studio:
    def __init__(self, storage):
        self.storage = storage

    def process(self, item):
        working = self.storage.working(item.item_id)
        working.write_bytes(item.original_path.read_bytes())
        result = self.storage.result(item.item_id, item.affiliate_name or "product")
        result.write_bytes(working.read_bytes())
        return StudioResult(working, result)


class UnknownPublisher:
    def check_publication(self, item):
        return PublicationCheck.UNKNOWN

    def publish(self, item):
        raise AssertionError("publish must never run when publication check is UNKNOWN")


class ConfirmedWithoutIdPublisher:
    def check_publication(self, item):
        return PublicationCheck.CONFIRMED

    def publish(self, item):
        raise AssertionError("publish must not run for an already confirmed publication")


class ConfirmedWithIdPublisher:
    def __init__(self):
        self.ids = {"123"}

    def check_publication(self, item):
        return PublicationCheck.CONFIRMED

    def publish(self, item):
        raise AssertionError("publish must not run for an already confirmed publication")


class PublicationIdentityTests(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        root = Path(self.td.name)
        self.storage = Storage(root)
        self.db = Database(self.storage.database / "armoredcreator.db")
        source = root / "source.mp4"
        source.write_bytes(b"VIDEO")
        self.item = SyncService(self.db, self.storage).ingest(source, "msg-identity")

    def tearDown(self):
        self.db.close()
        self.td.cleanup()

    def _prepare_publishing(self):
        item = self.db.get(self.item)
        self.db.set_vision(self.item, "product", "https://example.invalid/affiliate")
        result = self.storage.result(self.item, "https://example.invalid/affiliate", "product")
        result.write_bytes(item.original_path.read_bytes())
        self.db.set_result(self.item, result)
        self.db.transition(self.item, State.PUBLISHING, "test")

    def test_unknown_publication_check_enters_recovery_not_failed(self):
        self._prepare_publishing()
        Pipeline(self.db, self.storage, Vision(), Studio(self.storage), UnknownPublisher()).run(self.item)
        row = self.db.get(self.item)
        self.assertEqual(row.state, State.RECOVERY)
        self.assertNotEqual(row.state, State.FAILED)
        publication = self.db.publication(self.item)
        self.assertIsNotNone(publication)
        self.assertEqual(publication["idempotency_key"], f"armoredcreator:content:{self.item}")
        self.assertEqual(publication["confirmed"], 0)

    def test_confirmed_publication_without_real_id_never_becomes_published(self):
        self._prepare_publishing()
        self.db.publication_started(self.item)
        Pipeline(
            self.db, self.storage, Vision(), Studio(self.storage),
            ConfirmedWithoutIdPublisher(),
        ).run(self.item)
        self.assertEqual(self.db.get(self.item).state, State.RECOVERY)
        self.assertNotEqual(self.db.get(self.item).state, State.PUBLISHED)

    def test_recovery_requires_real_id_for_existing_confirmation(self):
        self._prepare_publishing()
        self.db.publication_started(self.item)
        self.db.conn.execute(
            "UPDATE publications SET confirmed=1, verification_status='CONFIRMED' WHERE content_id=?",
            (self.item,),
        )
        self.db.conn.commit()

        with self.assertRaises(RuntimeError) as ctx:
            Recovery(
                self.db, self.storage, Vision(), Studio(self.storage),
                ConfirmedWithoutIdPublisher(),
            ).reconcile(self.item)
        self.assertIn("without-real-message-id", str(ctx.exception))
        self.assertEqual(self.db.get(self.item).state, State.RECOVERY)

    def test_recovery_uses_real_id_when_already_confirmed(self):
        self._prepare_publishing()
        self.db.publication_started(self.item)
        self.db.publication_message_sent(self.item, "123")
        self.db.publication_confirmed(self.item, "123")

        Recovery(
            self.db, self.storage, Vision(), Studio(self.storage),
            ConfirmedWithIdPublisher(),
        ).reconcile(self.item)

        row = self.db.get(self.item)
        publication = self.db.publication(self.item)
        self.assertEqual(row.state, State.PUBLISHED)
        self.assertEqual(publication["published_message_id"], "123")


if __name__ == "__main__":
    unittest.main()
