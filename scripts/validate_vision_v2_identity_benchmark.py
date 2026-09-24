from __future__ import annotations

import argparse
import json
import math
import re
import time
from collections import Counter
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

from ArmoredVision.modules.v1.shopee_api import ShopeeAffiliateAPI
from ArmoredVision.modules.v1.shopee_resolver import resolve_short_url
from ArmoredVision.modules.v2.normalize import structural_compare, structural_facts
from ArmoredVision.modules.v2.reconcile import CandidateReconciler, candidate_key
from ArmoredVision.modules.v2.shopee_search import ShopeeCandidateAPI


STOP = {
    "de", "da", "do", "das", "dos", "e", "com", "para", "por", "em",
    "um", "uma", "uns", "umas", "a", "o", "as", "os", "na", "no",
    "nas", "nos", "se", "sem", "mais", "ou", "tipo", "modelo",
}


def log(message: str) -> None:
    print(f"[V2-BENCH] {message}", flush=True)


def normalize_tokens(text: str) -> list[str]:
    value = re.sub(r"[^a-z0-9]+", " ", str(text or "").lower())
    return [x for x in value.split() if len(x) >= 2 and x not in STOP]


def signature(product: dict) -> dict[str, object]:
    facts = structural_facts(str(product.get("productName") or ""))
    return {
        key: facts.get(key)
        for key in ("size_cm", "doors", "drawers", "niches", "basculhante", "ripado", "models")
    }


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

    token_weight = {
        token: math.log((1 + len(documents)) / (1 + df[token])) + 1.0
        for token in q
        if token in c
    }
    denom = sum(
        math.log((1 + len(documents)) / (1 + df[token])) + 1.0
        for token in q
    ) or 1.0
    token_score = sum(token_weight.values()) / denom

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


def discover(original: dict, args: argparse.Namespace) -> list[dict]:
    api = ShopeeCandidateAPI()
    ref = candidate_key(original)
    terms = []

    facts = structural_facts(str(original.get("productName") or ""))
    size = facts.get("size_cm")
    drawers = facts.get("drawers")
    doors = facts.get("doors")
    if size is not None:
        terms.append(f"{size:g}cm")
    if drawers is not None:
        terms.append("gaveta" if drawers == 1 else f"{int(drawers)} gavetas")
    if doors is not None:
        terms.append("porta" if doors == 1 else f"{int(doors)} portas")

    if len(terms) < 2:
        raise RuntimeError(f"Assinatura insuficiente para benchmark: {terms}")

    pool: dict[tuple[str, str], dict] = {}
    keyword_sets = [terms]
    for i in range(len(terms)):
        reduced = [x for j, x in enumerate(terms) if i != j]
        if len(reduced) >= 2:
            keyword_sets.append(reduced)

    for keyword_parts in keyword_sets:
        keyword = " ".join(keyword_parts)
        log(f"discovery: {keyword!r}")
        for page in range(1, args.keyword_pages + 1):
            products = api.search_products(keyword, page=page, limit=50, sort_type=1)
            for product in products:
                key = candidate_key(product)
                if key[0] and key[1] and key != ref:
                    pool.setdefault(key, product)
            if len(pool) >= args.pool_limit:
                return list(pool.values())[:args.pool_limit]
    return list(pool.values())[:args.pool_limit]


