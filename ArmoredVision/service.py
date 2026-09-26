from __future__ import annotations

import os
from pathlib import Path

from armored_core.models import Item
from armored_core.services import VisionResult, VisionUnresolvedError

from .modules.v1.shopee_api import ShopeeAffiliateAPI, ShopeeProductNotFoundError
from .modules.v1.shopee_resolver import resolve_short_url
from .modules.v1.caption.generator import CaptionGenerator, CaptionGenerationError


class ArmoredVision:
    """Affiliate identity stage.

    It owns Shopee resolution/API concerns only. Pipeline state remains in
    armored_core and no JSON state from the legacy Vision is used.
    """

    def __init__(self, api=None, candidate_discovery=None, caption_generator=None):
        self.api = api
        # Kept only for constructor compatibility with the frozen V2 surface.
        # Vision V2 is intentionally not part of the active V1 pipeline.
        self.candidate_discovery = candidate_discovery
        self.caption_generator = caption_generator

    def identify(self, item: Item) -> VisionResult:
        original = (item.original_url or "").strip()
        if not original:
            raise RuntimeError("Vision: original Shopee URL ausente")

        resolved = resolve_short_url(original)
        api = self.api or ShopeeAffiliateAPI()
        try:
            product = api.get_exact_product(resolved.shop_id, resolved.item_id)
        except ShopeeProductNotFoundError as exc:
            raise VisionUnresolvedError(
                f"Vision V1 não resolveu o produto Shopee {resolved.shop_id}:{resolved.item_id}; "
                "item preservado para futura resolução"
            ) from exc

        affiliate_url = str(product.get("offerLink") or "").strip()
        if not affiliate_url:
            generated = api.generate_short_link(original)
            affiliate_url = str(generated["short_link"]).strip()

        identifier = str(
            product.get("productName")
            or product.get("itemId")
            or f"{resolved.shop_id}_{resolved.item_id}"
        )

        # V1 remains the sole authority for product identity and affiliate URL.
        # Vision V2 is frozen and is deliberately not invoked here.
        affiliate_urls = (affiliate_url,)
        candidate_records: tuple[dict, ...] = ()

        publication_caption = None
        if os.getenv("ARMORED_CAPTION_ENABLED", "0") == "1":
            try:
                generator = self.caption_generator or CaptionGenerator()
                publication_caption = generator.generate(product)
            except CaptionGenerationError as exc:
                raise VisionUnresolvedError(str(exc)) from exc
            except Exception as exc:
                raise VisionUnresolvedError(
                    f"Caption Generator indisponível: {type(exc).__name__}: {exc}"
                ) from exc

        return VisionResult(
            identifier,
            affiliate_url,
            affiliate_urls=tuple(affiliate_urls),
            publication_caption=publication_caption,
            candidate_records=tuple(candidate_records),
        )


def build(**_kwargs):
    return ArmoredVision()
