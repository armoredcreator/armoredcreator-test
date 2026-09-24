"""ArmoredVision V2 — isolated Shopee Affiliate feed capability probe.

This probe only inspects the live BR Affiliate GraphQL feed schema and reads
the first five rows of the first FULL feed. It does not modify Vision V1/V2.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
import re
from typing import Any

import requests

ENDPOINT = os.getenv(
    "SHOPEE_AFFILIATE_API_URL",
    "https://open-api.affiliate.shopee.com.br/graphql",
)
APP_ID = os.getenv("SHOPEE_APP_ID", "")
SECRET = os.getenv("SHOPEE_SECRET_KEY", "")
OUTPUT = "storage/vision_v2_source_lab/shopee_affiliate_feeds_probe.json"
PAGE_LIMIT = 200
MAX_ROWS_PER_FEED = 10000
PRODUCT_RE = re.compile(r"/product/(\\d+)/(\\d+)")

INTROSPECTION = """
query ProbeSchema {
  __schema {
    queryType {
      fields {
        name
        args { name type { kind name ofType { kind name ofType { kind name } } } }
        type { kind name ofType { kind name ofType { kind name } } }
      }
    }
  }
}
"""

KNOWN = {
    "1609734117:22794532266",
    "329536801:28939276497",
    "335209020:29176620632",
    "382998202:23198215253",
}


def type_text(node: dict[str, Any] | None) -> str:
    if not node:
        return ""
    if node.get("name"):
        return str(node["name"])
    inner = node.get("ofType")
    if inner:
        return f'{node.get("kind", "")}({type_text(inner)})'
    return str(node.get("kind", ""))


def signed_post(query: str, variables: dict[str, Any] | None = None) -> dict[str, Any]:
    if not APP_ID or not SECRET:
        raise RuntimeError("Missing SHOPEE_APP_ID or SHOPEE_SECRET_KEY in environment.")

    payload = json.dumps(
        {"query": query, "variables": variables or {}},
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



def parse_row_columns(columns: str) -> dict[str, Any] | None:
    try:
        row = json.loads(columns)
    except (TypeError, json.JSONDecodeError):
        return None
    if not isinstance(row, dict):
        return None
    product_link = str(row.get("product_link") or "")
    match = PRODUCT_RE.search(product_link)
    if match:
        row["shop_id"] = match.group(1)
        row["item_id"] = match.group(2)
        row["benchmark_id"] = f"{match.group(1)}:{match.group(2)}"
    elif row.get("itemid"):
        row["item_id"] = str(row["itemid"])
    return row


def scan_feed_for_benchmarks(datafeed_id: str) -> dict[str, Any]:
    started = time.perf_counter()
    found: dict[str, dict[str, Any]] = {}
    scanned = 0
    pages = 0
    offset = 0
    total_count = None
    malformed = 0

    while offset < MAX_ROWS_PER_FEED:
        query = f"""
        query ScanItemFeedData {{
          getItemFeedData(datafeedId: "{datafeed_id}", offset: {offset}, limit: {PAGE_LIMIT}) {{
            rows {{ columns updateType }}
            pageInfo {{ offset limit totalCount hasMore }}
          }}
        }}
        """
        response = signed_post(query)
        body = response["body"]
        if response["http_status"] != 200 or not isinstance(body, dict) or body.get("errors"):
            return {
                "datafeed_id": datafeed_id,
                "http_status": response["http_status"],
                "graphql_errors": body.get("errors") if isinstance(body, dict) else None,
                "rows_scanned": scanned,
                "pages": pages,
                "matches": found,
                "malformed_rows": malformed,
                "elapsed_seconds": round(time.perf_counter() - started, 3),
            }

        container = body.get("data", {}).get("getItemFeedData") or {}
        rows = container.get("rows") or []
        page_info = container.get("pageInfo") or {}
        total_count = page_info.get("totalCount", total_count)
        pages += 1

        for raw in rows:
            scanned += 1
            parsed = parse_row_columns(raw.get("columns"))
            if parsed is None:
                malformed += 1
                continue
            benchmark_id = parsed.get("benchmark_id")
            if benchmark_id in KNOWN and benchmark_id not in found:
                found[benchmark_id] = {
                    "shop_id": parsed.get("shop_id"),
                    "item_id": parsed.get("item_id"),
                    "title": parsed.get("title"),
                    "product_link": parsed.get("product_link"),
                    "image_link": parsed.get("image_link"),
                    "global_category1": parsed.get("global_category1"),
                    "global_category2": parsed.get("global_category2"),
                }

        if not page_info.get("hasMore") or not rows:
            break
        offset += len(rows)

    return {
        "datafeed_id": datafeed_id,
        "http_status": 200,
        "graphql_errors": None,
        "rows_scanned": scanned,
        "pages": pages,
        "total_count": total_count,
        "matches": found,
        "matched_count": len(found),
        "malformed_rows": malformed,
        "elapsed_seconds": round(time.perf_counter() - started, 3),
    }

def main() -> int:
    result: dict[str, Any] = {
        "status": "PROBE_ONLY",
        "endpoint": ENDPOINT,
        "credentials_present": bool(APP_ID and SECRET),
        "introspection": None,
        "feed_operations": {},
        "feed_type_schema": {},
        "feed_query": None,
        "datafeed_schema": {},
        "datafeed_probe": None,
        "benchmark_scan": None,
        "benchmark_policy": {
            "known_ids": sorted(KNOWN),
            "ids_are_labels_only": True,
            "ids_were_not_sent_to_shopee": True,
        },
    }

    if not APP_ID or not SECRET:
        result["status"] = "CONFIG_ERROR"
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 2

    introspection = signed_post(INTROSPECTION)
    body = introspection["body"]
    result["introspection"] = {
        "http_status": introspection["http_status"],
        "graphql_errors": body.get("errors") if isinstance(body, dict) else None,
    }

    fields = (
        body.get("data", {}).get("__schema", {}).get("queryType", {}).get("fields", [])
        if isinstance(body, dict)
        else []
    )
    wanted = {"listItemFeeds", "getItemFeedData"}
    for field in fields:
        if field.get("name") in wanted:
            result["feed_operations"][field["name"]] = {
                "return_type": type_text(field.get("type")),
                "args": [
                    {"name": arg.get("name"), "type": type_text(arg.get("type"))}
                    for arg in field.get("args", [])
                ],
            }

    if not result["feed_operations"]:
        result["status"] = "FEED_OPERATIONS_NOT_EXPOSED"
    elif "listItemFeeds" not in result["feed_operations"]:
        result["status"] = "LIST_ITEM_FEEDS_NOT_EXPOSED"
    else:
        connection_query = """
        query ProbeFeedTypes {
          connection: __type(name: "ItemFeedListConnection") {
            fields { name type { kind name ofType { kind name ofType { kind name } } } }
          }
          feed: __type(name: "ItemFeed") {
            fields { name type { kind name ofType { kind name ofType { kind name } } } }
          }
        }
        """
        type_probe = signed_post(connection_query)
        type_body = type_probe["body"]
        result["feed_type_schema"] = {
            "http_status": type_probe["http_status"],
            "graphql_errors": type_body.get("errors") if isinstance(type_body, dict) else None,
            "connection_fields": type_body.get("data", {}).get("connection", {}).get("fields", [])
            if isinstance(type_body, dict) else [],
            "item_feed_fields": type_body.get("data", {}).get("feed", {}).get("fields", [])
            if isinstance(type_body, dict) else [],
        }

        connection_fields = {
            field.get("name") for field in result["feed_type_schema"].get("connection_fields", [])
        }
        item_fields = {
            field.get("name") for field in result["feed_type_schema"].get("item_feed_fields", [])
        }

        if "feeds" in connection_fields:
            preferred = [
                name for name in (
                    "datafeedId", "feedMode", "createdAt", "updatedAt",
                    "status", "fileSize", "itemCount"
                ) if name in item_fields
            ]
            if preferred:
                selection = "\n".join(f"          {name}" for name in preferred)
                feed_query = f"""
        query ProbeItemFeeds {{
          listItemFeeds(feedMode: FULL) {{
            feeds {{
{selection}
            }}
          }}
        }}
                """
                feed_result = signed_post(feed_query)
                feed_body = feed_result["body"]
                result["feed_query"] = {
                    "http_status": feed_result["http_status"],
                    "graphql_errors": feed_body.get("errors") if isinstance(feed_body, dict) else None,
                    "data": feed_body.get("data", {}).get("listItemFeeds")
                    if isinstance(feed_body, dict) else None,
                    "selected_fields": preferred,
                }

        result["status"] = (
            "FEED_QUERY_EXECUTED" if result["feed_query"] else "FEED_SCHEMA_VISIBLE"
        )

        data_query = """
        query ProbeFeedDataTypes {
          connection: __type(name: "ItemFeedDataConnection") {
            fields { name type { kind name ofType { kind name ofType { kind name } } } }
          }
          page: __type(name: "ItemFeedPageInfo") {
            fields { name type { kind name ofType { kind name ofType { kind name } } } }
          }
          row: __type(name: "ItemFeedDataRow") {
            fields { name type { kind name ofType { kind name ofType { kind name } } } }
          }
        }
        """
        data_schema_result = signed_post(data_query)
        data_schema_body = data_schema_result["body"]
        result["datafeed_schema"] = {
            "http_status": data_schema_result["http_status"],
            "graphql_errors": data_schema_body.get("errors") if isinstance(data_schema_body, dict) else None,
            "connection_fields": data_schema_body.get("data", {}).get("connection", {}).get("fields", [])
            if isinstance(data_schema_body, dict) else [],
            "page_info_fields": data_schema_body.get("data", {}).get("page", {}).get("fields", [])
            if isinstance(data_schema_body, dict) else [],
            "row_fields": data_schema_body.get("data", {}).get("row", {}).get("fields", [])
            if isinstance(data_schema_body, dict) else [],
        }

        connection_data_fields = {
            field.get("name")
            for field in result["datafeed_schema"].get("connection_fields", [])
        }
        row_fields = {
            field.get("name")
            for field in result["datafeed_schema"].get("row_fields", [])
        }
        page_fields = {
            field.get("name")
            for field in result["datafeed_schema"].get("page_info_fields", [])
        }

        feeds = (result["feed_query"] or {}).get("data", {}).get("feeds", [])
        if "rows" in connection_data_fields and "columns" in row_fields and feeds:
            datafeed_id = feeds[0].get("datafeedId")
            if datafeed_id:
                page_selection = "\n".join(
                    f"              {name}" for name in page_fields
                )
                page_block = f"pageInfo {{ {page_selection} }}" if page_selection else "pageInfo"
                datafeed_query = f"""
        query ProbeItemFeedData {{
          getItemFeedData(datafeedId: "{datafeed_id}", offset: 0, limit: 5) {{
            rows {{ columns updateType }}
            {page_block}
          }}
        }}
                """
                data_result = signed_post(datafeed_query)
                data_body = data_result["body"]
                result["datafeed_probe"] = {
                    "http_status": data_result["http_status"],
                    "graphql_errors": data_body.get("errors") if isinstance(data_body, dict) else None,
                    "data": data_body.get("data", {}).get("getItemFeedData")
                    if isinstance(data_body, dict) else None,
                    "datafeed_id": datafeed_id,
                    "selected_fields": ["rows.columns", "rows.updateType", *[
                        f"pageInfo.{name}" for name in page_fields
                    ]],
                }
                full_feed_ids = [
                    item.get("datafeedId")
                    for item in (result["feed_query"] or {}).get("data", {}).get("feeds", [])
                    if item.get("datafeedId")
                ]
                result["benchmark_scan"] = {
                    "page_limit": PAGE_LIMIT,
                    "max_rows_per_feed": MAX_ROWS_PER_FEED,
                    "feeds": [scan_feed_for_benchmarks(feed_id) for feed_id in full_feed_ids],
                }

    with open(OUTPUT, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
