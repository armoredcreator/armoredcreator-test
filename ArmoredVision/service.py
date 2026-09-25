from __future__ import annotations

import os
from pathlib import Path

from armored_core.models import Item
from armored_core.services import VisionResult, VisionUnresolvedError

from .modules.v1.shopee_api import ShopeeAffiliateAPI, ShopeeProductNotFoundError
from .modules.v1.shopee_resolver import resolve_short_url
from .modules.v2.service import CandidateDiscovery, VisionCandidateError
from .modules.v1.caption.generator import CaptionGenerator, CaptionGenerationError


class ArmoredVision:
    """Affiliate identity stage.

    It owns Shopee resolution/API concerns only. Pipeline state remains in
    armored_core and no JSON state from the legacy Vision is used.
    """

    def __init__(self, api=None, candidate_discovery=None, caption_generator=None):
        self.api = api
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
                "item preservado para futura reconciliação Vision V2"
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

        affiliate_urls = (affiliate_url,)
        candidate_records: tuple[dict, ...] = ()
        if os.getenv("ARMORED_VISION_V2_ENABLED", "0") == "1":
            try:
                discovery = self.candidate_discovery or CandidateDiscovery()
                affiliate_urls, candidate_records = discovery.discover(
                    product,
                    original_affiliate_url=affiliate_url,
                    original_url=original,
                )
            except VisionCandidateError as exc:
                raise VisionUnresolvedError(str(exc)) from exc
            except Exception as exc:
                raise VisionUnresolvedError(
                    f"Vision V2 indisponível: {type(exc).__name__}: {exc}"
                ) from exc

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
