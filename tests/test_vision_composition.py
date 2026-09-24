from __future__ import annotations

from ArmoredVision.service import ArmoredVision


PRODUCT = {
    "itemId": 123,
    "shopId": 456,
    "productName": "Produto Exemplo 1L",
    "shopName": "Loja Exemplo",
    "productLink": "https://shopee.com.br/product/456/123",
    "offerLink": "https://s.shopee.com.br/original",
    "imageUrl": "",
}


class V1FakeAPI:
    def __init__(self):
        self.exact_calls = []

    def get_exact_product(self, shop_id, item_id):
        self.exact_calls.append((shop_id, item_id))
        return PRODUCT.copy()

    def generate_short_link(self, origin):
        return "https://s.shopee.com.br/generated"


class FakeV2:
    def discover(self, product, *, original_affiliate_url, original_url):
        assert product["itemId"] == 123
        return (
            original_affiliate_url,
            "https://s.shopee.com.br/c1",
            "https://s.shopee.com.br/c2",
        ), (
            {"candidate_order": 0, "decision": "ORIGINAL"},
            {"candidate_order": 1, "decision": "ACCEPTED"},
        )


class FakeCaption:
    def generate(self, product):
        assert product["productName"] == "Produto Exemplo 1L"
        return "Olha esse charme ✨\n#casa"


def test_v1_contract_remains_usable_when_new_layers_are_disabled(monkeypatch):
    monkeypatch.delenv("ARMORED_VISION_V2_ENABLED", raising=False)
    monkeypatch.delenv("ARMORED_CAPTION_ENABLED", raising=False)
    api = V1FakeAPI()
    vision = ArmoredVision(api=api)

    result = vision.identify(
        type("ItemStub", (), {
            "original_url": "https://shopee.com.br/product/456/123",
        })()
    )

    assert result.affiliate_name == "Produto Exemplo 1L"
    assert result.affiliate_url == "https://s.shopee.com.br/original"
    assert result.affiliate_urls == ("https://s.shopee.com.br/original",)
    assert result.publication_caption is None
    assert api.exact_calls == [("456", "123")]


def test_v2_and_caption_are_composed_after_v1_without_changing_v1(monkeypatch):
    monkeypatch.setenv("ARMORED_VISION_V2_ENABLED", "1")
    monkeypatch.setenv("ARMORED_CAPTION_ENABLED", "1")
    api = V1FakeAPI()
    vision = ArmoredVision(
        api=api,
        candidate_discovery=FakeV2(),
        caption_generator=FakeCaption(),
    )

    result = vision.identify(
        type("ItemStub", (), {
            "original_url": "https://shopee.com.br/product/456/123",
        })()
    )

    assert result.affiliate_url == "https://s.shopee.com.br/original"
    assert result.affiliate_urls == (
        "https://s.shopee.com.br/original",
        "https://s.shopee.com.br/c1",
        "https://s.shopee.com.br/c2",
    )
    assert result.publication_caption == "Olha esse charme ✨\n#casa"
    assert result.candidate_records[0]["decision"] == "ORIGINAL"
    assert api.exact_calls == [("456", "123")]
