"""ArmoredVision V2 — fast coverage probe for Shopee Affiliate FULL feeds.

Samples selected offsets instead of scanning the entire feed. This is a lab
probe only; it does not modify Vision V1/V2.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

import requests

ENDPOINT = os.getenv(
    "SHOPEE_AFFILIATE_API_URL",
    "https://open-api.affiliate.shopee.com.br/graphql",
)
APP_ID = os.getenv("SHOPEE_APP_ID", "")
SECRET = os.getenv("SHOPEE_SECRET_KEY", "")
PAGE_LIMIT = 200

KNOWN = {
    "1609734117:22794532266",
    "329536801:28939276497",
    "335209020:29176620632",
    "382998202:23198215253",
}

BENCHMARK_TERMS = {
    "bancada", "barbearia", "cabeleireiro", "cabeleireira",
    "barbeiro", "penteadeira", "90cm", "gaveta",
}
PRODUCT_RE = re.compile(r"/product/(\d+)/(\d+)")


def signed_post(query: str) -> dict[str, Any]:
    payload = json.dumps(
        {"query": query, "variables": {}},
        separators=(",", ":"),
        ensure_ascii=False,
    )
    timestamp = str(int(time.time()))
    signature = hashlib.sha256(
        (APP_ID + timestamp + payload + SECRET).encode("utf-8")
    ).hexdigest()
    response = requests.post(
        ENDPOINT,
        data=payload.encode("utf-8"),
        headers={
            "Authorization": (
                f"SHA256 Credential={APP_ID}, "
                f"Timestamp={timestamp}, Signature={signature}"
            ),
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        timeout=30,
    )
    try:
        body = response.json()
    except Exception:
        body = {"raw": response.text[:4000]}
    return {"http_status": response.status_code, "body": body}


def parse_row(columns: str) -> dict[str, Any] | None:
    try:
        row = json.loads(columns)
    except (TypeError, json.JSONDecodeError):
        return None
    if not isinstance(row, dict):
        return None
    link = str(row.get("product_link") or "")
    match = PRODUCT_RE.search(link)
    if match:
        row["benchmark_id"] = f"{match.group(1)}:{match.group(2)}"
    return row


def scan_offset(datafeed_id: str, offset: int) -> dict[str, Any]:
    query = f"""
    query Sample {{
      getItemFeedData(datafeedId: "{datafeed_id}", offset: {offset}, limit: {PAGE_LIMIT}) {{
        rows {{ columns updateType }}
        pageInfo {{ offset limit totalCount hasMore }}
      }}
    }}
    """
    result = signed_post(query)
    body = result["body"]
    if result["http_status"] != 200 or body.get("errors"):
        return {
            "datafeed_id": datafeed_id,
            "offset": offset,
            "http_status": result["http_status"],
            "graphql_errors": body.get("errors"),
        }

    container = body.get("data", {}).get("getItemFeedData") or {}
    rows = container.get("rows") or []
    hits: list[dict[str, Any]] = []
    term_hits = 0
    malformed = 0

    for raw in rows:
        parsed = parse_row(raw.get("columns"))
        if parsed is None:
            malformed += 1
            continue
        if parsed.get("benchmark_id") in KNOWN:
            hits.append({
                "benchmark_id": parsed["benchmark_id"],
                "title": parsed.get("title"),
                "product_link": parsed.get("product_link"),
            })
        title = str(parsed.get("title") or "").lower()
        description = str(parsed.get("description") or "").lower()
        haystack = f"{title} {description}"
        matched_terms = sorted(term for term in BENCHMARK_TERMS if term in haystack)
        if len(matched_terms) >= 3:
            term_hits += 1
            if len(hits) < 20:
                hits.append({
                    "benchmark_id": parsed.get("benchmark_id"),
                    "title": parsed.get("title"),
                    "product_link": parsed.get("product_link"),
                    "matched_terms": matched_terms,
                })

    page = container.get("pageInfo") or {}
    return {
        "datafeed_id": datafeed_id,
        "offset": offset,
        "rows": len(rows),
        "total_count": page.get("totalCount"),
        "has_more": page.get("hasMore"),
        "malformed_rows": malformed,
        "benchmark_or_term_hits": hits,
        "strong_term_rows": term_hits,
    }


def main() -> int:
    if not APP_ID or not SECRET:
        print(json.dumps({"status": "CONFIG_ERROR"}, indent=2))
        return 2

    feed_query = """
    query Feeds {
      listItemFeeds(feedMode: FULL) {
        feeds { datafeedId feedMode totalCount }
      }
    }
    """
    feed_result = signed_post(feed_query)
    body = feed_result["body"]
    if feed_result["http_status"] != 200 or body.get("errors"):
        print(json.dumps({"status": "FEED_QUERY_ERROR", "response": body}, indent=2))
        return 1

    feeds = body.get("data", {}).get("listItemFeeds", {}).get("feeds", [])
    feeds = [f for f in feeds if f.get("feedMode") == "FULL" and f.get("datafeedId")]

    offsets_by_feed: dict[str, list[int]] = {}
    for feed in feeds:
        total = int(feed.get("totalCount") or 0)
        if total <= 0:
            continue
        offsets = {0, max(0, total // 5), max(0, (2 * total) // 5),
                   max(0, (3 * total) // 5), max(0, (4 * total) // 5)}
        offsets_by_feed[feed["datafeedId"]] = sorted(
            min(offset, max(0, total - PAGE_LIMIT)) for offset in offsets
        )

    jobs = [
        (feed_id, offset)
        for feed_id, offsets in offsets_by_feed.items()
        for offset in offsets
    ]

    started = time.perf_counter()
    results: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=min(4, len(jobs) or 1)) as pool:
        futures = [pool.submit(scan_offset, feed_id, offset) for feed_id, offset in jobs]
        for future in as_completed(futures):
            results.append(future.result())

    results.sort(key=lambda item: (item["datafeed_id"], item["offset"]))
    output = {
        "status": "OFFSET_SAMPLE_EXECUTED",
        "page_limit": PAGE_LIMIT,
        "feeds": [
            {
                "datafeed_id": f["datafeedId"],
                "total_count": f["totalCount"],
                "offsets": offsets_by_feed.get(f["datafeedId"], []),
            }
            for f in feeds
        ],
        "samples": results,
        "summary": {
            "sampled_rows": sum(int(r.get("rows", 0)) for r in results),
            "exact_benchmark_hits": sum(
                1
                for r in results
                for h in r.get("benchmark_or_term_hits", [])
                if h.get("benchmark_id") in KNOWN
            ),
            "strong_term_rows": sum(int(r.get("strong_term_rows", 0)) for r in results),
            "elapsed_seconds": round(time.perf_counter() - started, 3),
        },
    }
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
