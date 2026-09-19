import tempfile
import unittest
from pathlib import Path

from armored_core.models import Item, PublicationCheck
from armored_core.services import PublicationResult, StudioResult
from ArmoredHub.service import ArmoredHub
from ArmoredStudio.service import ArmoredStudio


class AdapterContractTests(unittest.TestCase):
    def test_studio_returns_canonical_result(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            w, r = root / "work.mp4", root / "result.mp4"
            adapter = ArmoredStudio(lambda item: (w, r))
            result = adapter.process(None)
            self.assertIsInstance(result, StudioResult)
            self.assertEqual((result.working_path, result.result_path), (w, r))

    def test_hub_uses_tri_state_contract(self):
        class Publisher:
            def check_publication(self, item):
                return PublicationCheck.UNKNOWN
            def publish(self, item):
                return PublicationResult(True, "message")

        hub = ArmoredHub(Publisher())
        self.assertEqual(hub.check_publication(None), PublicationCheck.UNKNOWN)


if __name__ == "__main__":
    unittest.main()