def evaluate_labeled(rows: list[dict], labels: dict[str, int], key_field: str) -> dict:
    labeled = [r for r in rows if r[key_field] in labels]
    positives = [r for r in labeled if labels[r[key_field]] == 1]
    negatives = [r for r in labeled if labels[r[key_field]] == 0]
    predicted = [r for r in labeled if r["decision"] == "ACCEPT"]

    tp = sum(1 for r in predicted if labels[r[key_field]] == 1)
    fp = sum(1 for r in predicted if labels[r[key_field]] == 0)
    fn = sum(1 for r in positives if r["decision"] != "ACCEPT")

    precision = tp / (tp + fp) if tp + fp else None
    recall = tp / (tp + fn) if tp + fn else None
    return {
        "labeled": len(labeled),
        "known_positive": len(positives),
        "known_negative": len(negatives),
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "precision": None if precision is None else round(precision, 4),
        "recall": None if recall is None else round(recall, 4),
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Benchmark real V2 product identity cases before any production integration."
    )
    parser.add_argument("--url", required=True)
    parser.add_argument("--keyword-pages", type=int, default=2)
    parser.add_argument("--pool-limit", type=int, default=120)
    parser.add_argument("--top", type=int, default=30)
    args = parser.parse_args()

    for candidate in (
        ROOT / ".env",
        ROOT / "credentials" / "shopee" / "affiliate.env",
    ):
        if candidate.exists():
            load_dotenv(candidate, override=False)

    started = time.monotonic()
    log("resolvendo produto original...")
    resolved = resolve_short_url(args.url)
    original = ShopeeAffiliateAPI().get_exact_product(resolved.shop_id, resolved.item_id)
    log(f"original: {resolved.shop_id}:{resolved.item_id} | {original.get('productName')}")

    products = discover(original, args)
    log(f"pool descoberta: {len(products)}")

    documents = [str(original.get("productName") or "")] + [
        str(p.get("productName") or "") for p in products
    ]
    reconciler = CandidateReconciler(
        image_scorer=lambda _a, _b: None,
    )

    rows = []
    for product in products:
        key = candidate_key(product)
        name = str(product.get("productName") or "")
        matches, conflicts = structural_compare(original, product)
        identity = discriminative_score(
            str(original.get("productName") or ""),
            name,
            documents,
        )
        accepted, existing_score, reason, evidence = reconciler.compare(original, product)
        # Existing reconciler is measured as a baseline only. The identity
        # prototype is deliberately conservative and does not use image data.
        prototype_accept = (
            not conflicts
            and identity >= 0.62
            and len(matches) >= 2
        )

        rows.append({
            "key": f"{key[0]}:{key[1]}",
            "shop_id": key[0],
            "item_id": key[1],
            "product_name": name,
            "structural_matches": matches,
            "structural_conflicts": conflicts,
            "identity_score": round(identity, 4),
            "prototype_decision": "ACCEPT" if prototype_accept else "REJECT",
            "baseline_decision": "ACCEPT" if accepted else "REJECT",
            "baseline_score": round(existing_score, 4),
            "baseline_reason": reason,
            "baseline_evidence": evidence,
        })

    rows.sort(key=lambda r: (-r["identity_score"], r["key"]))

    # Ground truth currently consists only of cases already proven in the
    # previous real V2 run. Negatives are intentionally obvious counterexamples
    # from the same discovery pool. More labels can be added without changing
    # the benchmark code.
    known_positive = {
        "1609734117:22794532266",
        "329536801:28939276497",
    }
    known_negative = {
        "375188138:22197711605",  # penteadeira
        "375188138:22498014344",  # cabeceira
        "375188138:23198009505",  # cabeceira
        "1168408423:22192826116", # mesa sem gaveta
        "1262524556:21299262872", # escrivaninha sem gaveta
    }
    labels = {key: 1 for key in known_positive}
    labels.update({key: 0 for key in known_negative})

    for row in rows:
        row["ground_truth"] = (
            "SAME_PRODUCT" if labels.get(row["key"]) == 1
            else "DIFFERENT_PRODUCT" if labels.get(row["key"]) == 0
            else "UNLABELED"
        )

    prototype_eval = evaluate_labeled(
        [{**r, "decision": r["prototype_decision"]} for r in rows],
        labels,
        "key",
    )
    baseline_eval = evaluate_labeled(
        [{**r, "decision": r["baseline_decision"]} for r in rows],
        labels,
        "key",
    )

    payload = {
        "status": "V2_IDENTITY_BENCHMARK",
        "elapsed_seconds": round(time.monotonic() - started, 2),
        "production_changes": False,
        "original": {
            "shop_id": resolved.shop_id,
            "item_id": resolved.item_id,
            "product_name": original.get("productName"),
            "structural_signature": signature(original),
        },
        "pool_size": len(products),
        "ground_truth": {
            "known_positive": sorted(known_positive),
            "known_negative": sorted(known_negative),
            "warning": "Only explicitly verified cases are labeled. UNLABELED is not treated as negative.",
        },
        "evaluation": {
            "identity_prototype": prototype_eval,
            "existing_reconciler_baseline": baseline_eval,
        },
        "top": rows[:args.top],
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
