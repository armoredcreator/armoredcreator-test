"""
ArmoredVision V2 — Shopee internal recommendation probe.

This is an isolated research probe. It does NOT modify Vision V1 or V2
discovery/reconciliation. It tests documented/reverse-engineered Shopee
marketplace recommendation routes directly.

Target product:
  shop_id=382998202
  item_id=23298215147

The four known-positive IDs are ONLY post-response benchmark labels.
They are never sent to Shopee.

Usage:
  python scripts/probe_shopee_recommendations.py --endpoint all

Optional:
  --endpoint product_detail_page
  --endpoint recommend_post
  --endpoint recommend_v2
"""

from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path
from typing import Any

import requests


SHOP_ID = 382998202
ITEM_ID = 23298215147
KNOWN_BENCHMARK_IDS = {
    "1609734117:22794532266",
    "329536801:28939276497",
    "335209020:29176620632",
    "382998202:23198215253",
}
OUTPUT = Path("storage/vision_v2_source_lab/shopee_recommendations.json")

BASE_URL = os.getenv("SHOPEE_MARKETPLACE_BASE_URL", "https://shopee.com.br")

ENDPOINTS = {
    "product_detail_page": "/api/v4/recommend/product_detail_page",
    "recommend_post": "/api/v4/recommend/recommend_post",
    "recommend_v2": "/api/v4/recommend/recommend_v2",
}


def base_headers() -> dict[str, str]:
    return {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/153.0.0.0 Safari/537.36"
        ),
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.8",
        "Content-Type": "application/json",
        "Origin": BASE_URL,
        "Referer": f"{BASE_URL}/",
    }


def payload_for(name: str) -> dict[str, Any]:
    if name == "product_detail_page":
        return {
            "requests": [
                {
                    "url": f"{BASE_URL}{ENDPOINTS[name]}",
                    "method": "POST",
                    "payload": {
                        "offset": 0,
                        "limit": 48,
                        "shopid": SHOP_ID,
                        "itemid": ITEM_ID,
                    },
                }
            ]
        }

    if name == "recommend_post":
        return {
            "requests": [
                {
                    "url": f"{BASE_URL}{ENDPOINTS[name]}",
                    "method": "POST",
                    "payload": {
                        "offset": 0,
                        "limit": 48,
                        "shopid": SHOP_ID,
                        "itemid": ITEM_ID,
                    },
                }
            ]
        }

    if name == "recommend_v2":
        # This route is category-oriented. We intentionally do not invent a
        # category ID; the probe records the response/error if this route
        # requires one.
        return {
            "requests": [
                {
                    "url": f"{BASE_URL}{ENDPOINTS[name]}",
                    "method": "POST",
                    "payload": {
                        "offset": 0,
                        "limit": 48,
                        "bundle": "category_landing_page",
                        "cat_level": 1,
                    },
                }
            ]
        }

    raise ValueError(name)


def extract_ids(value: Any) -> set[str]:
    found: set[str] = set()

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            shop = node.get("shopid", node.get("shopId"))
            item = node.get("itemid", node.get("itemId"))
            if shop is not None and item is not None:
                found.add(f"{shop}:{item}")
            for child in node.values():
                walk(child)
        elif isinstance(node, list):
            for child in node:
                walk(child)

    walk(value)
    return found


def run_endpoint(name: str) -> dict[str, Any]:
    url = f"{BASE_URL}{ENDPOINTS[name]}"
    payload = payload_for(name)
    started = time.perf_counter()

    try:
        response = requests.post(
            url,
            json=payload,
            headers=base_headers(),
            timeout=(10, 30),
        )
        elapsed = round(time.perf_counter() - started, 3)

        try:
            body: Any = response.json()
        except ValueError:
            body = {"raw_text": response.text[:10000]}

        ids = extract_ids(body)
        hits = sorted(ids & KNOWN_BENCHMARK_IDS)

        return {
            "endpoint": name,
            "url": url,
            "http_status": response.status_code,
            "elapsed_seconds": elapsed,
            "discovered_ids": len(ids),
            "known_benchmark_hits": hits,
            "known_benchmark_hit_count": len(hits),
            "response": body,
        }
    except Exception as exc:
        return {
            "endpoint": name,
            "url": url,
            "http_status": None,
            "elapsed_seconds": round(time.perf_counter() - started, 3),
            "discovered_ids": 0,
            "known_benchmark_hits": [],
            "known_benchmark_hit_count": 0,
            "error": f"{type(exc).__name__}: {exc}",
        }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--endpoint",
        choices=["all", *ENDPOINTS.keys()],
        default="all",
    )
    args = parser.parse_args()

    names = list(ENDPOINTS) if args.endpoint == "all" else [args.endpoint]
    results = [run_endpoint(name) for name in names]

    output = {
        "status": "PROBE_ONLY",
        "target": {
            "shop_id": SHOP_ID,
            "item_id": ITEM_ID,
        },
        "benchmark_policy": {
            "known_ids_are_post_response_labels_only": True,
            "known_ids_were_not_sent_to_shopee": True,
        },
        "results": results,
    }

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(
        json.dumps(output, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print(json.dumps(
        {
            "status": "PROBE_ONLY",
            "output": str(OUTPUT),
            "results": [
                {
                    "endpoint": r["endpoint"],
                    "http_status": r["http_status"],
                    "discovered_ids": r["discovered_ids"],
                    "known_benchmark_hits": r["known_benchmark_hits"],
                    "error": r.get("error"),
                }
                for r in results
            ],
        },
        ensure_ascii=False,
        indent=2,
    ))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
