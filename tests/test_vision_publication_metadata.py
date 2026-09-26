from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from armored_core.database import Database


class VisionPublicationMetadataTests(unittest.TestCase):
    def test_database_persists_caption_and_affiliate_links(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            db = Database(root / "armoredcreator.db")
            try:
                item_id = db.create_item(
                    "123",
                    root / "123.mp4",
                    original_url="https://shopee.com.br/product/1/123",
                )

                db.set_vision(
                    item_id,
                    "Produto A",
                    "https://s.shopee.com.br/original",
                    affiliate_urls=(
                        "https://s.shopee.com.br/original",
                    ),
                    publication_caption="Olha esse charme ✨\n#casa",
                )

                item = db.get(item_id)

                self.assertEqual(
                    item.publication_caption,
                    "Olha esse charme ✨\n#casa",
                )
                self.assertEqual(
                    item.affiliate_urls,
                    ("https://s.shopee.com.br/original",),
                )
            finally:
                db.close()


if __name__ == "__main__":
    unittest.main()
