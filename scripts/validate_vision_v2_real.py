from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from dotenv import load_dotenv

from ArmoredVision.modules.v1.shopee_api import ShopeeAffiliateAPI
from ArmoredVision.modules.v1.shopee_resolver import resolve_short_url
from ArmoredVision.modules.v2.service import CandidateDiscovery, VisionCandidateError


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
        "--json",
        action="store_true",
        dest="as_json",
        help="Imprime somente JSON.",
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

    affiliate_url = str(product.get("offerLink") or "").strip()
    if not affiliate_url:
        affiliate_url = str(api.generate_short_link(original_url)["short_link"]).strip()

    try:
        links, records = CandidateDiscovery().discover(
            product,
            original_affiliate_url=affiliate_url,
            original_url=original_url,
        )
    except VisionCandidateError as exc:
        payload = {
            "status": "WAITING_VISION",
            "reason": str(exc),
            "original": {
                "shop_id": resolved.shop_id,
                "item_id": resolved.item_id,
                "product_name": product.get("productName"),
                "product_link": product.get("productLink"),
            },
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 2

    accepted = [record for record in records if record.get("decision") == "ACCEPTED"]
    rejected = [record for record in records if record.get("decision") == "REJECTED"]

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
        "accepted": accepted,
        "rejected": rejected,
    }

    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
