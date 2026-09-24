"""
ArmoredVision V2 — source validation laboratory.

This script intentionally tests ONE external discovery source at a time.
It does not use our discovery logic, known benchmark IDs as input, CLIP,
SIFT, dHash, or the V2 reconciler.

First source: Shopee Affiliate Open API product_item_recommend_get.

Environment:
  SHOPEE_ACCESS_TOKEN or SHOPEE_API_ACCESS_TOKEN
  optional: ARMORED_VISION_LAB_ORIGINAL_URL

Usage:
  python scripts/validate_vision_v2_source_lab.py --source recommendations \
      --url "https://s.shopee.com.br/8KolJcZrfU"

The result is saved under storage/vision_v2_source_lab/<source>.json.
Known IDs may be configured only as post-discovery benchmark labels and are
never sent to the discovery source.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import requests


ORIGINAL_DEFAULT = "https://s.shopee.com.br/8KolJcZrfU"
RESULT_ROOT = Path("storage/vision_v2_source_lab")

# Benchmark-only labels. Never included in requests.
KNOWN_BENCHMARK_IDS = {
    "1609734117:22794532266",
    "329536801:28939276497",
    "335209020:29176620632",
    "382998202:23198215253",
}

SOURCES = ("recommendations",)


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

    # Short links are resolved without logging cookies or auth headers.
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

    # Some Shopee pages expose the canonical product URL in HTML.
    m = re.search(r"https?://(?:www\.)?shopee\.com\.br/product/(\d+)/(\d+)", r.text)
    if m:
        return m.group(1), m.group(2)
    m = re.search(r"shopee\.com\.br/[^\"' ]+\.i\.(\d+)\.(\d+)", r.text)
    if m:
        return m.group(1), m.group(2)

    raise RuntimeError(f"Não foi possível resolver shop_id/item_id: {url} -> {r.url}")


def request_recommendations(shop_id: str, item_id: str) -> dict[str, Any]:
    token = os.getenv("SHOPEE_ACCESS_TOKEN") or os.getenv("SHOPEE_API_ACCESS_TOKEN")
    if not token:
        raise RuntimeError(
            "Defina SHOPEE_ACCESS_TOKEN (ou SHOPEE_API_ACCESS_TOKEN) "
            "para testar product_item_recommend_get."
        )

    endpoint = os.getenv(
        "SHOPEE_RECOMMENDATIONS_ENDPOINT",
        "https://open.shopee.vn/openapi/product/v2/product_item_recommend_get",
    )
    params = {"item_id": item_id, "shop_id": shop_id}
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
        "User-Agent": "ArmoredVisionSourceLab/1.0",
    }

    started = time.perf_counter()
    response = requests.get(
        endpoint,
        params=params,
        headers=headers,
        timeout=(10, 30),
    )
    elapsed = time.perf_counter() - started

    try:
        payload = response.json()
    except ValueError:
        payload = {"raw": response.text[:4000]}

    return {
        "endpoint": endpoint,
        "http_status": response.status_code,
        "elapsed_seconds": round(elapsed, 3),
        "request_params": params,
        "response": payload,
    }


def extract_candidates(payload: Any) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            item_id = node.get("item_id") or node.get("itemId")
            shop_id = node.get("shop_id") or node.get("shopId")
            name = node.get("title") or node.get("productName") or node.get("product_name")
            link = node.get("product_link") or node.get("productLink") or node.get("url")
            images = node.get("images") or node.get("imageUrl") or node.get("image_url")
            if item_id is not None and shop_id is not None:
                candidates.append(
                    {
                        "shop_id": str(shop_id),
                        "item_id": str(item_id),
                        "product_name": name,
                        "product_link": link,
                        "images": images,
                    }
                )
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(payload)

    unique: dict[str, dict[str, Any]] = {}
    for candidate in candidates:
        key = f"{candidate['shop_id']}:{candidate['item_id']}"
        unique[key] = candidate
    return list(unique.values())


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", choices=SOURCES, required=True)
    parser.add_argument("--url", default=os.getenv("ARMORED_VISION_LAB_ORIGINAL_URL", ORIGINAL_DEFAULT))
    args = parser.parse_args()

    shop_id, item_id = resolve_original(args.url)
    result = request_recommendations(shop_id, item_id)
    candidates = extract_candidates(result.get("response"))

    discovered_ids = {
        f"{c['shop_id']}:{c['item_id']}" for c in candidates
    }

    output = {
        "status": "OK" if result["http_status"] < 400 else "SOURCE_ERROR",
        "source": args.source,
        "original": {
            "shop_id": shop_id,
            "item_id": item_id,
            "url": args.url,
        },
        "discovery": {
            "candidate_count": len(candidates),
            "unique_candidate_count": len(discovered_ids),
            "known_benchmark_hits": sorted(discovered_ids & KNOWN_BENCHMARK_IDS),
            "known_benchmark_hit_count": len(discovered_ids & KNOWN_BENCHMARK_IDS),
        },
        "candidates": candidates,
        "raw": result,
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
    output_path.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")

    print(json.dumps({
        "status": output["status"],
        "source": args.source,
        "original": f"{shop_id}:{item_id}",
        "candidates": len(candidates),
        "known_benchmark_hits": output["discovery"]["known_benchmark_hit_count"],
        "output": str(output_path),
        "http_status": result["http_status"],
    }, ensure_ascii=False, indent=2))

    return 0 if result["http_status"] < 400 else 2


if __name__ == "__main__":
    raise SystemExit(main())
