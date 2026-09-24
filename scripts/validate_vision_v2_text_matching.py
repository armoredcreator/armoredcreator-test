from __future__ import annotations

import argparse
import json
import math
import re
import time
from collections import Counter, defaultdict
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

from ArmoredVision.modules.v1.shopee_api import ShopeeAffiliateAPI
from ArmoredVision.modules.v1.shopee_resolver import resolve_short_url
from ArmoredVision.modules.v2.normalize import structural_compare, structural_facts
from ArmoredVision.modules.v2.reconcile import candidate_key
from ArmoredVision.modules.v2.shopee_search import ShopeeCandidateAPI


STOP = {
    "de", "da", "do", "das", "dos", "e", "com", "para", "por", "em",
    "um", "uma", "uns", "umas", "a", "o", "as", "os", "na", "no",
    "nas", "nos", "se", "sem", "mais", "ou", "tipo",
}


def log(message: str) -> None:
    print(f"[V2-TEXT] {message}", flush=True)


def normalize_tokens(text: str) -> list[str]:
    text = str(text or "").lower()
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return [t for t in text.split() if len(t) >= 2 and t not in STOP]


def tfidf_scores(query: str, documents: list[str]) -> list[float]:
    tokenized = [normalize_tokens(x) for x in documents]
    df = Counter()
    for toks in tokenized:
        for token in set(toks):
            df[token] += 1

    n = max(1, len(documents))
    idf = {token: math.log((1 + n) / (1 + freq)) + 1.0 for token, freq in df.items()}

    def vector(toks: list[str]) -> dict[str, float]:
        counts = Counter(toks)
        if not counts:
            return {}
        total = len(toks)
        return {k: (v / total) * idf.get(k, 1.0) for k, v in counts.items()}

    qv = vector(normalize_tokens(query))
    qnorm = math.sqrt(sum(v * v for v in qv.values())) or 1.0
    scores = []
    for toks in tokenized:
        dv = vector(toks)
        dnorm = math.sqrt(sum(v * v for v in dv.values())) or 1.0
        dot = sum(qv.get(k, 0.0) * v for k, v in dv.items())
        scores.append(dot / (qnorm * dnorm))
    return scores


def jaccard(query: str, candidate: str) -> float:
    a, b = set(normalize_tokens(query)), set(normalize_tokens(candidate))
    return len(a & b) / len(a | b) if a and b else 0.0


def rare_overlap(query: str, candidate: str, documents: list[str]) -> float:
    q = set(normalize_tokens(query))
    c = set(normalize_tokens(candidate))
    if not q or not c:
        return 0.0
    df = Counter()
    for doc in documents:
        for token in set(normalize_tokens(doc)):
            df[token] += 1
    weights = {t: 1.0 / math.sqrt(df[t]) for t in q if t in c and df[t] > 0}
    denom = sum(1.0 / math.sqrt(df[t]) for t in q if df[t] > 0)
    return sum(weights.values()) / denom if denom else 0.0


def structural_signature(product: dict) -> list[str]:
    facts = structural_facts(str(product.get("productName") or ""))
    return [
        f"{key}={facts[key]}"
        for key in ("size_cm", "doors", "drawers", "niches", "basculhante", "ripado", "models")
        if facts.get(key) is not None
    ]


def structural_terms(product: dict) -> list[str]:
    facts = structural_facts(str(product.get("productName") or ""))
    terms = []
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
    parser = argparse.ArgumentParser(description="Experimento V2 de matching textual barato + estrutura.")
    parser.add_argument("--url", required=True)
    parser.add_argument("--keyword-pages", type=int, default=2)
    parser.add_argument("--pool-limit", type=int, default=120)
    parser.add_argument("--top", type=int, default=30)
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
    reference_key = candidate_key(original)
    terms = structural_terms(original)
    log(f"estrutura={structural_signature(original)}")
    log(f"termos estruturais={terms}")

    pool: dict[tuple[str, str], dict] = {}
    term_sets = [terms]
    if len(terms) >= 2:
        for index in range(len(terms)):
            reduced = [x for i, x in enumerate(terms) if i != index]
            if len(reduced) >= 2:
                term_sets.append(reduced)

    for n, term_set in enumerate(term_sets, 1):
        keyword = " ".join(term_set)
        log(f"busca {n}/{len(term_sets)}: {keyword!r}")
        for page in range(1, args.keyword_pages + 1):
            products = api.search_products(keyword, page=page, limit=50, sort_type=1)
            before = len(pool)
            for product in products:
                key = candidate_key(product)
                if key[0] and key[1] and key != reference_key:
                    pool.setdefault(key, product)
            log(f"  página {page}: {len(products)} | pool={len(pool)} (+{len(pool)-before})")

    products = list(pool.values())
    documents = [str(original.get("productName") or "")] + [
        str(p.get("productName") or "") for p in products
    ]
    scores = tfidf_scores(documents[0], documents[1:])

    rows = []
    for product, tfidf in zip(products, scores):
        name = str(product.get("productName") or "")
        matches, conflicts = structural_compare(original, product)
        jac = jaccard(str(original.get("productName") or ""), name)
        rare = rare_overlap(str(original.get("productName") or ""), name, documents)
        hard_reject = bool(conflicts)

        # Text-only ranking: TF-IDF is the primary signal; rare-token overlap
        # and Jaccard provide transparent secondary evidence.
        combined = 0.60 * tfidf + 0.25 * rare + 0.15 * jac
        if hard_reject:
            combined *= 0.15

        rows.append({
            "shop_id": str(product.get("shopId") or ""),
            "item_id": str(product.get("itemId") or ""),
            "product_name": name,
            "shop_name": product.get("shopName"),
            "tfidf": round(tfidf, 4),
            "rare_overlap": round(rare, 4),
            "jaccard": round(jac, 4),
            "combined": round(combined, 4),
            "structural_matches": matches,
            "structural_conflicts": conflicts,
            "decision": "HARD_REJECT" if hard_reject else "SURVIVES_TEXT",
            "structural_signature": structural_signature(product),
        })

    rows.sort(key=lambda r: (-r["combined"], r["shop_id"], r["item_id"]))
    survivors = [r for r in rows if r["decision"] == "SURVIVES_TEXT"]

    payload = {
        "status": "EXPERIMENTAL_TEXT_MATCHING",
        "elapsed_seconds": round(time.monotonic() - started, 2),
        "original": {
            "shop_id": resolved.shop_id,
            "item_id": resolved.item_id,
            "product_name": original.get("productName"),
            "structural_signature": structural_signature(original),
            "structural_terms": terms,
        },
        "pool_size": len(products),
        "survivors_without_structural_conflict": len(survivors),
        "top": rows[:args.top],
        "top_survivors": survivors[:args.top],
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
