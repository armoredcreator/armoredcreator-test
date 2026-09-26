from __future__ import annotations

import argparse
import json
import math
import re
import sys
import time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

from ArmoredVision.modules.v1.shopee_api import ShopeeAffiliateAPI
from ArmoredVision.modules.v1.shopee_resolver import resolve_short_url
from ArmoredVision.modules.v2.normalize import structural_compare, structural_facts
from ArmoredVision.modules.v2.reconcile import CandidateReconciler, candidate_key
from ArmoredVision.modules.v2.shopee_search import ShopeeCandidateAPI


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

STOP = {
    "de", "da", "do", "das", "dos", "e", "com", "para", "por", "em",
    "um", "uma", "uns", "umas", "a", "o", "as", "os", "na", "no",
    "nas", "nos", "se", "sem", "mais", "ou", "tipo", "modelo",
}


def log(message: str) -> None:
    print(f"[V2-CLOSED-BENCH] {message}", flush=True)


def normalize_tokens(text: str) -> list[str]:
    value = re.sub(r"[^a-z0-9]+", " ", str(text or "").lower())
    return [x for x in value.split() if len(x) >= 2 and x not in STOP]


def phrase_tokens(text: str, n: int = 2) -> set[str]:
    toks = normalize_tokens(text)
    return {" ".join(toks[i:i+n]) for i in range(len(toks) - n + 1)}


def discriminative_score(original: str, candidate: str, documents: list[str]) -> float:
    q = set(normalize_tokens(original))
    c = set(normalize_tokens(candidate))
    if not q or not c:
        return 0.0

    df = Counter()
    for doc in documents:
        for token in set(normalize_tokens(doc)):
            df[token] += 1

    def weight(token: str) -> float:
        return math.log((1 + len(documents)) / (1 + df[token])) + 1.0

    denom = sum(weight(token) for token in q) or 1.0
    token_score = sum(weight(token) for token in q if token in c) / denom

    q2 = phrase_tokens(original, 2)
    c2 = phrase_tokens(candidate, 2)
    phrase_score = len(q2 & c2) / max(1, len(q2))

    matches, conflicts = structural_compare(
        {"productName": original},
        {"productName": candidate},
    )
    if conflicts:
        return 0.0

    structure_score = min(1.0, len(matches) / 3.0)
    return 0.55 * token_score + 0.25 * phrase_score + 0.20 * structure_score


def prototype_decision(original: dict, candidate: dict, documents: list[str]) -> tuple[str, float, list[str], list[str]]:
    original_name = str(original.get("productName") or "")
    candidate_name = str(candidate.get("productName") or "")
    matches, conflicts = structural_compare(original, candidate)
    score = discriminative_score(original_name, candidate_name, documents)
    accepted = not conflicts and score >= 0.62 and len(matches) >= 2
    return ("ACCEPT" if accepted else "REJECT", score, matches, conflicts)


def evaluate(rows: list[dict], decision_field: str) -> dict:
    tp = sum(1 for r in rows if r["label"] == 1 and r[decision_field] == "ACCEPT")
    fp = sum(1 for r in rows if r["label"] == 0 and r[decision_field] == "ACCEPT")
    fn = sum(1 for r in rows if r["label"] == 1 and r[decision_field] != "ACCEPT")
    tn = sum(1 for r in rows if r["label"] == 0 and r[decision_field] != "ACCEPT")
    precision = tp / (tp + fp) if tp + fp else None
    recall = tp / (tp + fn) if tp + fn else None
    return {
        "labeled": len(rows),
        "positive": sum(1 for r in rows if r["label"] == 1),
        "negative": sum(1 for r in rows if r["label"] == 0),
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "tn": tn,
        "precision": None if precision is None else round(precision, 4),
        "recall": None if recall is None else round(recall, 4),
    }


