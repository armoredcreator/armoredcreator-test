import os
import tempfile
import unittest
from pathlib import Path

from armored_core.coordinator import Coordinator
from armored_core.database import Database
from armored_core.models import PublicationCheck, State
from armored_core.services import PublicationResult, VisionResult
from armored_core.storage import Storage
from armored_core.production_contracts import SourceMessage
from ArmoredStudio.service import ArmoredStudio


class Source:
    def __init__(self, path):
        self.path = path
        self.used = False

    def fetch_next(self):
        if self.used:
            return None
        self.used = True
        return SourceMessage(self.path, "e2e-100", "local", original_url="https://example.invalid/product")


class Vision:
    def identify(self, item):
        return VisionResult("e2e-product", "https://example.invalid/affiliate")


class CrashStudio:
    def __init__(self, real):
        self.real = real

    def process(self, item):
        raise RuntimeError("simulated studio crash")


class PersistentPublisher:
    def __init__(self, published=None, crash_after_publish=False):
        self.published = published if published is not None else set()
        self.count = 0
        self.crash_after_publish = crash_after_publish

    def check_publication(self, item):
        return PublicationCheck.CONFIRMED if item.item_id in self.published else PublicationCheck.ABSENT

    def publish(self, item):
        self.count += 1
        self.published.add(item.item_id)
        if self.crash_after_publish:
            raise RuntimeError("simulated crash after external publication")
        return PublicationResult(True, f"e2e-message-{item.item_id}")


class Bindings:
    def __init__(self, source, vision, studio, publisher):
        self.source = source
        self.vision = vision
        self.studio = studio
        self.publisher = publisher


class EndToEndRecoveryTests(unittest.TestCase):
    def test_real_studio_chain_recovers_after_studio_failure(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            input_dir = root / "input"
            input_dir.mkdir()
            source = input_dir / "e2e-200.mp4"
            original = b"DETERMINISTIC-E2E-VIDEO"
            source.write_bytes(original)

            storage = Storage(root)
            db = Database(storage.database / "armoredcreator.db")
            publisher = PersistentPublisher()

            coordinator = Coordinator.build(
                root,
                Bindings(Source(source), Vision(), CrashStudio(ArmoredStudio(root)), publisher),
            )
            item_id = coordinator.ingest_once()

            self.assertEqual(item_id, 1)
            with self.assertRaises(RuntimeError):
                coordinator.run(item_id)
            self.assertEqual(db.get(item_id).state, State.FAILED)
            coordinator.close()

            db = Database(storage.database / "armoredcreator.db")
            coordinator = Coordinator.build(
                root,
                Bindings(Source(source), Vision(), ArmoredStudio(root), publisher),
            )
            os.environ["ARMORED_STUDIO_ALLOW_COPY"] = "1"
            coordinator.recover(item_id)
            self.assertEqual(db.get(item_id).state, State.PUBLISHED)
            self.assertEqual(db.get(item_id).original_path.read_bytes(), original)
            self.assertEqual(publisher.count, 1)

            row = db.get(item_id)
            self.assertEqual([p.name for p in row.workspace.iterdir()], [row.original_path.name])
            events = [r["new_state"] for r in db.conn.execute(
                "SELECT new_state FROM state_events WHERE item_id=? ORDER BY id", (item_id,)
            ).fetchall()]
            self.assertIn(State.VISION.value, events)
            self.assertIn(State.STUDIO.value, events)
            self.assertIn(State.PUBLISHING.value, events)
            self.assertIn(State.FAILED.value, events)

            coordinator.close()
            os.environ.pop("ARMORED_STUDIO_ALLOW_COPY", None)

    def test_recovery_after_external_publication_does_not_duplicate(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            input_dir = root / "input"
            input_dir.mkdir()
            source = input_dir / "e2e-300.mp4"
            source.write_bytes(b"RECOVERY-PUBLICATION")

            storage = Storage(root)
            db = Database(storage.database / "armoredcreator.db")
            published = set()
            crashing = PersistentPublisher(published, crash_after_publish=True)
            coordinator = Coordinator.build(
                root,
                Bindings(Source(source), Vision(), ArmoredStudio(root), crashing),
            )
            item_id = coordinator.ingest_once()

            with self.assertRaises(RuntimeError):
                coordinator.run(item_id)

            self.assertEqual(crashing.count, 1)
            self.assertEqual(db.get(item_id).state, State.FAILED)
            self.assertFalse(db.publication(item_id)["confirmed"])

            recovered = PersistentPublisher(published, crash_after_publish=False)
            coordinator.close()
            coordinator = Coordinator.build(
                root,
                Bindings(Source(source), Vision(), ArmoredStudio(root), recovered),
            )
            coordinator.recover(item_id)

            db = Database(storage.database / "armoredcreator.db")
            row = db.get(item_id)
            self.assertEqual(row.state, State.PUBLISHED)
            self.assertEqual(recovered.count, 0)
            self.assertTrue(row.original_path.exists())
            self.assertEqual([p.name for p in row.workspace.iterdir()], [row.original_path.name])
            self.assertTrue(db.publication(item_id)["confirmed"])

            coordinator.run(item_id)
            self.assertEqual(recovered.count, 0)
            coordinator.close()


if __name__ == "__main__":
    unittest.main()
