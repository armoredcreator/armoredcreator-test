"""
ArmoredVision V2 — Shopee browser interception diagnostic.

The protected /api/v4/recommend/* endpoints return 403 to plain HTTP clients.
This probe lets real Chrome execute Shopee JS and logs whether the PDP actually
requests the recommendation route.

It also records:
- final URL
- page title
- page status
- console errors
- recommendation request/response URLs and status
- anti-fraud/captcha requests
- a short body preview

No credentials or cookies are persisted.
"""

from __future__ import annotations

import json
from pathlib import Path

from playwright.sync_api import sync_playwright

SHOP_ID = 382998202
ITEM_ID = 23298215147
PRODUCT_URL = f"https://shopee.com.br/product/{SHOP_ID}/{ITEM_ID}"
OUTPUT = Path("storage/vision_v2_source_lab/shopee_recommendations_browser_diag.json")


def main() -> int:
    events = []
    console_errors = []

    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome", headless=False)
        context = browser.new_context(
            locale="pt-BR",
            viewport={"width": 1440, "height": 1000},
        )
        page = context.new_page()

        def request_handler(req) -> None:
            u = req.url
            if "/api/v4/" in u or "/anti_fraud/" in u:
                if "recommend" in u or "captcha" in u or "anti_fraud" in u:
                    event = {
                        "kind": "request",
                        "method": req.method,
                        "url": u,
                    }
                    events.append(event)
                    print(f"[REQ] {req.method} {u}")

        def response_handler(resp) -> None:
            u = resp.url
            if "/api/v4/" in u or "/anti_fraud/" in u:
                if "recommend" in u or "captcha" in u or "anti_fraud" in u:
                    event = {
                        "kind": "response",
                        "status": resp.status,
                        "url": u,
                    }
                    events.append(event)
                    print(f"[RESP] {resp.status} {u}")

        def console_handler(msg) -> None:
            if msg.type == "error":
                console_errors.append(msg.text)
                print(f"[CONSOLE ERROR] {msg.text}")

        page.on("request", request_handler)
        page.on("response", response_handler)
        page.on("console", console_handler)

        print(f"Opening: {PRODUCT_URL}")
        navigation = page.goto(
            PRODUCT_URL,
            wait_until="domcontentloaded",
            timeout=60000,
        )

        page.wait_for_timeout(20000)

        try:
            body_text = page.locator("body").inner_text(timeout=5000)
        except Exception as exc:
            body_text = f"<body unavailable: {type(exc).__name__}>"

        result = {
            "status": "PROBE_ONLY",
            "target": {"shop_id": SHOP_ID, "item_id": ITEM_ID},
            "product_url": PRODUCT_URL,
            "final_url": page.url,
            "title": page.title(),
            "navigation_status": navigation.status if navigation else None,
            "body_preview": body_text[:3000],
            "body_chars": len(body_text),
            "events": events,
            "console_errors": console_errors[:100],
        }

        OUTPUT.parent.mkdir(parents=True, exist_ok=True)
        OUTPUT.write_text(
            json.dumps(result, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        print(json.dumps({
            "status": "PROBE_ONLY",
            "output": str(OUTPUT),
            "final_url": page.url,
            "title": page.title(),
            "navigation_status": navigation.status if navigation else None,
            "events": len(events),
            "console_errors": len(console_errors),
        }, ensure_ascii=False, indent=2))

        browser.close()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
