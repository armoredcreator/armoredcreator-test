"""
ArmoredVision V2 — source validation laboratory.

This script intentionally tests ONE external discovery source at a time.
It does not use our discovery logic, known benchmark IDs as input, CLIP,
SIFT, dHash, or the V2 reconciler.

Sources currently supported:
  - product_offers: Shopee Affiliate Open API productOfferV2.

Environment for product_offers:
  SHOPEE_APP_ID
  SHOPEE_SECRET_KEY
  optional SHOPEE_AFFILIATE_API_URL

Usage:
  python scripts/validate_vision_v2_source_lab.py --source product_offers

The result is saved under storage/vision_v2_source_lab/<source>.json.
Known IDs may be configured only as post-discovery benchmark labels and are
never sent to the discovery source.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any

import requests


ORIGINAL_DEFAULT = "https://s.shopee.com.br/8KolJcZrfU"
ORIGINAL_NAME_DEFAULT = "Bancada Suspensa Barbearia Cabeleireiro 90cm Com Gaveta"
RESULT_ROOT = Path("storage/vision_v2_source_lab")

# Benchmark-only labels. Never included in requests.
KNOWN_BENCHMARK_IDS = {
    "1609734117:22794532266",
    "329536801:28939276497",
    "335209020:29176620632",
    "382998202:23198215253",
}

SOURCES = ("product_offers",)


def parse_product_ids(value: str) -> tuple[str, str] | None:
    patterns = (
        r"/product/(\d+)/(\d+)",
        r"\.i\.(\d+)\.(\d+)",
    )
    for pattern in patterns:
        m = re.search(pattern, value)
        if m:
            return m.group(1), m.group(2)
    return None


def resolve_original(url: str) -> tuple[str, str]:
    ids = parse_product_ids(url)
    if ids:
        return ids

    r = requests.get(
        url,
        allow_redirects=True,
        timeout=(10, 20),
        headers={"User-Agent": "ArmoredVisionSourceLab/1.0"},
    )
    r.raise_for_status()
    ids = parse_product_ids(r.url)
    if ids:
        return ids

    m = re.search(r"https?://(?:www\.)?shopee\.com\.br/product/(\d+)/(\d+)", r.text)
    if m:
        return m.group(1), m.group(2)
    m = re.search(r"shopee\.com\.br/[^\\"' ]+\.i\.(\d+)\.(\d+)", r.text)
    if m:
        return m.group(1), m.group(2)

    raise RuntimeError(f"Não foi possível resolver shop_id/item_id: {url} -> {r.url}")


def request_product_offers(keyword: str, *, page: int, limit: int) -> dict[str, Any]:
    app_id = os.getenv("SHOPEE_APP_ID")
    secret = os.getenv("SHOPEE_SECRET_KEY")
    endpoint = os.getenv(
        "SHOPEE_AFFILIATE_API_URL",
        "https://open-api.affiliate.shopee.com.br/graphql",
    )

    if not app_id:
        raise RuntimeError("Defina SHOPEE_APP_ID.")
    if not secret:
        raise RuntimeError("Defina SHOPEE_SECRET_KEY.")

    # Keep this query compact and deterministic. The signature covers the
    # exact JSON payload sent over the wire.
    query = (
        "query ProductOfferV2($keyword: String, $sortType: Int, "
        "$page: Int, $limit: Int) { "
        "productOfferV2(keyword: $keyword, sortType: $sortType, "
        "page: $page, limit: $limit) { "
        "nodes { productId productName productLink offerLink imageUrl "
        "price priceMin priceMax commissionRate shopId shopName "
        "soldCount ratingStar productCatIds } "
        "pageInfo { page limit hasNextPage } } }"
    )
    payload_obj = {
        "query": query,
        "operationName": "ProductOfferV2",
        "variables": {
            "keyword": keyword,
            "sortType": 1,
            "page": page,
            "limit": limit,
        },
    }
    payload = json.dumps(payload_obj, ensure_ascii=False, separators=(",", ":"))

    timestamp = str(int(time.time()))
    signature_input = f"{app_id}{timestamp}{payload}{secret}"
    signature = hashlib.sha256(signature_input.encode("utf-8")).hexdigest()

    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "Authorization": (
            f"SHA256 Credential={app_id}, "
            f"Timestamp={timestamp}, "
            f"Signature={signature}"
        ),
        "User-Agent": "ArmoredVisionSourceLab/1.0",
    }

    started = time.perf_counter()
    response = requests.post(
        endpoint,
        data=payload.encode("utf-8"),
        headers=headers,
        timeout=(10, 30),
    )
    elapsed = time.perf_counter() - started

    try:
        response_payload = response.json()
    except ValueError:
        response_payload = {"raw": response.text[:4000]}

    return {
        "endpoint": endpoint,
        "http_status": response.status_code,
        "elapsed_seconds": round(elapsed, 3),
        "request": {
            "keyword": keyword,
            "sort_type": 1,
            "page": page,
            "limit": limit,
        },
        "response": response_payload,
    }


def extract_candidates(payload: Any) -> list[dict[str, Any]]:
    root = payload
    if isinstance(payload, dict):
        root = payload.get("data", payload)

    if isinstance(root, dict):
        offers = root.get("productOfferV2")
        if isinstance(offers, dict):
            nodes = offers.get("nodes")
            if isinstance(nodes, list):
                return [
                    {
                        "shop_id": str(node.get("shopId")) if node.get("shopId") is not None else None,
                        "item_id": str(node.get("productId")) if node.get("productId") is not None else None,
                        "product_name": node.get("productName"),
                        "product_link": node.get("productLink"),
                        "offer_link": node.get("offerLink"),
                        "images": node.get("imageUrl"),
                        "price": node.get("price"),
                        "price_min": node.get("priceMin"),
                        "price_max": node.get("priceMax"),
                        "commission_rate": node.get("commissionRate"),
                        "shop_name": node.get("shopName"),
                        "sold_count": node.get("soldCount"),
                        "rating_star": node.get("ratingStar"),
                        "category_ids": node.get("productCatIds"),
                    }
                    for node in nodes
                    if node.get("productId") is not None and node.get("shopId") is not None
                ]

    return []


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", choices=SOURCES, required=True)
    parser.add_argument(
        "--url",
        default=os.getenv("ARMORED_VISION_LAB_ORIGINAL_URL", ORIGINAL_DEFAULT),
    )
    parser.add_argument(
        "--keyword",
        default=os.getenv("ARMORED_VISION_LAB_KEYWORD", ORIGINAL_NAME_DEFAULT),
        help="Keyword sent verbatim to productOfferV2. Defaults to the original product title.",
    )
    parser.add_argument("--pages", type=int, default=3)
    parser.add_argument("--limit", type=int, default=50)
    args = parser.parse_args()

    shop_id, item_id = resolve_original(args.url)

    started = time.perf_counter()
    page_results: list[dict[str, Any]] = []
    candidates: list[dict[str, Any]] = []

    for page in range(1, max(args.pages, 1) + 1):
        result = request_product_offers(args.keyword, page=page, limit=args.limit)
        page_results.append(result)
        candidates.extend(extract_candidates(result.get("response")))
        if result["http_status"] >= 400:
            break

    unique: dict[str, dict[str, Any]] = {}
    for candidate in candidates:
        key = f"{candidate['shop_id']}:{candidate['item_id']}"
        unique[key] = candidate
    candidates = list(unique.values())

    discovered_ids = {f"{c['shop_id']}:{c['item_id']}" for c in candidates}
    benchmark_hits = sorted(discovered_ids & KNOWN_BENCHMARK_IDS)
    elapsed = time.perf_counter() - started
    last_status = page_results[-1]["http_status"] if page_results else 0

    output = {
        "status": "OK" if last_status < 400 else "SOURCE_ERROR",
        "source": args.source,
        "original": {
            "shop_id": shop_id,
            "item_id": item_id,
            "url": args.url,
            "product_name": ORIGINAL_NAME_DEFAULT,
        },
        "source_input": {
            "keyword": args.keyword,
            "pages": args.pages,
            "limit": args.limit,
        },
        "discovery": {
            "candidate_count": len(candidates),
            "unique_candidate_count": len(discovered_ids),
            "known_benchmark_hits": benchmark_hits,
            "known_benchmark_hit_count": len(benchmark_hits),
            "elapsed_seconds": round(elapsed, 3),
        },
        "candidates": candidates,
        "raw_pages": page_results,
        "rules": {
            "known_ids_are_benchmark_only": True,
            "known_ids_were_not_sent_to_source": True,
            "our_discovery": False,
            "our_reconciler": False,
            "visual_judgement": "not performed in this first pass; preserve source output first",
        },
    }

    RESULT_ROOT.mkdir(parents=True, exist_ok=True)
    output_path = RESULT_ROOT / f"{args.source}.json"
    output_path.write_text(
        json.dumps(output, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print(json.dumps({
        "status": output["status"],
        "source": args.source,
        "original": f"{shop_id}:{item_id}",
        "keyword": args.keyword,
        "pages": args.pages,
        "candidates": len(candidates),
        "known_benchmark_hits": len(benchmark_hits),
        "output": str(output_path),
        "http_status": last_status,
    }, ensure_ascii=False, indent=2))

    return 0 if last_status < 400 else 2


if __name__ == "__main__":
    raise SystemExit(main())
