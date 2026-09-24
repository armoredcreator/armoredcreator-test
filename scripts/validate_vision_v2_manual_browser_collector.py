"""Isolated manual Chrome collector for Shopee discovery experiments.

This script does NOT navigate Shopee, click anything, solve verification, or alter
browser fingerprints. It attaches to a Chrome instance that the user opened
manually with remote debugging enabled and reads the currently rendered DOM.

Usage:
    python scripts/validate_vision_v2_manual_browser_collector.py

The user performs the Shopee search manually. The collector then extracts visible
product links/cards from the current page and writes JSON to storage/.
"""

from __future__ import annotations

import argparse
import json
import re
import time
from pathlib import Path
from urllib.parse import urljoin

from playwright.sync_api import sync_playwright


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / "storage" / "vision_v2_manual_browser_discovery.json"
PRODUCT_RE = re.compile(r"/product/(\\d+)/(\\d+)")


def clean_text(value: str | None) -> str:
    return re.sub(r"\\s+", " ", value or "").strip()


def collect(page):
    products = {}
    for anchor in page.locator("a[href*='/product/']").all():
        href = anchor.get_attribute("href") or ""
        match = PRODUCT_RE.search(href)
        if not match:
            continue

        shop_id, item_id = match.groups()
        key = f"{shop_id}:{item_id}"
        if key in products:
            continue

        title = clean_text(
            anchor.get_attribute("aria-label")
            or anchor.get_attribute("title")
            or anchor.inner_text(timeout=1000)
        )

        image_url = None
        try:
            image = anchor.locator("img").first
            if image.count():
                image_url = image.get_attribute("src") or image.get_attribute("data-src")
                if not image_url:
                    srcset = image.get_attribute("srcset") or ""
                    image_url = srcset.split(",")[0].strip().split(" ")[0] or None
        except Exception:
            pass

        products[key] = {
            "shop_id": shop_id,
            "item_id": item_id,
            "product_name": title,
            "product_link": urljoin("https://shopee.com.br", href),
            "image_url": image_url,
        }

    return list(products.values())


def verification_visible(page) -> bool:
    try:
        body = clean_text(page.locator("body").inner_text(timeout=1500)).lower()
    except Exception:
        return False

    markers = (
        "tente novamente mais tarde",
        "a verificação falhou",
        "verificação falhou",
        "verification failed",
        "try again later",
    )
    return any(marker in body for marker in markers)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--cdp-url", default="http://127.0.0.1:9222")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--wait-seconds", type=float, default=2.0)
    args = parser.parse_args()

    print("[MANUAL-WEB] isolated collector")
    print("[MANUAL-WEB] no Shopee navigation")
    print("[MANUAL-WEB] no automated clicks")
    print("[MANUAL-WEB] no verification bypass")
    print(f"[MANUAL-WEB] connecting to {args.cdp_url}")

    with sync_playwright() as pw:
        browser = pw.chromium.connect_over_cdp(args.cdp_url)
        contexts = browser.contexts
        if not contexts:
            raise RuntimeError("Nenhum contexto Chrome encontrado.")

        pages = [p for ctx in contexts for p in ctx.pages]
        shopee_pages = [p for p in pages if "shopee.com.br" in (p.url or "")]
        if not shopee_pages:
            raise RuntimeError(
                "Nenhuma aba Shopee encontrada. Abra manualmente uma busca da Shopee "
                "no Chrome conectado e execute novamente."
            )

        page = shopee_pages[0]
        print(f"[MANUAL-WEB] page: {page.url}")

        time.sleep(max(0.0, args.wait_seconds))

        blocked = verification_visible(page)
        products = collect(page)

        payload = {
            "status": "V2_MANUAL_BROWSER_DISCOVERY",
            "url": page.url,
            "verification_visible": blocked,
            "candidate_count": len(products),
            "positive_found": [
                key
                for key in (
                    "1609734117:22794532266",
                    "329536801:28939276497",
                )
                if any(p["shop_id"] + ":" + p["item_id"] == key for p in products)
            ],
            "positive_missed": [
                key
                for key in (
                    "1609734117:22794532266",
                    "329536801:28939276497",
                )
                if not any(p["shop_id"] + ":" + p["item_id"] == key for p in products)
            ],
            "products": products,
        }

        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        print(json.dumps(
            {
                "status": payload["status"],
                "url": payload["url"],
                "verification_visible": payload["verification_visible"],
                "candidate_count": payload["candidate_count"],
                "positive_found": payload["positive_found"],
                "positive_missed": payload["positive_missed"],
                "output": str(output),
            },
            ensure_ascii=False,
            indent=2,
        ))


if __name__ == "__main__":
    main()
