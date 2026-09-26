from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

from ArmoredVision.modules.v1.shopee_api import ShopeeAffiliateAPI
from ArmoredVision.modules.v1.shopee_resolver import resolve_short_url
from ArmoredVision.modules.v2.normalize import structural_facts
from ArmoredVision.modules.v2.reconcile import CandidateReconciler, candidate_key
from ArmoredVision.modules.v2.shopee_search import ShopeeCandidateAPI


def log(message: str) -> None:
    print(f"[V2-EXP] {message}", flush=True)


def _structural_signature(product: dict) -> list[str]:
    facts = structural_facts(str(product.get("productName") or ""))
    return [
        f"{key}={facts[key]}"
        for key in ("size_cm", "doors", "drawers", "niches", "basculhante", "ripado", "models")
        if facts.get(key) is not None
    ]


def _structural_terms(product: dict) -> list[str]:
    facts = structural_facts(str(product.get("productName") or ""))
    terms: list[str] = []
    size = facts.get("size_cm")
    if size is not None:
        terms.append(f"{size:g}cm")
    if facts.get("drawers") is not None:
        terms.append("gaveta" if facts["drawers"] == 1 else f"{int(facts['drawers'])} gaveta")
    if facts.get("doors") is not None:
        terms.append("porta" if facts["doors"] == 1 else f"{int(facts['doors'])} porta")
    if facts.get("niches") is not None:
        terms.append("nicho" if facts["niches"] else "sem nicho")
    if facts.get("basculhante") is not None:
        terms.append("basculhante" if facts["basculhante"] else "sem basculante")
    if facts.get("ripado") is not None:
        terms.append("ripado" if facts["ripado"] else "sem ripado")
    return list(dict.fromkeys(terms))


def main() -> int:
    parser = argparse.ArgumentParser(description="Experimento V2 de descoberta estrutural e visual.")
    parser.add_argument("--url", required=True)
    parser.add_argument("--category-pages", type=int, default=3)
    parser.add_argument("--keyword-pages", type=int, default=2)
    parser.add_argument("--pool-limit", type=int, default=250)
    parser.add_argument("--top-visual", type=int, default=30)
    args = parser.parse_args()

    for candidate in (ROOT / ".env", ROOT / "credentials" / "shopee" / "affiliate.env"):
        if candidate.exists():
            load_dotenv(candidate, override=False)

    started = time.monotonic()
    log("resolvendo produto original...")
    resolved = resolve_short_url(args.url)
    original = ShopeeAffiliateAPI().get_exact_product(resolved.shop_id, resolved.item_id)
    log(f"original: {resolved.shop_id}:{resolved.item_id} | {original.get('productName')}")
    api = ShopeeCandidateAPI()
    reconciler = CandidateReconciler()

    reference_key = candidate_key(original)
    reference_cats = [str(x) for x in (original.get("productCatIds") or []) if str(x)]
    structural_terms = _structural_terms(original)
    log(f"categorias={reference_cats} | assinatura={_structural_signature(original)}")
    log(f"termos estruturais={structural_terms}")

    pools: dict[str, dict[tuple[str, str], dict]] = {
        "STRUCTURAL_ONLY": {},
        "CATEGORY_DEEP": {},
    }

    term_sets = [structural_terms]
    if len(structural_terms) >= 2:
        for index in range(len(structural_terms)):
            reduced = [x for i, x in enumerate(structural_terms) if i != index]
            if len(reduced) >= 2:
                term_sets.append(reduced)

    log(f"A: {len(term_sets)} famílias estruturais")
    for n, terms in enumerate(term_sets, 1):
        keyword = " ".join(terms).strip()
        log(f"A {n}/{len(term_sets)}: '{keyword}'")
        for page in range(1, args.keyword_pages + 1):
            products = api.search_products(keyword, page=page, limit=50, sort_type=1)
            before = len(pools["STRUCTURAL_ONLY"])
            for product in products:
                key = candidate_key(product)
                if key[0] and key[1] and key != reference_key:
                    pools["STRUCTURAL_ONLY"].setdefault(key, product)
            log(f"  página {page}: {len(products)} ofertas | pool={len(pools['STRUCTURAL_ONLY'])} (+{len(pools['STRUCTURAL_ONLY'])-before})")

    log(f"B: categoria sem keyword, {len(reference_cats)} categorias")
    for category_id in reference_cats:
        for page in range(1, args.category_pages + 1):
            products = api.search_category_products(category_id, page=page, limit=50)
            before = len(pools["CATEGORY_DEEP"])
            for product in products:
                key = candidate_key(product)
                if key[0] and key[1] and key != reference_key:
                    pools["CATEGORY_DEEP"].setdefault(key, product)
            log(f"  categoria={category_id} página={page}: {len(products)} ofertas | pool={len(pools['CATEGORY_DEEP'])} (+{len(pools['CATEGORY_DEEP'])-before})")
            if len(pools["CATEGORY_DEEP"]) >= args.pool_limit:
                break

    visual_pool = {}
    visual_pool.update(pools["CATEGORY_DEEP"])
    visual_pool.update(pools["STRUCTURAL_ONLY"])
    log(f"C: visual-first sobre {len(visual_pool)} candidatos")

    visual_rank = []
    products = list(visual_pool.values())
    workers = max(2, min(12, int(os.getenv("ARMORED_VISION_V2_IMAGE_WORKERS", "8"))))
    log(f"  comparação visual paralela: {workers} workers")

    def score_one(product: dict):
        score = reconciler.image_scorer(
            str(original.get("imageUrl") or ""),
            str(product.get("imageUrl") or ""),
        )
        return score, product

    completed = 0
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = [executor.submit(score_one, product) for product in products]
        for future in as_completed(futures):
            completed += 1
            score, product = future.result()
            if score is not None:
                visual_rank.append((float(score), product))
            if completed % 10 == 0 or completed == len(products):
                log(f"  imagem {completed}/{len(products)} | pontuáveis={len(visual_rank)}")

    visual_rank.sort(key=lambda row: (-row[0], str(row[1].get("shopId") or ""), str(row[1].get("itemId") or "")))
    results = []
    for score, product in visual_rank[:args.top_visual]:
        valid, final_score, reason, evidence = reconciler.compare(original, product)
        results.append({
            "shop_id": str(product.get("shopId") or ""),
            "item_id": str(product.get("itemId") or ""),
            "product_name": product.get("productName"),
            "shop_name": product.get("shopName"),
            "visual_probe": round(score, 4),
            "reconciler_score": round(final_score, 4),
            "decision": "ACCEPTED" if valid else "REJECTED",
            "reason": reason,
            "structural_signature": _structural_signature(product),
            "evidence": evidence,
        })

    accepted = [row for row in results if row["decision"] == "ACCEPTED"]
    payload = {
        "status": "EXPERIMENTAL_DISCOVERY",
        "elapsed_seconds": round(time.monotonic() - started, 2),
        "original": {
            "shop_id": resolved.shop_id,
            "item_id": resolved.item_id,
            "product_name": original.get("productName"),
            "categories": reference_cats,
            "structural_signature": _structural_signature(original),
            "structural_terms": structural_terms,
        },
        "pool_sizes": {
            "STRUCTURAL_ONLY": len(pools["STRUCTURAL_ONLY"]),
            "CATEGORY_DEEP": len(pools["CATEGORY_DEEP"]),
            "VISUAL_UNION": len(visual_pool),
            "VISUAL_SCORABLE": len(visual_rank),
        },
        "top_visual": results,
        "accepted_from_visual_first": len(accepted),
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
