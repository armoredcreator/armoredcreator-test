from __future__ import annotations

import os
from pathlib import Path

from armored_core.models import Item
from armored_core.services import VisionResult

from .modules.v1.shopee_api import ShopeeAffiliateAPI
from .modules.v1.shopee_resolver import resolve_short_url


class ArmoredVision:
    """Affiliate identity stage.

    It owns Shopee resolution/API concerns only. Pipeline state remains in
    armored_core and no JSON state from the legacy Vision is used.
    """

    def __init__(self, api=None):
        self.api = api

    def identify(self, item: Item) -> VisionResult:
        original = (item.original_url or "").strip()
        if not original:
            raise RuntimeError("Vision: original Shopee URL ausente")

        resolved = resolve_short_url(original)
        api = self.api or ShopeeAffiliateAPI()
        product = api.get_exact_product(resolved.shop_id, resolved.item_id)

        affiliate_url = str(product.get("offerLink") or "").strip()
        if not affiliate_url:
            generated = api.generate_short_link(original)
            affiliate_url = str(generated["short_link"]).strip()

        identifier = str(
            product.get("productName")
            or product.get("itemId")
            or f"{resolved.shop_id}_{resolved.item_id}"
        )

        return VisionResult(identifier, affiliate_url)


def build(**_kwargs):
    return ArmoredVision()
