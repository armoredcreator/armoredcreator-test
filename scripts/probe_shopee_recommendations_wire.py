"""
ArmoredVision V2 — Shopee recommendation wire probe (v2).

The previous probe used a scraper-wrapper payload and received HTTP 403.
This probe sends the documented Shopee wire payload directly to
/api/v4/recommend/product_detail_page.

Target:
  shop_id=382998202
  item_id=23298215147

Known positives are post-response labels only and are never sent.
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
BASE_URL = os.getenv("SHOPEE_MARKETPLACE_BASE_URL", "https://shopee.com.br")
OUTPUT = Path("storage/vision_v2_source_lab/shopee_recommendations_wire.json")

ENDPOINTS = {
    "product_detail_page": "/api/v4/recommend/product_detail_page",
    "recommend_post": "/api/v4/recommend/recommend_post",
}


def headers() -> dict[str, str]:
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
        "Referer": f"{BASE_URL}/product/{SHOP_ID}/{ITEM_ID}",
    }


def payload(name: str) -> dict[str, Any]:
    if name == "product_detail_page":
        return {
            "offset": 0,
            "limit": 48,
            "shopid": SHOP_ID,
            "itemid": ITEM_ID,
        }
    if name == "recommend_post":
        return {
            "offset": 0,
            "limit": 48,
            "section": "from_same_shop",
            "item_card": 3,
            "bundle": "product_detail_page_ftss",
            "shopid": SHOP_ID,
            "itemid": ITEM_ID,
        }
    raise ValueError(name)


def extract_items(value: Any) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            shop = node.get("shopid", node.get("shopId"))
            item = node.get("itemid", node.get("itemId"))
            if shop is not None and item is not None:
                found.append({
                    "shop_id": str(shop),
                    "item_id": str(item),
                    "id": f"{shop}:{item}",
                    "name": node.get("name", node.get("title")),
                    "image": node.get("image"),
                })
            for child in node.values():
                walk(child)
        elif isinstance(node, list):
            for child in node:
                walk(child)

    walk(value)

    unique: dict[str, dict[str, Any]] = {}
    for item in found:
        unique[item["id"]] = item
    return list(unique.values())


def run(name: str) -> dict[str, Any]:
    url = f"{BASE_URL}{ENDPOINTS[name]}"
    started = time.perf_counter()

    try:
        response = requests.post(
            url,
            json=payload(name),
            headers=headers(),
            timeout=(10, 30),
        )
        elapsed = round(time.perf_counter() - started, 3)
        try:
            body = response.json()
        except ValueError:
            body = {"raw_text": response.text[:10000]}

        items = extract_items(body)
        ids = {x["id"] for x in items}
        hits = sorted(ids & KNOWN_BENCHMARK_IDS)

        return {
            "endpoint": name,
            "url": url,
            "http_status": response.status_code,
            "elapsed_seconds": elapsed,
            "payload_shape": "direct_shopee_wire",
            "discovered_items": len(items),
            "known_benchmark_hits": hits,
            "items": items,
            "response": body,
        }
    except Exception as exc:
        return {
            "endpoint": name,
            "url": url,
            "http_status": None,
            "elapsed_seconds": round(time.perf_counter() - started, 3),
            "payload_shape": "direct_shopee_wire",
            "discovered_items": 0,
            "known_benchmark_hits": [],
            "error": f"{type(exc).__name__}: {exc}",
        }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--endpoint",
        choices=["all", *ENDPOINTS.keys()],
        default="product_detail_page",
    )
    args = parser.parse_args()

    names = list(ENDPOINTS) if args.endpoint == "all" else [args.endpoint]
    results = [run(name) for name in names]

    output = {
        "status": "PROBE_ONLY",
        "target": {"shop_id": SHOP_ID, "item_id": ITEM_ID},
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

    print(json.dumps({
        "status": "PROBE_ONLY",
        "output": str(OUTPUT),
        "results": [
            {
                "endpoint": r["endpoint"],
                "http_status": r["http_status"],
                "discovered_items": r["discovered_items"],
                "known_benchmark_hits": r["known_benchmark_hits"],
                "error": r.get("error"),
            }
            for r in results
        ],
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    raise SystemExit(main())
