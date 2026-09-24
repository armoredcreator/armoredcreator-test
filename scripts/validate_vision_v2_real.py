from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from dotenv import load_dotenv

from ArmoredVision.modules.v1.shopee_api import ShopeeAffiliateAPI
from ArmoredVision.modules.v1.shopee_resolver import resolve_short_url
from ArmoredVision.modules.v2.service import CandidateDiscovery, VisionCandidateError
from ArmoredVision.modules.caption.generator import CaptionGenerator, CaptionGenerationError


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Audita a Vision V2 contra um produto Shopee real sem alterar o SQLite."
    )
    parser.add_argument(
        "--url",
        default=os.getenv("ARMORED_VISION_V2_REAL_URL", ""),
        help="URL Shopee original; também pode vir de ARMORED_VISION_V2_REAL_URL",
    )
    parser.add_argument(
        "--caption",
        action="store_true",
        help="Também gera e valida a legenda para o produto original.",
    )
    parser.add_argument(
        "--require-gemini",
        action="store_true",
        help="Com --caption, falha se Gemini não estiver configurado ou não responder.",
    )
    args = parser.parse_args()

    root = Path(os.getenv("ARMORED_ROOT", ".")).resolve()
    for candidate in (
        root / ".env",
        root / "credentials" / "shopee" / "affiliate.env",
    ):
        if candidate.exists():
            load_dotenv(candidate, override=False)

    original_url = str(args.url or "").strip()
    if not original_url:
        parser.error("informe --url ou ARMORED_VISION_V2_REAL_URL")

    api = ShopeeAffiliateAPI()
    resolved = resolve_short_url(original_url)
    product = api.get_exact_product(resolved.shop_id, resolved.item_id)

    affiliate_url = api.affiliate_link_for_product(product)

    try:
        links, records = CandidateDiscovery().discover(
            product,
            original_affiliate_url=affiliate_url,
            original_url=original_url,
        )
    except VisionCandidateError as exc:
        records = list(getattr(exc, "records", ()) or ())
        payload = {
            "status": "WAITING_VISION",
            "reason": str(exc),
            "original": {
                "shop_id": resolved.shop_id,
                "item_id": resolved.item_id,
                "product_name": product.get("productName"),
                "product_link": product.get("productLink"),
                "affiliate_url": affiliate_url,
            },
            "diagnostics": {
                "discovered": getattr(exc, "discovered_count", len(records)),
                "accepted": getattr(exc, "accepted_count", 0),
                "minimum_required": getattr(exc, "minimum_required", None),
                "candidates": [
                    {
                        "order": r.get("candidate_order"),
                        "shop_id": r.get("shop_id"),
                        "item_id": r.get("item_id"),
                        "product_name": r.get("product_name"),
                        "decision": r.get("decision"),
                        "reason": r.get("reason"),
                        "score": r.get("score"),
                        "evidence": r.get("evidence"),
                    }
                    for r in records[1:]
                ],
            },
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 2

    accepted = [record for record in records if record.get("decision") == "ACCEPTED"]
    rejected = [record for record in records if record.get("decision") == "REJECTED"]

    caption = None
    caption_error = None
    if args.caption:
        if args.require_gemini:
            os.environ["ARMORED_CAPTION_ALLOW_DETERMINISTIC_FALLBACK"] = "0"
        os.environ["ARMORED_CAPTION_ENABLED"] = "1"
        try:
            caption = CaptionGenerator().generate(product)
        except CaptionGenerationError as exc:
            caption_error = str(exc)
            if args.require_gemini:
                payload = {
                    "status": "CAPTION_ERROR",
                    "reason": caption_error,
                    "original": {
                        "shop_id": resolved.shop_id,
                        "item_id": resolved.item_id,
                        "product_name": product.get("productName"),
                    },
                }
                print(json.dumps(payload, ensure_ascii=False, indent=2))
                return 3

    payload = {
        "status": "RESOLVED",
        "original": {
            "shop_id": resolved.shop_id,
            "item_id": resolved.item_id,
            "product_name": product.get("productName"),
            "product_link": product.get("productLink"),
            "affiliate_url": affiliate_url,
        },
        "discovery": {
            "target_additional": max(
                10,
                min(15, int(os.getenv("ARMORED_VISION_V2_TARGET_CANDIDATES", "12"))),
            ),
            "accepted_additional": len(accepted),
            "published_links_total": len(links),
            "rejected": len(rejected),
        },
        "links": list(links),
        "caption": caption,
        "caption_error": caption_error,
        "accepted": accepted,
        "rejected": rejected,
    }

    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
