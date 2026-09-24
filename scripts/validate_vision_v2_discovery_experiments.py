from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

from ArmoredVision.modules.v1.shopee_api import ShopeeAffiliateAPI
from ArmoredVision.modules.v1.shopee_resolver import resolve_short_url
from ArmoredVision.modules.v2.normalize import structural_facts
from ArmoredVision.modules.v2.reconcile import CandidateReconciler, candidate_key
from ArmoredVision.modules.v2.shopee_search import ShopeeCandidateAPI


def _structural_signature(product: dict) -> list[str]:
    facts = structural_facts(str(product.get("productName") or ""))
    result = []
    for key in ("size_cm", "doors", "drawers", "niches", "basculhante", "ripado", "models"):
        value = facts.get(key)
        if value is not None:
            result.append(f"{key}={value}")
    return result


def _structural_terms(product: dict) -> list[str]:
    facts = structural_facts(str(product.get("productName") or ""))
    terms: list[str] = []
    size = facts.get("size_cm")
    if size is not None:
        terms.append(f"{size:g}cm")
    if facts.get("drawers") is not None:
        terms.append(f"{int(facts['drawers'])} gaveta" if facts["drawers"] != 1 else "gaveta")
    if facts.get("doors") is not None:
        terms.append(f"{int(facts['doors'])} porta" if facts["doors"] != 1 else "porta")
    if facts.get("niches") is not None:
        terms.append("nicho" if facts["niches"] else "sem nicho")
    if facts.get("basculhante") is not None:
        terms.append("basculhante" if facts["basculhante"] else "sem basculhante")
    if facts.get("ripado") is not None:
        terms.append("ripado" if facts["ripado"] else "sem ripado")
    return list(dict.fromkeys(terms))


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Experimento V2: descoberta por identidade estrutural e visual, sem alterar o pipeline."
    )
    parser.add_argument("--url", required=True, help="URL Shopee original, inclusive s.shopee.com.br")
    parser.add_argument("--category-pages", type=int, default=6)
    parser.add_argument("--keyword-pages", type=int, default=3)
    parser.add_argument("--pool-limit", type=int, default=400)
    parser.add_argument("--top-visual", type=int, default=40)
    args = parser.parse_args()

    for candidate in (
        ROOT / ".env",
        ROOT / "credentials" / "shopee" / "affiliate.env",
    ):
        if candidate.exists():
            load_dotenv(candidate, override=False)

    resolved = resolve_short_url(args.url)
    original = ShopeeAffiliateAPI().get_exact_product(resolved.shop_id, resolved.item_id)
    api = ShopeeCandidateAPI()
    reconciler = CandidateReconciler()

    reference_key = candidate_key(original)
    reference_cats = [str(x) for x in (original.get("productCatIds") or []) if str(x)]
    structural_terms = _structural_terms(original)

    pools: dict[str, dict[tuple[str, str], dict]] = {
        "STRUCTURAL_ONLY": {},
        "CATEGORY_DEEP": {},
    }

    # EXPERIMENTO A: queries compostas somente pelos atributos estruturais.
    # Não usa o título original nem palavras comerciais específicas.
    term_sets = [structural_terms]
    if len(structural_terms) >= 2:
        for index in range(len(structural_terms)):
            reduced = [x for i, x in enumerate(structural_terms) if i != index]
            if len(reduced) >= 2:
                term_sets.append(reduced)
    for terms in term_sets:
        keyword = " ".join(terms).strip()
        if not keyword:
            continue
        for page in range(1, args.keyword_pages + 1):
            for product in api.search_products(keyword, page=page, limit=50, sort_type=1):
                key = candidate_key(product)
                if key[0] and key[1] and key != reference_key:
                    pools["STRUCTURAL_ONLY"].setdefault(key, product)

    # EXPERIMENTO B: varredura mais profunda da categoria, sem keyword.
    # O V2 atual consulta somente duas páginas; aqui a categoria vira o pool primário.
    for category_id in reference_cats:
        for page in range(1, args.category_pages + 1):
            for product in api.search_category_products(category_id, page=page, limit=50):
                key = candidate_key(product)
                if key[0] and key[1] and key != reference_key:
                    pools["CATEGORY_DEEP"].setdefault(key, product)
                if len(pools["CATEGORY_DEEP"]) >= args.pool_limit:
                    break
            if len(pools["CATEGORY_DEEP"]) >= args.pool_limit:
                break
        if len(pools["CATEGORY_DEEP"]) >= args.pool_limit:
            break

    # EXPERIMENTO C: a imagem deixa de ser apenas validadora.
    # Ela passa a ordenar o pool amplo antes da decisão textual/estrutural.
    visual_pool = {}
    visual_pool.update(pools["CATEGORY_DEEP"])
    visual_pool.update(pools["STRUCTURAL_ONLY"])

    visual_rank = []
    for product in visual_pool.values():
        score = reconciler.image_scorer(
            str(original.get("imageUrl") or ""),
            str(product.get("imageUrl") or ""),
        )
        if score is not None:
            visual_rank.append((float(score), product))

    visual_rank.sort(
        key=lambda row: (
            -row[0],
            str(row[1].get("shopId") or ""),
            str(row[1].get("itemId") or ""),
        )
    )

    results = []
    for score, product in visual_rank[: args.top_visual]:
        valid, final_score, reason, evidence = reconciler.compare(original, product)
        results.append(
            {
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
            }
        )

    accepted = [row for row in results if row["decision"] == "ACCEPTED"]
    payload = {
        "status": "EXPERIMENTAL_DISCOVERY",
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
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
