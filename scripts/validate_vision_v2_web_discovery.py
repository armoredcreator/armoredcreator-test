from __future__ import annotations

import argparse
import json
import re
import time
from pathlib import Path
from urllib.parse import urljoin

try:
    from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError
except ImportError as exc:  # pragma: no cover
    raise SystemExit(
        "Playwright nao instalado. No venv do teste: "
        "python -m pip install playwright && python -m playwright install chromium"
    ) from exc


ROOT = Path(__file__).resolve().parents[1]

REFERENCE_URL = "https://shopee.com.br/product/382998202/23298215147"
KNOWN_POSITIVE = {
    "1609734117:22794532266",
    "329536801:28939276497",
}
KNOWN_NEGATIVE = {
    "375188138:22197711605",
    "375188138:22498014344",
    "375188138:23198009505",
    "1168408423:22192826116",
    "1262524556:21299262872",
}


def product_key(url: str) -> str | None:
    match = re.search(r"/product/(\d+)/(\d+)", url)
    return f"{match.group(1)}:{match.group(2)}" if match else None


def clean(text: str | None) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def pause(seconds: float, reason: str) -> None:
    if seconds <= 0:
        return
    print(f"[WEB-DISCOVERY] pause {seconds:.1f}s: {reason}", flush=True)
    time.sleep(seconds)


def extract_page(page, url: str, delay: float = 0.0, label: str = "") -> dict:
    if label:
        print(f"[WEB-DISCOVERY] opening {label}: {url}", flush=True)
    page.goto(url, wait_until="domcontentloaded", timeout=30000)
    pause(delay, "DOM carregado; visualizacao da pagina")
    try:
        page.wait_for_load_state("networkidle", timeout=10000)
    except PlaywrightTimeoutError:
        pass
    pause(delay, "pagina estabilizada; antes da extracao")

    title = clean(page.title())
    h1 = clean(page.locator("h1").first.inner_text(timeout=3000)) if page.locator("h1").count() else ""

    description = ""
    selectors = [
        '[data-testid="pdp-description"]',
        ".product-detail",
        '[class*="description"]',
    ]
    for selector in selectors:
        try:
            node = page.locator(selector).first
            if node.count():
                text = clean(node.inner_text(timeout=2000))
                if len(text) > len(description):
                    description = text
        except Exception:
            pass

    images: list[str] = []
    for img in page.locator("img").all():
        try:
            src = img.get_attribute("src") or img.get_attribute("data-src")
            if src and "shopee" in src.lower():
                images.append(src)
        except Exception:
            pass

    links: list[dict] = []
    for anchor in page.locator("a").all():
        try:
            href = anchor.get_attribute("href") or ""
            absolute = urljoin(url, href)
            key = product_key(absolute)
            if key:
                links.append({
                    "key": key,
                    "url": absolute.split("?")[0],
                    "text": clean(anchor.inner_text(timeout=1000)),
                })
        except Exception:
            pass

    unique_links = {}
    for item in links:
        unique_links.setdefault(item["key"], item)

    return {
        "url": url,
        "key": product_key(url),
        "title": title,
        "h1": h1,
        "description": description[:12000],
        "images": list(dict.fromkeys(images))[:30],
        "product_links": list(unique_links.values()),
    }


