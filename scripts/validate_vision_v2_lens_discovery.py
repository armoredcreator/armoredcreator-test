"""Isolated Google Lens browser discovery experiment.

This is a research/POC tool. It uses the normal Google/Lens web interface in a
regular Playwright Chromium session. It does not use a Lens API, does not log
into Shopee, and does not attempt to bypass CAPTCHA/verification.

The experiment uploads one local product image to Google Lens and extracts
public result links from the rendered result page. Shopee URLs are isolated for
later V2 reconciliation.

Usage:
    python scripts/validate_vision_v2_lens_discovery.py --image "C:\\path\\produto.jpg"

Optional:
    --headed
    --keep-open
    --query "bancada suspensa barbearia cabeleireiro 90cm gaveta"
    --output storage/vision_v2_lens_discovery.json
"""

from __future__ import annotations

import argparse
import json
import re
import time
from pathlib import Path
from urllib.parse import urljoin, urlparse

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / "storage" / "vision_v2_lens_discovery.json"

PRODUCT_RE = re.compile(r"/product/(\d+)/(\d+)")
POSITIVE_IDS = {
    "1609734117:22794532266",
    "329536801:28939276497",
}


def clean_text(value: str | None) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def is_shopee_url(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    return host.endswith("shopee.com.br")


def extract_links(page):
    results = []
    seen = set()

    for anchor in page.locator("a[href]").all():
        href = anchor.get_attribute("href") or ""
        if not href:
            continue

        href = urljoin(page.url, href)
        if not href.startswith(("http://", "https://")):
            continue

        text = clean_text(
            anchor.inner_text(timeout=800)
            if anchor.is_visible()
            else anchor.get_attribute("aria-label")
        )

        key = href.split("#", 1)[0]
        if key in seen:
            continue
        seen.add(key)

        results.append(
            {
                "url": key,
                "text": text[:500],
                "is_shopee": is_shopee_url(key),
                "product_id": (
                    f"{PRODUCT_RE.search(key).group(1)}:{PRODUCT_RE.search(key).group(2)}"
                    if PRODUCT_RE.search(key)
                    else None
                ),
            }
        )

    return results


def click_lens_upload(page):
    selectors = (
        'div[aria-label*="Pesquisar por imagem" i]',
        'div[aria-label*="Search by image" i]',
        'button[aria-label*="Pesquisar por imagem" i]',
        'button[aria-label*="Search by image" i]',
        'div[aria-label*="Lens" i]',
        'button[aria-label*="Lens" i]',
    )

    for selector in selectors:
        locator = page.locator(selector).first
        try:
            if locator.count() and locator.is_visible():
                locator.click()
                return True
        except Exception:
            continue

    return False


def upload_image(page, image_path: Path):
    # Google may expose the upload input immediately or after opening Lens.
    file_inputs = page.locator('input[type="file"]')
    try:
        if file_inputs.count():
            file_inputs.first.set_input_files(str(image_path))
            return "direct-file-input"
    except Exception:
        pass

    if not click_lens_upload(page):
        raise RuntimeError(
            "Não encontrei o controle 'Pesquisar por imagem/Lens' no Google. "
            "A interface pode ter mudado; abra google.com manualmente e tente "
            "novamente com --headed para inspeção."
        )

    try:
        page.locator('input[type="file"]').first.wait_for(
            state="attached", timeout=5000
        )
        page.locator('input[type="file"]').first.set_input_files(str(image_path))
        return "lens-dialog-file-input"
    except Exception:
        pass

    raise RuntimeError(
        "O Lens abriu, mas nenhum input de upload ficou disponível. "
        "Não foi usado nenhum método alternativo ou bypass."
    )


def collect_shopee_products(links):
    products = {}
    for entry in links:
        match = PRODUCT_RE.search(entry["url"])
        if not match or not is_shopee_url(entry["url"]):
            continue

        shop_id, item_id = match.groups()
        key = f"{shop_id}:{item_id}"
        products.setdefault(
            key,
            {
                "shop_id": shop_id,
                "item_id": item_id,
                "product_link": entry["url"],
                "result_text": entry["text"],
            },
        )
    return list(products.values())


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--image", required=True, help="Imagem local do produto.")
    parser.add_argument(
        "--query",
        default="",
        help="Texto opcional para refinar o resultado do Lens.",
    )
    parser.add_argument("--headed", action="store_true")
    parser.add_argument("--keep-open", action="store_true")
    parser.add_argument("--wait-seconds", type=float, default=5.0)
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    args = parser.parse_args()

    image_path = Path(args.image).expanduser().resolve()
    if not image_path.is_file():
        raise FileNotFoundError(f"Imagem não encontrada: {image_path}")

    print("[LENS] isolated browser experiment")
    print("[LENS] Google Lens web UI — no Lens API")
    print("[LENS] no Shopee login")
    print("[LENS] no CAPTCHA/verification bypass")
    print(f"[LENS] image: {image_path}")

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=not args.headed)
        page = browser.new_page(
            viewport={"width": 1440, "height": 1000},
            locale="pt-BR",
        )

        page.goto("https://www.google.com/", wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(1500)

        method = upload_image(page, image_path)
        print(f"[LENS] upload method: {method}")

        try:
            page.wait_for_load_state("domcontentloaded", timeout=15000)
        except PlaywrightTimeoutError:
            pass

        if args.query:
            # We only fill an ordinary Lens refinement field when one is exposed.
            # No hidden/internal endpoint is used.
            for selector in (
                'textarea[aria-label*="Adicionar à pesquisa" i]',
                'input[aria-label*="Adicionar à pesquisa" i]',
                'textarea[aria-label*="Add to your search" i]',
                'input[aria-label*="Add to your search" i]',
            ):
                field = page.locator(selector).first
                try:
                    if field.count() and field.is_visible():
                        field.fill(args.query)
                        field.press("Enter")
                        break
                except Exception:
                    continue

        page.wait_for_timeout(max(0, int(args.wait_seconds * 1000)))

        links = extract_links(page)
        shopee_products = collect_shopee_products(links)

        found_ids = {f"{p['shop_id']}:{p['item_id']}" for p in shopee_products}

        payload = {
            "status": "V2_GOOGLE_LENS_DISCOVERY_EXPERIMENT",
            "image": str(image_path),
            "page_url": page.url,
            "upload_method": method,
            "query": args.query,
            "all_link_count": len(links),
            "shopee_product_count": len(shopee_products),
            "positive_found": sorted(POSITIVE_IDS & found_ids),
            "positive_missed": sorted(POSITIVE_IDS - found_ids),
            "shopee_products": shopee_products,
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
                "page_url": payload["page_url"],
                "shopee_product_count": payload["shopee_product_count"],
                "positive_found": payload["positive_found"],
                "positive_missed": payload["positive_missed"],
                "output": str(output),
            },
            ensure_ascii=False,
            indent=2,
        ))

        if args.keep_open:
            print("[LENS] browser left open for manual inspection.")
            try:
                while True:
                    time.sleep(1)
            except KeyboardInterrupt:
                pass

        browser.close()


if __name__ == "__main__":
    main()
