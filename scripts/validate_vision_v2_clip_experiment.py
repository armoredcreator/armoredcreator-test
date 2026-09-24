from __future__ import annotations

import argparse
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

from ArmoredVision.modules.v1.shopee_api import ShopeeAffiliateAPI
from ArmoredVision.modules.v1.shopee_resolver import resolve_short_url
from ArmoredVision.modules.v2.normalize import structural_facts
from ArmoredVision.modules.v2.reconcile import CandidateReconciler, candidate_key
from ArmoredVision.modules.v2.shopee_search import ShopeeCandidateAPI
from ArmoredVision.modules.v2.visual_identity import CLIPVisualScorer


def log(message: str) -> None:
    print(f"[V2-CLIP] {message}", flush=True)


def signature(product: dict) -> list[str]:
    facts = structural_facts(str(product.get("productName") or ""))
    return [
        f"{key}={facts[key]}"
        for key in ("size_cm", "doors", "drawers", "niches", "basculhante", "ripado", "models")
        if facts.get(key) is not None
    ]


def terms(product: dict) -> list[str]:
    facts = structural_facts(str(product.get("productName") or ""))
    values: list[str] = []
    size = facts.get("size_cm")
    if size is not None:
        values.append(f"{size:g}cm")
    if facts.get("drawers") is not None:
        values.append("gaveta" if facts["drawers"] == 1 else f"{int(facts['drawers'])} gaveta")
    if facts.get("doors") is not None:
        values.append("porta" if facts["doors"] == 1 else f"{int(facts['doors'])} porta")
    return list(dict.fromkeys(values))


def discover(api: ShopeeCandidateAPI, original: dict, *, pages: int, pool_limit: int) -> dict[tuple[str, str], dict]:
    reference_key = candidate_key(original)
    structural_terms = terms(original)
    pool: dict[tuple[str, str], dict] = {}
    term_sets = [structural_terms]
    if len(structural_terms) >= 2:
        for index in range(len(structural_terms)):
            reduced = [x for i, x in enumerate(structural_terms) if i != index]
            if len(reduced) >= 2:
                term_sets.append(reduced)

    for family in term_sets:
        keyword = " ".join(family).strip()
        for page in range(1, pages + 1):
            for product in api.search_products(keyword, page=page, limit=50, sort_type=1):
                key = candidate_key(product)
                if key[0] and key[1] and key != reference_key:
                    pool.setdefault(key, product)
            if len(pool) >= pool_limit:
                return pool
    return pool


def main() -> int:
    parser = argparse.ArgumentParser(description="Experimento V2: CLIP como evidência visual.")
    parser.add_argument("--url", required=True)
    parser.add_argument("--top", type=int, default=30)
    parser.add_argument("--pages", type=int, default=2)
    parser.add_argument("--pool-limit", type=int, default=120)
    parser.add_argument("--batch-size", type=int, default=8)
    args = parser.parse_args()

    for candidate in (ROOT / ".env", ROOT / "credentials" / "shopee" / "affiliate.env"):
        if candidate.exists():
            load_dotenv(candidate, override=False)

    started = time.monotonic()
    log("resolvendo original...")
    resolved = resolve_short_url(args.url)
    original = ShopeeAffiliateAPI().get_exact_product(resolved.shop_id, resolved.item_id)
    log(f"original: {resolved.shop_id}:{resolved.item_id} | {original.get('productName')}")

    api = ShopeeCandidateAPI()
    reconciler = CandidateReconciler()
    pool = discover(api, original, pages=args.pages, pool_limit=args.pool_limit)
    log(f"pool descoberta: {len(pool)} candidatos")

    products = list(pool.values())
    scorer = CLIPVisualScorer()
    batch_size = max(1, int(args.batch_size))
    clip_scores: list[float | None] = []

    for start in range(0, len(products), batch_size):
        batch = products[start:start + batch_size]
        scores = scorer.score_batch(
            str(original.get("imageUrl") or ""),
            [str(product.get("imageUrl") or "") for product in batch],
        )
        clip_scores.extend(scores)
        log(f"CLIP {min(start + batch_size, len(products))}/{len(products)}")

    rows = []
    for product, clip_score in zip(products, clip_scores):
        valid, reconciler_score, reason, evidence = reconciler.compare(original, product)
        rows.append({
            "shop_id": str(product.get("shopId") or ""),
            "item_id": str(product.get("itemId") or ""),
            "product_name": product.get("productName"),
            "shop_name": product.get("shopName"),
            "clip_score": None if clip_score is None else round(float(clip_score), 4),
            "dhash_score": evidence.get("image_score"),
            "reconciler_score": round(float(reconciler_score), 4),
            "decision_current": "ACCEPTED" if valid else "REJECTED",
            "reason_current": reason,
            "structural_signature": signature(product),
            "structural_conflicts": evidence.get("structural_conflicts"),
            "structural_matches": evidence.get("structural_matches"),
            "name_score": evidence.get("name_score"),
        })

    rows.sort(key=lambda row: (row["clip_score"] is None, -(row["clip_score"] or -1.0)))
    payload = {
        "status": "EXPERIMENTAL_CLIP",
        "elapsed_seconds": round(time.monotonic() - started, 2),
        "original": {
            "shop_id": resolved.shop_id,
            "item_id": resolved.item_id,
            "product_name": original.get("productName"),
            "structural_signature": signature(original),
        },
        "pool_size": len(products),
        "clip_scorable": sum(row["clip_score"] is not None for row in rows),
        "top": rows[:max(1, int(args.top))],
        "note": "CLIP é evidência experimental; não altera a decisão do reconciliador nesta etapa.",
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
