"""
ArmoredVision V2 — final Shopee Affiliate feed capability probe.

This is an isolated lab probe. It does not modify Vision V1/V2.
It uses the configured BR Affiliate Open API credentials and performs only
GraphQL schema inspection for listItemFeeds/getItemFeedData. It never prints
the secret.

Environment:
  SHOPEE_APP_ID
  SHOPEE_SECRET_KEY
  SHOPEE_AFFILIATE_API_URL (optional; defaults to BR GraphQL endpoint)

Usage:
  python scripts/probe_shopee_affiliate_feeds.py

The probe first asks the live GraphQL schema whether the feed operations exist.
Only if they exist does it attempt a minimal listItemFeeds query using the
arguments exposed by the schema. No bulk feed is downloaded.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from typing import Any

import requests

ENDPOINT = os.getenv(
    "SHOPEE_AFFILIATE_API_URL",
    "https://open-api.affiliate.shopee.com.br/graphql",
)
APP_ID = os.getenv("SHOPEE_APP_ID", "")
SECRET = os.getenv("SHOPEE_SECRET_KEY", "")
OUTPUT = "storage/vision_v2_source_lab/shopee_affiliate_feeds_probe.json"

INTROSPECTION = """
query ProbeSchema {
  __schema {
    queryType {
      fields {
        name
        args {
          name
          type { kind name ofType { kind name ofType { kind name } } }
        }
        type {
          kind
          name
          ofType { kind name ofType { kind name } }
        }
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
        prefix = node.get("kind", "")
        return f"{prefix}({type_text(inner)})"
    return str(node.get("kind", ""))


def signed_post(query: str, variables: dict[str, Any] | None = None) -> dict[str, Any]:
    if not APP_ID or not SECRET:
        raise RuntimeError(
            "Missing SHOPEE_APP_ID or SHOPEE_SECRET_KEY in environment."
        )

    payload = json.dumps(
        {"query": query, "variables": variables or {}},
        separators=(",", ":"),
        ensure_ascii=False,
    )
    timestamp = str(int(time.time()))
    signature = hashlib.sha256(
        (APP_ID + timestamp + payload + SECRET).encode("utf-8")
    ).hexdigest()

    headers = {
        "Authorization": (
            f"SHA256 Credential={APP_ID}, "
            f"Timestamp={timestamp}, Signature={signature}"
        ),
        "Content-Type": "application/json",
        "Accept": "application/json",
    }

    response = requests.post(
        ENDPOINT,
        data=payload.encode("utf-8"),
        headers=headers,
        timeout=30,
    )

    try:
        body = response.json()
    except Exception:
        body = {"raw": response.text[:4000]}

    return {
        "http_status": response.status_code,
        "body": body,
    }


def main() -> int:
    result: dict[str, Any] = {
        "status": "PROBE_ONLY",
        "endpoint": ENDPOINT,
        "credentials_present": bool(APP_ID and SECRET),
        "introspection": None,
        "feed_operations": {},
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

    fields = []
    if isinstance(body, dict):
        fields = (
            body.get("data", {})
            .get("__schema", {})
            .get("queryType", {})
            .get("fields", [])
            or []
        )

    wanted = {"listItemFeeds", "getItemFeedData"}
    for field in fields:
        if field.get("name") in wanted:
            result["feed_operations"][field["name"]] = {
                "return_type": type_text(field.get("type")),
                "args": [
                    {
                        "name": arg.get("name"),
                        "type": type_text(arg.get("type")),
                    }
                    for arg in field.get("args", [])
                ],
            }

    if not result["feed_operations"]:
        result["status"] = "FEED_OPERATIONS_NOT_EXPOSED"
    elif "listItemFeeds" not in result["feed_operations"]:
        result["status"] = "LIST_ITEM_FEEDS_NOT_EXPOSED"
    else:
        result["status"] = "FEED_SCHEMA_VISIBLE"

    with open(OUTPUT, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
