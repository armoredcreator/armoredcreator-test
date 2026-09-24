from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from armored_core.database import Database


class VisionPublicationMetadataTests(unittest.TestCase):
    def test_database_persists_caption_links_and_candidate_audit(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            db = Database(root / "armoredcreator.db")
            try:
                item_id = db.create_item(
                    "123",
                    root / "123.mp4",
                    original_url="https://shopee.com.br/product/1/123",
                )
                records = (
                    {
                        "candidate_order": 0,
                        "source_type": "original",
                        "source_url": "https://shopee.com.br/product/1/123",
                        "product_link": "https://shopee.com.br/product/1/123",
                        "affiliate_url": "https://s.shopee.com.br/original",
                        "shop_id": "1",
                        "item_id": "123",
                        "product_name": "Produto A",
                        "shop_name": "Loja A",
                        "image_url": "",
                        "category_ids": ["1"],
                        "price_min": 10,
                        "price_max": 12,
                        "score": 1,
                        "decision": "ORIGINAL",
                        "reason": "V1",
                        "evidence": {"source": "V1_EXACT"},
                    },
                    {
                        "candidate_order": 1,
                        "source_type": "discovered",
                        "source_url": "https://shopee.com.br/product/2/999",
                        "product_link": "https://shopee.com.br/product/2/999",
                        "affiliate_url": "https://s.shopee.com.br/c1",
                        "shop_id": "2",
                        "item_id": "999",
                        "product_name": "Produto A",
                        "shop_name": "Loja B",
                        "image_url": "",
                        "category_ids": ["1"],
                        "price_min": 11,
                        "price_max": 13,
                        "score": 0.91,
                        "decision": "ACCEPTED",
                        "reason": "evidências",
                        "evidence": {"name_score": 0.91},
                    },
                )
                db.set_vision(
                    item_id,
                    "Produto A",
                    "https://s.shopee.com.br/original",
                    affiliate_urls=(
                        "https://s.shopee.com.br/original",
                        "https://s.shopee.com.br/c1",
                    ),
                    publication_caption="Olha esse charme ✨\n#casa",
                    candidate_records=records,
                )
                item = db.get(item_id)

                self.assertEqual(item.publication_caption, "Olha esse charme ✨\n#casa")
                self.assertEqual(
                    item.affiliate_urls,
                    (
                        "https://s.shopee.com.br/original",
                        "https://s.shopee.com.br/c1",
                    ),
                )
                candidates = db.vision_candidates(item_id)
                self.assertEqual(len(candidates), 2)
                self.assertEqual(candidates[0]["decision"], "ORIGINAL")
                self.assertEqual(candidates[1]["decision"], "ACCEPTED")
            finally:
                db.close()


if __name__ == "__main__":
    unittest.main()
