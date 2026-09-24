from __future__ import annotations

from ArmoredVision.modules.v2.normalize import quantity_facts
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


def test_quantity_normalization_treats_one_liter_and_1000ml_as_equal():
    assert quantity_facts("Pote 1L")["volume"] == quantity_facts("Pote 1000ml")["volume"]


def test_reconciler_accepts_same_product_with_reordered_name():
    reconciler = CandidateReconciler(image_scorer=lambda *_: 1.0)
    original = product(100, "Kit 3 Potes Herméticos de Vidro 1L", shop=1)
    candidate = product(200, "Potes Herméticos Vidro 1000ml Kit 3", shop=2)
    accepted, score, reason, evidence = reconciler.compare(original, candidate)
    assert accepted is True
    assert score > 0.5
    assert evidence["attribute_conflicts"] == []


def test_reconciler_rejects_explicit_quantity_conflict():
    reconciler = CandidateReconciler(image_scorer=lambda *_: 1.0)
    original = product(100, "Kit 3 Potes Herméticos de Vidro 1L", shop=1)
    candidate = product(200, "Kit 2 Potes Herméticos de Vidro 1L", shop=2)
    accepted, _, reason, evidence = reconciler.compare(original, candidate)
    assert accepted is False
    assert "quantity" in evidence["attribute_conflicts"]
    assert "incompatível" in reason


class FakeAPI:
    def __init__(self):
        self.search_calls = []
        self.revalidate_calls = []

    def get_exact_product(self, shop_id, item_id):
        return product(int(item_id), "Kit 3 Potes Herméticos de Vidro 1L", int(shop_id))

    def search_products(self, keyword, *, page=1, limit=20, sort_type=1):
        self.search_calls.append(keyword)
        rows = [
            product(100, "Kit 3 Potes Herméticos de Vidro 1L", 1),
            *[product(1000 + i, "Kit 3 Potes Herméticos de Vidro 1L", 20 + i) for i in range(9)],
            product(2000, "Kit 2 Potes Herméticos de Vidro 1L", 40),
            product(2001, "Capa para Celular Transparente", 41),
            product(2002, "Kit 3 Potes Herméticos de Vidro 1L", 42),
        ]
        return rows

    def generate_short_link(self, origin_url):
        return origin_url + "?affiliate=1"


def test_candidate_discovery_keeps_original_and_caps_at_six_accepted(monkeypatch):
    monkeypatch.setenv("ARMORED_VISION_V2_TARGET_CANDIDATES", "12")
    monkeypatch.setenv("ARMORED_VISION_V2_MIN_ACCEPTED", "2")
    monkeypatch.setenv("ARMORED_VISION_V2_MAX_ACCEPTED", "6")
    api = FakeAPI()
    discovery = CandidateDiscovery(api=api, reconciler=CandidateReconciler(image_scorer=lambda *_: 1.0))

    links, records = discovery.discover(
        product(100, "Kit 3 Potes Herméticos de Vidro 1L", 1),
        original_affiliate_url="https://s.shopee.com.br/original",
        original_url="https://shopee.com.br/product/1/100",
    )

    assert len(links) == 7
    assert len(records) >= 10
    assert records[0]["candidate_order"] == 0
    assert records[0]["decision"] == "ORIGINAL"
    accepted = [r for r in records if r["decision"] == "ACCEPTED"]
    assert len(accepted) == 6
    assert [r["candidate_order"] for r in accepted] == list(range(1, 7))
    assert len(set(links)) == len(links)


def test_candidate_discovery_requires_minimum_proven_candidates(monkeypatch):
    monkeypatch.setenv("ARMORED_VISION_V2_TARGET_CANDIDATES", "10")
    monkeypatch.setenv("ARMORED_VISION_V2_MIN_ACCEPTED", "2")
    api = FakeAPI()

    class RejectingReconciler:
        def compare(self, *_):
            return False, 0.1, "rejeitado", {}

    discovery = CandidateDiscovery(api=api, reconciler=RejectingReconciler())
    try:
        discovery.discover(
            product(100, "Kit 3 Potes Herméticos de Vidro 1L", 1),
            original_affiliate_url="https://s.shopee.com.br/original",
            original_url="https://shopee.com.br/product/1/100",
        )
    except RuntimeError as exc:
        assert "mínimo=2" in str(exc)
    else:
        raise AssertionError("V2 deveria exigir o mínimo de candidatos")
