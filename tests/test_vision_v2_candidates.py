from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from ArmoredVision.modules.v2.normalize import quantity_facts, structural_facts
from ArmoredVision.modules.v2.reconcile import CandidateReconciler
from ArmoredVision.modules.v2.service import CandidateDiscovery


def product(item: int, name: str, shop: int = 10) -> dict:
    return {
        "itemId": item,
        "shopId": shop,
        "productName": name,
        "shopName": f"Loja {shop}",
        "productLink": f"https://shopee.com.br/product/{shop}/{item}",
        "offerLink": f"https://s.shopee.com.br/{item}",
        "imageUrl": "https://img.test/shared.jpg",
        "productCatIds": [10, 20],
        "priceMin": "49.90",
        "priceMax": "59.90",
    }


class FakeAPI:
    def __init__(self):
        self.search_calls = []

    def get_exact_product(self, shop_id, item_id):
        return product(int(item_id), "Kit 3 Potes Herméticos de Vidro 1L", int(shop_id))

    def search_products(self, keyword, *, page=1, limit=20, sort_type=1):
        self.search_calls.append(keyword)
        return [
            *[
                product(1000 + i, "Kit 3 Potes Herméticos de Vidro 1L", 20 + i)
                for i in range(9)
            ],
            product(2000, "Kit 2 Potes Herméticos de Vidro 1L", 40),
            product(2001, "Capa para Celular Transparente", 41),
            product(2002, "Kit 3 Potes Herméticos de Vidro 1L", 42),
        ]

    def generate_short_link(self, origin_url):
        return origin_url + "?affiliate=1"

    def affiliate_link_for_product(self, product):
        offer = str(product.get("offerLink") or "").strip()
        if offer:
            return offer
        return self.generate_short_link(str(product.get("productLink") or ""))