def discover(original: dict, args: argparse.Namespace) -> list[dict]:
    api = ShopeeCandidateAPI()
    ref = candidate_key(original)
    facts = structural_facts(str(original.get("productName") or ""))

    terms: list[str] = []
    if facts.get("size_cm") is not None:
        terms.append(f"{facts['size_cm']:g}cm")
    if facts.get("drawers") is not None:
        drawers = int(facts["drawers"])
        terms.append("gaveta" if drawers == 1 else f"{drawers} gavetas")
    if facts.get("doors") is not None:
        doors = int(facts["doors"])
        terms.append("porta" if doors == 1 else f"{doors} portas")

    if len(terms) < 2:
        raise RuntimeError(f"Assinatura insuficiente para discovery: {terms}")

    keyword_sets = [terms]
    for i in range(len(terms)):
        reduced = [x for j, x in enumerate(terms) if i != j]
        if len(reduced) >= 2:
            keyword_sets.append(reduced)

    pool: dict[tuple[str, str], dict] = {}
    for parts in keyword_sets:
        keyword = " ".join(parts)
        log(f"discovery: {keyword!r}")
        for page in range(1, args.keyword_pages + 1):
            for product in api.search_products(keyword, page=page, limit=50, sort_type=1):
                key = candidate_key(product)
                if key[0] and key[1] and key != ref:
                    pool.setdefault(key, product)
                if len(pool) >= args.pool_limit:
                    return list(pool.values())[:args.pool_limit]
    return list(pool.values())[:args.pool_limit]


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Closed V2 identity benchmark: exact labeled cases + separate discovery recall."
    )
    parser.add_argument("--url", required=True)
    parser.add_argument("--keyword-pages", type=int, default=2)
    parser.add_argument("--pool-limit", type=int, default=120)
    args = parser.parse_args()

    for candidate in (
        ROOT / ".env",
        ROOT / "credentials" / "shopee" / "affiliate.env",
    ):
        if candidate.exists():
            load_dotenv(candidate, override=False)

    started = time.monotonic()
    log("resolvendo original...")
    resolved = resolve_short_url(args.url)
    v1 = ShopeeAffiliateAPI()
    original = v1.get_exact_product(resolved.shop_id, resolved.item_id)
    original_key = f"{resolved.shop_id}:{resolved.item_id}"
    log(f"original: {original_key} | {original.get('productName')}")

    api = ShopeeCandidateAPI()
    labeled_keys = sorted(KNOWN_POSITIVE | KNOWN_NEGATIVE)
    exact_products: dict[str, dict] = {}
    exact_errors: dict[str, str] = {}

    log("carregando casos rotulados diretamente por shop_id:item_id...")
    for key in labeled_keys:
        shop_id, item_id = key.split(":", 1)
        try:
            product = api.get_exact_product(shop_id, item_id)
            exact_products[key] = product
            log(f"exact OK: {key} | {product.get('productName')}")
        except Exception as exc:
            exact_errors[key] = str(exc)
            log(f"exact FAIL: {key} | {exc}")

    documents = [str(original.get("productName") or "")] + [
        str(p.get("productName") or "") for p in exact_products.values()
    ]

    reconciler = CandidateReconciler(image_scorer=lambda _a, _b: None)
    rows = []
    for key in labeled_keys:
        if key not in exact_products:
            continue
        product = exact_products[key]
        prototype, score, matches, conflicts = prototype_decision(
            original, product, documents
        )
        baseline, baseline_score, baseline_reason, baseline_evidence = reconciler.compare(
            original, product
        )
        rows.append({
            "key": key,
            "label": 1 if key in KNOWN_POSITIVE else 0,
            "label_name": "SAME_PRODUCT" if key in KNOWN_POSITIVE else "DIFFERENT_PRODUCT",
            "product_name": product.get("productName"),
            "prototype_score": round(score, 4),
            "prototype_decision": prototype,
            "structural_matches": matches,
            "structural_conflicts": conflicts,
            "baseline_score": round(baseline_score, 4),
            "baseline_decision": "ACCEPT" if baseline else "REJECT",
            "baseline_reason": baseline_reason,
            "baseline_evidence": baseline_evidence,
        })

    closed_eval = {
        "identity_prototype": evaluate(rows, "prototype_decision"),
        "existing_reconciler_baseline": evaluate(rows, "baseline_decision"),
    }

    log("executando discovery separadamente...")
    pool = discover(original, args)
    pool_keys = {f"{candidate_key(p)[0]}:{candidate_key(p)[1]}" for p in pool}
    discovery_hits = {
        key: key in pool_keys
        for key in labeled_keys
    }
    discovery_recall = (
        sum(1 for key in KNOWN_POSITIVE if discovery_hits.get(key))
        / len(KNOWN_POSITIVE)
        if KNOWN_POSITIVE else None
    )

    payload = {
        "status": "V2_CLOSED_IDENTITY_BENCHMARK",
        "elapsed_seconds": round(time.monotonic() - started, 2),
        "production_changes": False,
        "original": {
            "key": original_key,
            "product_name": original.get("productName"),
            "structural_signature": {
                key: structural_facts(str(original.get("productName") or "")).get(key)
                for key in ("size_cm", "doors", "drawers", "niches", "basculhante", "ripado", "models")
            },
        },
        "labels": {
            "known_positive": sorted(KNOWN_POSITIVE),
            "known_negative": sorted(KNOWN_NEGATIVE),
            "note": "Positive cases are previously accepted real V2 cases; they are benchmark positives, not independent external ground truth.",
        },
        "closed_identity": {
            "exact_loaded": sorted(exact_products),
            "exact_errors": exact_errors,
            "evaluation": closed_eval,
            "cases": rows,
        },
        "discovery_recall": {
            "pool_size": len(pool),
            "positive_hits": [key for key in sorted(KNOWN_POSITIVE) if discovery_hits.get(key)],
            "positive_misses": [key for key in sorted(KNOWN_POSITIVE) if not discovery_hits.get(key)],
            "negative_hits": [key for key in sorted(KNOWN_NEGATIVE) if discovery_hits.get(key)],
            "recall_known_positive": None if discovery_recall is None else round(discovery_recall, 4),
        },
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