def discover_from_search(
    page, query: str, pages: int, max_candidates: int, delay: float = 0.0
) -> list[dict]:
    candidates: dict[str, dict] = {}

    for page_number in range(0, pages):
        search_url = (
            "https://shopee.com.br/search?"
            f"keyword={query.replace(' ', '%20')}&page={page_number}"
        )
        print(f"[WEB-DISCOVERY] search page={page_number + 1}: {query!r}", flush=True)
        try:
            page.goto(search_url, wait_until="domcontentloaded", timeout=30000)
            pause(delay, f"busca page={page_number + 1} apos DOM")
            try:
                page.wait_for_load_state("networkidle", timeout=8000)
            except PlaywrightTimeoutError:
                pass
            pause(delay, f"busca page={page_number + 1} estabilizada")
        except Exception as exc:
            print(f"[WEB-DISCOVERY] search failed: {exc}", flush=True)
            continue

        found_on_page = 0
        for anchor in page.locator("a").all():
            try:
                href = anchor.get_attribute("href") or ""
                absolute = urljoin(search_url, href)
                key = product_key(absolute)
                if not key:
                    continue
                if key not in candidates:
                    found_on_page += 1
                candidates.setdefault(key, {
                    "key": key,
                    "url": absolute.split("?")[0],
                    "search_text": clean(anchor.inner_text(timeout=500)),
                    "search_query": query,
                    "search_page": page_number + 1,
                })
            except Exception:
                pass

        print(
            f"[WEB-DISCOVERY] page={page_number + 1}: "
            f"{found_on_page} novos candidatos; total={len(candidates)}",
            flush=True,
        )
        pause(delay, f"antes da proxima busca")

        if len(candidates) >= max_candidates:
            break

    return list(candidates.values())[:max_candidates]


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Isolated real-browser Shopee discovery experiment. "
        "Does not import ArmoredVision V2 discovery/reconciler."
    )
    parser.add_argument("--url", default=REFERENCE_URL)
    parser.add_argument("--query", default="bancada suspensa barbearia cabeleireiro 90cm gaveta")
    parser.add_argument("--pages", type=int, default=3)
    parser.add_argument("--max-candidates", type=int, default=60)
    parser.add_argument("--headed", action="store_true")
    parser.add_argument(
        "--slow",
        action="store_true",
        help="modo visual: pausa apos navegacao e entre paginas/candidatos",
    )
    parser.add_argument(
        "--delay-seconds",
        type=float,
        default=4.0,
        help="segundos de pausa usados com --slow (default: 4)",
    )
    parser.add_argument(
        "--keep-open",
        action="store_true",
        help="mantem o navegador aberto no final ate ENTER",
    )
    parser.add_argument("--output", default="storage/vision_v2_web_discovery.json")
    args = parser.parse_args()

    delay = max(0.0, args.delay_seconds) if args.slow else 0.0
    if args.slow and not args.headed:
        print("[WEB-DISCOVERY] --slow requer navegador visivel; ativando --headed", flush=True)
        args.headed = True

    started = time.monotonic()
    print("[WEB-DISCOVERY] isolated experiment", flush=True)
    print(f"[WEB-DISCOVERY] reference: {args.url}", flush=True)
    print("[WEB-DISCOVERY] imports from V2: NONE", flush=True)
    print(
        f"[WEB-DISCOVERY] visual mode: {'ON' if args.slow else 'OFF'} "
        f"(delay={delay:.1f}s)",
        flush=True,
    )

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=not args.headed)
        context = browser.new_context(
            locale="pt-BR",
            viewport={"width": 1440, "height": 1000},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/140.0.0.0 Safari/537.36"
            ),
        )
        page = context.new_page()

        reference = extract_page(
            page, args.url, delay=delay, label="reference"
        )
        candidates = discover_from_search(
            page, args.query, args.pages, args.max_candidates, delay=delay
        )

        hydrated = []
        for index, candidate in enumerate(candidates, 1):
            print(
                f"[WEB-DISCOVERY] candidate {index}/{len(candidates)}: {candidate['key']}",
                flush=True,
            )
            pause(delay, f"antes do candidato {index}")
            try:
                hydrated.append(
                    extract_page(
                        page,
                        candidate["url"],
                        delay=delay,
                        label=f"candidate {index}/{len(candidates)}",
                    )
                )
            except Exception as exc:
                candidate["page_error"] = str(exc)
                hydrated.append(candidate)

        if args.keep_open:
            print(
                "[WEB-DISCOVERY] navegador mantido aberto. "
                "Inspecione visualmente e pressione ENTER para finalizar.",
                flush=True,
            )
            input()

        browser.close()

    discovered_keys = {x.get("key") for x in hydrated if x.get("key")}
    positives = sorted(KNOWN_POSITIVE & discovered_keys)
    negatives = sorted(KNOWN_NEGATIVE & discovered_keys)

    payload = {
        "status": "V2_WEB_DISCOVERY_EXPERIMENT",
        "production_changes": False,
        "uses_existing_v2_discovery": False,
        "uses_existing_v2_reconciler": False,
        "uses_v1": False,
        "reference": reference,
        "query": args.query,
        "pages": args.pages,
        "max_candidates": args.max_candidates,
        "slow": args.slow,
        "delay_seconds": delay,
        "keep_open": args.keep_open,
        "candidate_count": len(hydrated),
        "known_positive": {
            "expected": sorted(KNOWN_POSITIVE),
            "found": positives,
            "missed": sorted(KNOWN_POSITIVE - discovered_keys),
            "recall": round(len(positives) / len(KNOWN_POSITIVE), 4),
        },
        "known_negative": {
            "expected": sorted(KNOWN_NEGATIVE),
            "found": negatives,
        },
        "candidates": hydrated,
        "elapsed_seconds": round(time.monotonic() - started, 2),
    }

    output = ROOT / args.output
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    print(json.dumps({
        "status": payload["status"],
        "candidate_count": len(hydrated),
        "positive_found": positives,
        "positive_missed": sorted(KNOWN_POSITIVE - discovered_keys),
        "known_positive_recall": payload["known_positive"]["recall"],
        "negative_found": negatives,
        "output": str(output),
        "elapsed_seconds": payload["elapsed_seconds"],
    }, ensure_ascii=False, indent=2))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
