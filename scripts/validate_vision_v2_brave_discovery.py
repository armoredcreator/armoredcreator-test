from __future__ import annotations

import argparse
import json
import os
import re
import time
from pathlib import Path
from urllib.parse import quote_plus, urlparse

import requests


KNOWN_BENCHMARK_IDS = {
    "1609734117:22794532266",
    "329536801:28939276497",
    "335209020:29176620632",
    "382998202:23198215253",
}


def build_queries(product_name: str) -> list[str]:
    """Generate discovery queries from the original product only.

    Known candidate IDs are intentionally never used here.
    """
    terms = [
        '"Bancada" "Barbearia" "Cabeleireiro" "90cm"',
        '"Bancada Suspensa" "Barbearia" "90cm"',
        '"Bancada" "90CM" "Gaveta" "Barbeiro"',
        '"Bancada Suspensa" "Cabeleireiro" "90cm" "Gaveta"',
        product_name,
    ]
    return [f"site:shopee.com.br {q}" for q in terms]


def extract_product_ids(url: str) -> list[str]:
    ids: set[str] = set()
    parsed = urlparse(url)
    if parsed.netloc.lower() not in {"shopee.com.br", "www.shopee.com.br"}:
        return []

    patterns = [
        r"/product/(\d+)/(\d+)",
        r"\.i\.(\d+)\.(\d+)",
    ]
    for pattern in patterns:
        for shop_id, item_id in re.findall(pattern, url):
            ids.add(f"{shop_id}:{item_id}")
    return sorted(ids)


def search_brave(api_key: str, query: str, count: int, timeout: float) -> dict:
    response = requests.get(
        "https://api.search.brave.com/res/v1/web/search",
        headers={
            "Accept": "application/json",
            "X-Subscription-Token": api_key,
        },
        params={"q": query, "count": count},
        timeout=timeout,
    )
    if response.status_code in {401, 403, 429}:
        raise RuntimeError(f"Brave HTTP {response.status_code}: {response.text[:300]}")
    response.raise_for_status()
    return response.json()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Isolated public-web discovery experiment using Brave Search API."
    )
    parser.add_argument(
        "--product-name",
        default="Bancada Suspensa Barbearia Cabeleireiro 90cm Com Gaveta",
    )
    parser.add_argument("--count", type=int, default=20)
    parser.add_argument("--delay-seconds", type=float, default=1.0)
    parser.add_argument("--output", default="storage/vision_v2_brave_discovery.json")
    args = parser.parse_args()

    api_key = os.getenv("BRAVE_SEARCH_API_KEY")
    if not api_key:
        print("[BRAVE] missing BRAVE_SEARCH_API_KEY")
        print("[BRAVE] set it only in the shell environment; never commit the key")
        return 2

    queries = build_queries(args.product_name)
    candidates: dict[str, dict] = {}
    raw_results = []

    print("[BRAVE] isolated public-search experiment")
    print("[BRAVE] V1/V2 imports: NONE")
    print("[BRAVE] known IDs are benchmark-only; they are not sent to search")
    print(f"[BRAVE] queries: {len(queries)}")

    for index, query in enumerate(queries, 1):
        print(f"[BRAVE] search {index}/{len(queries)}: {query}")
        try:
            payload = search_brave(api_key, query, args.count, 15.0)
        except Exception as exc:
            result = {
                "status": "ERROR",
                "error": str(exc),
                "queries": queries,
                "candidates": sorted(candidates),
            }
            Path(args.output).parent.mkdir(parents=True, exist_ok=True)
            Path(args.output).write_text(
                json.dumps(result, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 1

        web = payload.get("web", {})
        results = web.get("results", []) if isinstance(web, dict) else []
        raw_results.append(
            {
                "query": query,
                "result_count": len(results),
                "results": [
                    {
                        "title": item.get("title"),
                        "url": item.get("url"),
                        "description": item.get("description"),
                    }
                    for item in results
                ],
            }
        )

        for item in results:
            url = item.get("url")
            if not isinstance(url, str):
                continue
            for candidate_id in extract_product_ids(url):
                candidates.setdefault(
                    candidate_id,
                    {
                        "id": candidate_id,
                        "url": url,
                        "title": item.get("title"),
                        "description": item.get("description"),
                        "source_query": query,
                    },
                )

        if index < len(queries):
            time.sleep(max(0.0, args.delay_seconds))

    discovered_ids = sorted(candidates)
    benchmark_found = sorted(set(discovered_ids) & KNOWN_BENCHMARK_IDS)
    benchmark_missed = sorted(KNOWN_BENCHMARK_IDS - set(discovered_ids))

    output = {
        "status": "V2_BRAVE_DISCOVERY_EXPERIMENT",
        "product_name": args.product_name,
        "source": "Brave Search API",
        "query_count": len(queries),
        "queries": queries,
        "candidate_count": len(discovered_ids),
        "discovered_ids": discovered_ids,
        "candidates": [candidates[key] for key in discovered_ids],
        "benchmark": {
            "known_ids_are_evaluation_only": True,
            "found": benchmark_found,
            "missed": benchmark_missed,
        },
        "raw_results": raw_results,
    }

    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    Path(args.output).write_text(
        json.dumps(output, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print(json.dumps(
        {
            "status": output["status"],
            "candidate_count": len(discovered_ids),
            "discovered_ids": discovered_ids,
            "benchmark_found": benchmark_found,
            "benchmark_missed": benchmark_missed,
            "output": str(Path(args.output).resolve()),
        },
        ensure_ascii=False,
        indent=2,
    ))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
