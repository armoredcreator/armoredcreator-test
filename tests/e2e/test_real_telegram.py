from __future__ import annotations

import asyncio
import os
import unittest
from pathlib import Path

from armored_core.coordinator import Coordinator
from armored_core.models import State


class RealTelegramE2ETests(unittest.TestCase):
    """Opt-in real Telegram -> Vision -> Studio -> Hub -> Telegram test.

    This test is intentionally never enabled by the normal pytest suite.
    Enable it explicitly with ARMORED_REAL_TELEGRAM_E2E=1.
    """

    def test_real_telegram_full_pipeline(self):
        if os.getenv("ARMORED_REAL_TELEGRAM_E2E", "0") != "1":
            self.skipTest("ARMORED_REAL_TELEGRAM_E2E=1 required")

        previous = {name: os.environ.get(name) for name in ("ARMORED_REAL_TELEGRAM", "ARMORED_HUB_DRY_RUN", "ARMORED_STUDIO_FORCE_COPY")}
        os.environ["ARMORED_REAL_TELEGRAM"] = "1"
        os.environ["ARMORED_HUB_DRY_RUN"] = "0"
        root = Path.cwd()

        # Coordinator.build() is the canonical credential loader. Validate
        # after it loads credentials from .env / credentials/telegram / credentials/shopee.
        coordinator = Coordinator.build(root)

        required = (
            "TELEGRAM_API_ID",
            "TELEGRAM_API_HASH",
            "ARMORED_CREATOR_BOT_TOKEN",
        )
        missing = [name for name in required if not os.getenv(name)]
        if missing:
            coordinator.close()
            self.fail("Missing real-E2E environment variables: " + ", ".join(missing))

        # Backup contract: publication target is Telegram forum topic 228.
        # The parent group ID may be discovered automatically from the existing
        # ArmoredSync user session; it is not required in configuration.
        topic_id = os.getenv("ARMORED_HUB_TOPIC_ID", "228").strip()
        self.assertEqual(topic_id, "228", "Backup contract requires Telegram topic 228")

        source = coordinator.source
        item_id = None

        try:
            # Real Sync: discover one eligible Telegram video and materialize
            # it directly into storage/videos/{telegram_message_id}/.
            item_id = asyncio.run(coordinator.ingest_once_async())
            self.assertIsNotNone(item_id, "Telegram source returned no eligible video")

            # Real Vision + real Studio + real Hub publication.
            coordinator.run(str(item_id))
            item = coordinator.db.get(str(item_id))

            self.assertEqual(item.state, State.PUBLISHED)
            self.assertTrue(item.original_path.is_file())
            self.assertTrue(item.cleanup_completed)

            publication = coordinator.db.publication(str(item_id))
            self.assertIsNotNone(publication)
            self.assertEqual(int(publication["confirmed"]), 1)
            self.assertTrue(str(publication["published_message_id"]).isdigit())

            # Re-running the same canonical content ID must not publish twice.
            before = publication["published_message_id"]
            coordinator.run(str(item_id))
            after = coordinator.db.publication(str(item_id))["published_message_id"]
            self.assertEqual(after, before)

            # Cleanup keeps exactly the immutable original.
            files = [path.name for path in item.workspace.iterdir()]
            self.assertEqual(files, [item.original_path.name])
        finally:
            reader = getattr(source, "reader", None)
            if reader is not None:
                asyncio.run(reader.disconnect())
            coordinator.close()
            for name, value in previous.items():
                if value is None:
                    os.environ.pop(name, None)
                else:
                    os.environ[name] = value


if __name__ == "__main__":
    unittest.main()
