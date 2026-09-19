from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from armored_core.models import Item, State
from armored_core.telegram_publisher import TelegramPublisher


class TelegramPublisherTests(unittest.TestCase):
    def item(self, item_id: int = 41) -> Item:
        return Item(
            item_id=item_id,
            telegram_message_id="tg-test",
            state=State.PUBLISHING,
            workspace=Path("."),
            original_path=None,
            working_path=None,
            result_path=None,
            affiliate_name=None,
            affiliate_url=None,
        )

    def test_marker_is_deterministic(self):
        self.assertEqual(TelegramPublisher.marker(self.item(41)), "ARMOREDCREATOR_ITEM:41")
        self.assertEqual(TelegramPublisher.marker(self.item(41)), TelegramPublisher.marker(self.item(41)))

    def test_missing_runtime_config_is_explicit(self):
        names = ["TELEGRAM_PUBLISH_TARGET", "TELEGRAM_API_ID", "TELEGRAM_API_HASH"]
        old = {name: os.environ.get(name) for name in names}
        try:
            for name in names:
                os.environ.pop(name, None)
            with self.assertRaisesRegex(RuntimeError, "missing-telegram-config"):
                TelegramPublisher()
        finally:
            for name, value in old.items():
                if value is None:
                    os.environ.pop(name, None)
                else:
                    os.environ[name] = value

    def test_session_path_is_relative_and_portable_by_default(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["TELEGRAM_PUBLISH_TARGET"] = "example"
            os.environ["TELEGRAM_API_ID"] = "123"
            os.environ["TELEGRAM_API_HASH"] = "hash"
            os.environ.pop("TELEGRAM_SESSION_PATH", None)
            publisher = TelegramPublisher()
            self.assertFalse(publisher.session_path.is_absolute())
            self.assertEqual(publisher.session_path.as_posix(), "storage/telegram/session")


if __name__ == "__main__":
    unittest.main()
