from __future__ import annotations

import argparse
import json
import os
import hashlib
import time
from typing import Any

import requests

from ArmoredVision.modules.v2.shopee_search import ShopeeCandidateAPI


EXACT_IDENTITY_QUERY = """
query ExactIdentity($shopId: Int64!, $itemId: Int64!) {
  productOfferV2(shopId: $shopId, itemId: $itemId, page: 1, limit: 1) {
    nodes {
      itemId
      shopId
      productName
      productLink
      imageUrl
      productCatIds
      priceMin
      priceMax
      ratingStar
      commission
      sales
      offerLink
    }
  }
}
"""

# Experimental probes only. These fields are intentionally NOT wired into V2.
# The objective is to learn what the connected affiliate GraphQL schema exposes.
RICH_IDENTITY_QUERY = """
query RichIdentity($shopId: Int64!, $itemId: Int64!) {
  productOfferV2(shopId: $shopId, itemId: $itemId, page: 1, limit: 1) {
    nodes {
      itemId
      shopId
      productName
      productLink
      imageUrl
      productCatIds
      priceMin
      priceMax
      ratingStar
      commission
      sales
      offerLink
      brand
      gtin
      description
      dimensions
      models
      attributes
      skus
    }
  }
}
"""


def signed_post(query: str, variables: dict[str, Any]) -> dict[str, Any]:
    app_id = os.getenv("SHOPEE_APP_ID")
    secret = os.getenv("SHOPEE_SECRET_KEY")
    if not app_id or not secret:
        raise RuntimeError("SHOPEE_APP_ID/SHOPEE_SECRET_KEY não configurados")

    payload = json.dumps(
        {"query": query, "variables": variables},
        ensure_ascii=False,
        separators=(",", ":"),
    )
    ts = int(time.time())
    signature = hashlib.sha256(
        f"{app_id}{ts}{payload}{secret}".encode()
    ).hexdigest()

    response = requests.post(
        os.getenv(
            "SHOPEE_AFFILIATE_API_URL",
            "https://open-api.affiliate.shopee.com.br/graphql",
        ),
        data=payload.encode(),
        headers={
            "Content-Type": "application/json",
            "Authorization": (
                f"SHA256 Credential={app_id},Timestamp={ts},Signature={signature}"
            ),
        },
        timeout=(
            float(os.getenv("SHOPEE_API_CONNECT_TIMEOUT", "10")),
            float(os.getenv("SHOPEE_API_READ_TIMEOUT", "20")),
        ),
    )
    response.raise_for_status()
    return response.json()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--shop-id", required=True)
    parser.add_argument("--item-id", required=True)
    args = parser.parse_args()

    # First prove the exact identity lookup using the query already supported by V2.
    api = ShopeeCandidateAPI()
    base = api.get_exact_product(args.shop_id, args.item_id)

    print("[EXACT-ID] produto encontrado via productOfferV2", flush=True)
    print(json.dumps(base, ensure_ascii=False, indent=2), flush=True)

    print("[EXACT-ID] sondando campos ricos do schema...", flush=True)
    try:
        rich = signed_post(
            RICH_IDENTITY_QUERY,
            {"shopId": str(args.shop_id), "itemId": str(args.item_id)},
        )
    except Exception as exc:
        print(
            json.dumps(
                {
                    "status": "RICH_QUERY_TRANSPORT_ERROR",
                    "error": str(exc),
                },
                ensure_ascii=False,
                indent=2,
            ),
            flush=True,
        )
        return 2

    errors = rich.get("errors") or []
    data = rich.get("data") or {}

    if errors:
        print(
            json.dumps(
                {
                    "status": "RICH_FIELDS_NOT_EXPOSED_OR_REJECTED",
                    "errors": errors,
                    "base_identity_available": True,
                    "base_fields": sorted(base.keys()),
                },
                ensure_ascii=False,
                indent=2,
            ),
            flush=True,
        )
        return 0

    print(
        json.dumps(
            {
                "status": "RICH_FIELDS_AVAILABLE",
                "data": data,
                "base_fields": sorted(base.keys()),
            },
            ensure_ascii=False,
            indent=2,
        ),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
