"""Isolated Google Lens browser discovery experiment.

Uses the normal Google Lens web UI in Playwright Chromium. No Lens API, no
Shopee login, and no CAPTCHA/verification bypass.

The important invariant is that an upload is NOT considered successful merely
because an input[type=file] accepted bytes. We wait for an actual Lens result
state / URL before extracting links.

Usage:
    python scripts/validate_vision_v2_lens_discovery.py --image "C:\\path\\produto.jfif" --headed --keep-open
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
POSITIVE_IDS = {"1609734117:22794532266", "329536801:28939276497"}

LENS_URL_MARKERS = (
    "/searchbyimage",
    "/search",
    "lens.google.",
)
LENS_TEXT_MARKERS = (
    "resultados visuais",
    "visual matches",
    "correspondências visuais",
    "visualizações semelhantes",
    "visual matches for",
    "adicionar à pesquisa",
    "add to your search",
    "produtos",
    "products",
)


def clean_text(value: str | None) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def is_shopee_url(url: str) -> bool:
    return (urlparse(url).hostname or "").lower().endswith("shopee.com.br")


def extract_links(page):
    results, seen = [], set()
    for anchor in page.locator("a[href]").all():
        href = anchor.get_attribute("href") or ""
        if not href:
            continue
        href = urljoin(page.url, href)
        if not href.startswith(("http://", "https://")):
            continue
        key = href.split("#", 1)[0]
        if key in seen:
            continue
        seen.add(key)
        try:
            text = clean_text(anchor.inner_text(timeout=500))
        except Exception:
            text = clean_text(anchor.get_attribute("aria-label") or anchor.get_attribute("title"))
        match = PRODUCT_RE.search(key)
        results.append({
            "url": key,
            "text": text[:500],
            "is_shopee": is_shopee_url(key),
            "product_id": f"{match.group(1)}:{match.group(2)}" if match else None,
        })
    return results


def click_lens_control(page):
    selectors = (
        'div[aria-label*="Pesquisar por imagem" i]',
        'button[aria-label*="Pesquisar por imagem" i]',
        'div[aria-label*="Search by image" i]',
        'button[aria-label*="Search by image" i]',
        'div[aria-label*="Google Lens" i]',
        'button[aria-label*="Google Lens" i]',
        'div[aria-label*="Lens" i]',
        'button[aria-label*="Lens" i]',
    )
    for selector in selectors:
        loc = page.locator(selector).first
        try:
            if loc.count() and loc.is_visible():
                loc.click()
                return selector
        except Exception:
            pass
    return None


def lens_state(page):
    url = (page.url or "").lower()
    try:
        body = clean_text(page.locator("body").inner_text(timeout=1000)).lower()
    except Exception:
        body = ""
    url_hit = any(marker in url for marker in LENS_URL_MARKERS)
    text_hit = any(marker in body for marker in LENS_TEXT_MARKERS)
    return url_hit or text_hit, url, body[:2000]


def wait_for_lens_results(page, timeout_ms):
    deadline = time.monotonic() + timeout_ms / 1000
    while time.monotonic() < deadline:
        ok, url, body = lens_state(page)
        if ok:
            return True, url, body
        page.wait_for_timeout(500)
    ok, url, body = lens_state(page)
    return ok, url, body


def upload_image(page, image_path: Path):
    # Prefer opening Lens first. A file input on google.com alone is not proof
    # that the Lens workflow has started.
    clicked = click_lens_control(page)

    if clicked:
        print(f"[LENS] opened Lens control: {clicked}")
        page.wait_for_timeout(500)

    file_inputs = page.locator('input[type="file"]')
    if not file_inputs.count():
        raise RuntimeError(
            "O Google não expôs input de upload após abrir o Lens. "
            "Use --headed para inspecionar a interface."
        )

    file_inputs.first.set_input_files(str(image_path))
    print("[LENS] image bytes supplied to browser file input")

    # The navigation/result state is the real success criterion.
    ok, url, _ = wait_for_lens_results(page, 15000)
    if not ok:
        raise RuntimeError(
            "A imagem foi aceita pelo input, mas o Google não abriu uma página "
            f"de resultados do Lens. URL atual: {url}. "
            "Não vamos prosseguir fingindo que a descoberta funcionou."
        )
    return "lens-web-ui"


def collect_shopee_products(links):
    products = {}
    for entry in links:
        match = PRODUCT_RE.search(entry["url"])
        if not match or not entry["is_shopee"]:
            continue
        shop_id, item_id = match.groups()
        key = f"{shop_id}:{item_id}"
        products.setdefault(key, {
            "shop_id": shop_id,
            "item_id": item_id,
            "product_link": entry["url"],
            "result_text": entry["text"],
        })
    return list(products.values())


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--image", required=True)
    parser.add_argument("--query", default="")
    parser.add_argument("--headed", action="store_true")
    parser.add_argument("--keep-open", action="store_true")
    parser.add_argument("--wait-seconds", type=float, default=5.0)
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    args = parser.parse_args()

    image_path = Path(args.image).expanduser().resolve()
    if not image_path.is_file():
        raise FileNotFoundError(image_path)

    print("[LENS] isolated browser experiment")
    print("[LENS] Google Lens web UI — no Lens API")
    print("[LENS] no Shopee login")
    print("[LENS] no CAPTCHA/verification bypass")
    print(f"[LENS] image: {image_path}")

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=not args.headed)
        page = browser.new_page(viewport={"width": 1440, "height": 1000}, locale="pt-BR")
        page.goto("https://www.google.com/", wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(1500)

        method = upload_image(page, image_path)
        print(f"[LENS] workflow confirmed: {method}")

        if args.query:
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
                    pass

        page.wait_for_timeout(max(0, int(args.wait_seconds * 1000)))
        links = extract_links(page)
        products = collect_shopee_products(links)
        found = {f"{p['shop_id']}:{p['item_id']}" for p in products}

        payload = {
            "status": "V2_GOOGLE_LENS_DISCOVERY_EXPERIMENT",
            "image": str(image_path),
            "page_url": page.url,
            "upload_method": method,
            "query": args.query,
            "all_link_count": len(links),
            "shopee_product_count": len(products),
            "positive_found": sorted(POSITIVE_IDS & found),
            "positive_missed": sorted(POSITIVE_IDS - found),
            "shopee_products": products,
        }

        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

        print(json.dumps({
            "status": payload["status"],
            "page_url": payload["page_url"],
            "shopee_product_count": payload["shopee_product_count"],
            "positive_found": payload["positive_found"],
            "positive_missed": payload["positive_missed"],
            "output": str(output),
        }, ensure_ascii=False, indent=2))

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