class VisionV2CandidateTests(unittest.TestCase):
    def tearDown(self):
        for key in (
            "ARMORED_VISION_V2_TARGET_CANDIDATES",
            "ARMORED_VISION_V2_MIN_ACCEPTED",
            "ARMORED_VISION_V2_MAX_ACCEPTED",
        ):
            os.environ.pop(key, None)

    def test_quantity_normalization(self):
        self.assertEqual(
            quantity_facts("Pote 1L")["volume"],
            quantity_facts("Pote 1000ml")["volume"],
        )
        self.assertEqual(
            quantity_facts("Kit 3 Potes")["quantity"],
            quantity_facts("Conjunto 3 Unidades")["quantity"],
        )
        self.assertNotEqual(
            quantity_facts("Kit 3 Potes")["quantity"],
            quantity_facts("Kit 2 Potes")["quantity"],
        )

    def test_reconciler_accepts_same_product_with_reordered_name(self):
        reconciler = CandidateReconciler(image_scorer=lambda *_: 1.0)
        original = product(100, "Kit 3 Potes Herméticos de Vidro 1L", shop=1)
        candidate = product(200, "Potes Herméticos Vidro 1000ml Kit 3", shop=2)
        accepted, score, _, evidence = reconciler.compare(original, candidate)
        self.assertTrue(accepted)
        self.assertGreater(score, 0.5)
        self.assertEqual(evidence["attribute_conflicts"], [])

    def test_structural_facts_capture_identity_features(self):
        facts = structural_facts(
            "Bancada Suspensa Barbearia 90cm Com 1 Porta e 1 Gaveta Nicho Ripado"
        )
        self.assertEqual(facts["size_cm"], 90.0)
        self.assertEqual(facts["doors"], 1)
        self.assertEqual(facts["drawers"], 1)
        self.assertTrue(facts["niches"])
        self.assertTrue(facts["ripado"])

    def test_reconciler_rejects_structural_door_drawer_conflict(self):
        reconciler = CandidateReconciler(image_scorer=lambda *_: 0.99)
        original = product(
            100, "Bancada Suspensa Barbearia 90cm Com Gaveta", shop=1
        )
        candidate = product(
            200, "Bancada Suspensa Barbearia 90cm Com 2 Portas", shop=2
        )
        accepted, _, reason, evidence = reconciler.compare(original, candidate)
        self.assertFalse(accepted)
        self.assertIn("doors", evidence["structural_conflicts"])
        self.assertIn("drawers", evidence["structural_conflicts"])
        self.assertIn("incompatível", reason)

    def test_reconciler_accepts_moderate_name_when_multiple_strong_signals_agree(self):
        reconciler = CandidateReconciler(image_scorer=lambda *_: 0.75)
        original = product(
            100, "Bancada Suspensa Barbearia 90cm Com 1 Porta e Nicho", shop=1
        )
        candidate = product(
            200, "Bancada Suspensa 90cm Barbearia Com 1 Porta e Nicho", shop=2
        )
        accepted, score, reason, evidence = reconciler.compare(original, candidate)
        self.assertTrue(accepted)
        self.assertGreater(score, 0.5)
        self.assertIn("estrutura forte", reason)
        self.assertGreaterEqual(len(evidence["structural_matches"]), 2)

    def test_reconciler_rejects_explicit_quantity_conflict(self):
        reconciler = CandidateReconciler(image_scorer=lambda *_: 1.0)
        original = product(100, "Kit 3 Potes Herméticos de Vidro 1L", shop=1)
        candidate = product(200, "Kit 2 Potes Herméticos de Vidro 1L", shop=2)
        accepted, _, reason, evidence = reconciler.compare(original, candidate)
        self.assertFalse(accepted)
        self.assertIn("quantity", evidence["attribute_conflicts"])
        self.assertIn("incompatível", reason)

    def test_discovery_explores_multiple_query_families_before_capping(self):
        os.environ["ARMORED_VISION_V2_TARGET_CANDIDATES"] = "12"
        os.environ["ARMORED_VISION_V2_MIN_ACCEPTED"] = "2"
        api = FakeAPI()
        discovery = CandidateDiscovery(
            api=api,
            reconciler=CandidateReconciler(image_scorer=lambda *_: 1.0),
        )
        discovery.discover(
            product(100, "Bancada Suspensa Barbearia 90cm Com Gaveta", 1),
            original_affiliate_url="https://s.shopee.com.br/original",
            original_url="https://shopee.com.br/product/1/100",
        )
        queries = [str(call).strip().casefold() for call in api.search_calls]
        self.assertGreaterEqual(len(set(queries)), 4)

    def test_discovery_keeps_original_and_caps_at_six_accepted(self):
        os.environ["ARMORED_VISION_V2_TARGET_CANDIDATES"] = "12"
        os.environ["ARMORED_VISION_V2_MIN_ACCEPTED"] = "2"
        os.environ["ARMORED_VISION_V2_MAX_ACCEPTED"] = "6"

        api = FakeAPI()
        discovery = CandidateDiscovery(
            api=api,
            reconciler=CandidateReconciler(image_scorer=lambda *_: 1.0),
        )

        links, records = discovery.discover(
            product(100, "Kit 3 Potes Herméticos de Vidro 1L", 1),
            original_affiliate_url="https://s.shopee.com.br/original",
            original_url="https://shopee.com.br/product/1/100",
        )

        self.assertEqual(len(links), 7)
        self.assertGreaterEqual(len(records), 10)
        self.assertEqual(records[0]["candidate_order"], 0)
        self.assertEqual(records[0]["decision"], "ORIGINAL")
        accepted = [r for r in records if r["decision"] == "ACCEPTED"]
        self.assertEqual(len(accepted), 6)
        self.assertEqual(
            [r["candidate_order"] for r in accepted],
            list(range(1, 7)),
        )
        self.assertEqual(len(set(links)), len(links))

    def test_discovery_requires_minimum_proven_candidates(self):
        os.environ["ARMORED_VISION_V2_TARGET_CANDIDATES"] = "10"
        os.environ["ARMORED_VISION_V2_MIN_ACCEPTED"] = "2"

        class RejectingReconciler:
            def compare(self, *_):
                return False, 0.1, "rejeitado", {}

        discovery = CandidateDiscovery(
            api=FakeAPI(),
            reconciler=RejectingReconciler(),
        )

        with self.assertRaisesRegex(RuntimeError, "mínimo=2"):
            discovery.discover(
                product(100, "Kit 3 Potes Herméticos de Vidro 1L", 1),
                original_affiliate_url="https://s.shopee.com.br/original",
                original_url="https://shopee.com.br/product/1/100",
            )


if __name__ == "__main__":
    unittest.main()
