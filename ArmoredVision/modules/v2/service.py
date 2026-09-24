from __future__ import annotations

import os
from collections import OrderedDict
from typing import Any

from .models import CandidateRecord
from .normalize import structural_facts
from .reconcile import CandidateReconciler, candidate_key
from .shopee_search import ShopeeCandidateAPI

class VisionCandidateError(RuntimeError):
    pass

def _identity_anchor_terms(product_name: str) -> list[str]:
    """Build narrow discovery anchors from identity-bearing words plus structure.

    These anchors are discovery-only. They do not accept a candidate and do not
    replace the existing structural queries.
    """
    words = [w.casefold() for w in str(product_name or "").split() if len(w) >= 3]
    facts = structural_facts(product_name)
    size = f"{facts['size_cm']:g}cm" if facts.get("size_cm") else ""
    structural = []
    if facts.get("drawers") is not None:
        structural.append("gaveta" if int(facts["drawers"]) == 1 else f"{int(facts['drawers'])} gavetas")
    if facts.get("doors") is not None:
        structural.append("porta" if int(facts["doors"]) == 1 else f"{int(facts['doors'])} portas")

    # Only use domain/identity terms that are actually present in the reference.
    anchors = [
        token for token in (
            "barbearia", "cabeleireiro", "bancada", "penteadeira",
            "suspensa", "camarim", "espelho", "gaveteiro", "nicho",
            "ripado", "basculhante",
        )
        if any(token in word for word in words)
    ]

    raw: list[str] = []
    for anchor in anchors:
        if size and structural:
            raw.append(" ".join((size, structural[0], anchor)))
        if structural:
            raw.append(" ".join((structural[0], anchor)))
        if size:
            raw.append(" ".join((size, anchor)))
    if len(anchors) >= 2 and size and structural:
        raw.append(" ".join((size, structural[0], anchors[0], anchors[1])))
    if len(anchors) >= 2 and structural:
        raw.append(" ".join((structural[0], anchors[0], anchors[1])))

    result: list[str] = []
    seen: set[str] = set()
    for term in raw:
        term = " ".join(term.split()).strip()
        key = term.casefold()
        if term and key not in seen:
            seen.add(key)
            result.append(term)
    return result


def _query_terms(product_name: str) -> list[str]:
    """Build several discovery signatures, prioritizing explicit structure."""
    words = [w for w in str(product_name or "").split() if len(w) >= 2]
    if not words:
        return []
    facts = structural_facts(product_name)
    size = f"{facts['size_cm']:g}cm" if facts.get("size_cm") else ""
    parts = []
    for token in ("bancada", "suspensa", "barbearia", "cabeleireiro", "gaveta", "porta", "nicho", "ripado", "basculhante"):
        if any(token.casefold() in w.casefold() for w in words):
            parts.append(token)
    distinctive = [w for w in words if any(ch.isdigit() for ch in w) or len(w) >= 5]
    raw = [
        " ".join(x for x in ("bancada", "suspensa", size, "gaveta") if x),
        " ".join(x for x in ("bancada", size, "gaveta") if x),
        " ".join(x for x in ("bancada", "barbearia", size, "gaveta") if x),
        " ".join(x for x in ("bancada", "cabeleireiro", size, "gaveta") if x),
        " ".join(x for x in ("bancada", "suspensa", size, "com gaveta") if x),
        " ".join(x for x in ("bancada", size, "1 gaveta") if x),
        " ".join(x for x in ("bancada", size, "gaveteiro") if x),
        " ".join(x for x in ("barbearia", size, "gaveta") if x),
        " ".join(x for x in ("cabeleireiro", size, "gaveta") if x),
        " ".join(x for x in ("bancada", "suspensa", "gaveta") if x),
        " ".join(x for x in (" ".join(parts[:5]), size) if x),
        " ".join(words[:6]),
        " ".join(distinctive[:5]),
        " ".join(words[-5:]),
    ]
    result: list[str] = []
    seen: set[str] = set()
    for term in raw:
        term = " ".join(term.split()).strip()
        key = term.casefold()
        if term and key not in seen:
            seen.add(key)
            result.append(term)
    return result

def _to_float(value: Any) -> float | None:
    try:
        if value is None or str(value).strip() == "":
            return None
        return float(str(value).replace(",", "."))
    except (TypeError, ValueError):
        return None

class CandidateDiscovery:
    """Expand a V1-resolved product into up to six validated alternatives."""

    def __init__(self, api=None, reconciler=None):
        self.api = api or ShopeeCandidateAPI()
        self.reconciler = reconciler or CandidateReconciler()

    def discover(self, original: dict[str, Any], *, original_affiliate_url: str, original_url: str) -> tuple[tuple[str, ...], tuple[dict[str, Any], ...]]:
        target = max(10, min(15, int(os.getenv("ARMORED_VISION_V2_TARGET_CANDIDATES", "12"))))
        maximum = max(1, min(6, int(os.getenv("ARMORED_VISION_V2_MAX_ACCEPTED", "6"))))
        minimum = max(1, min(maximum, int(os.getenv("ARMORED_VISION_V2_MIN_ACCEPTED", "2"))))
        reference = dict(original)
        try:
            reference.update(self.api.get_exact_product(str(original.get("shopId") or ""), str(original.get("itemId") or "")))
        except Exception:
            pass

        records: "OrderedDict[tuple[str, str], dict[str, Any]]" = OrderedDict()
        reference_cats = {str(x) for x in (reference.get("productCatIds") or [])}
        # Keep a large discovery pool before reconciliation.
        raw_pool_limit = max(target * 10, 100)
        # Search identity anchors first. They recover equivalent listings whose
        # listing noun changes (for example bancada -> penteadeira) while
        # retaining the same domain + size + structural signature.
        identity_terms = _identity_anchor_terms(str(reference.get("productName") or ""))
        base_terms = identity_terms + _query_terms(str(reference.get("productName") or ""))
        seen_terms: set[str] = set()
        ordered_terms = []
        for term in base_terms:
            key = term.casefold()
            if term and key not in seen_terms:
                seen_terms.add(key)
                ordered_terms.append(term)

        # Search multiple independent families and two/three pages before capping.
        # A hard pool cap must not prevent the identity-anchor phase from running.
        for keyword in ordered_terms:
            for sort_type in (1, 2):
                for page in (1, 2, 3):
                    products = self.api.search_products(keyword, page=page, limit=50, sort_type=sort_type)
                    for product in products:
                        key = candidate_key(product)
                        if not key[0] or not key[1] or key == candidate_key(reference):
                            continue
                        records.setdefault(key, product)
                        if len(records) >= raw_pool_limit:
                            break
                    if len(records) >= raw_pool_limit:
                        break
                if len(records) >= raw_pool_limit:
                    break
            if len(records) >= raw_pool_limit:
                # The anchor terms were deliberately placed first. Once the
                # pool is full, remaining broad discovery is unnecessary.
                break

        # Category expansion is discovery only; category never proves identity.
        search_category = getattr(self.api, "search_category_products", None)
        if callable(search_category):
            for category_id in sorted(reference_cats):
                for page in (1, 2):
                    for product in search_category(category_id, page=page, limit=50):
                        key = candidate_key(product)
                        if not key[0] or not key[1] or key == candidate_key(reference):
                            continue
                        records.setdefault(key, product)
                        if len(records) >= raw_pool_limit:
                            break
                    if len(records) >= raw_pool_limit:
                        break
                if len(records) >= raw_pool_limit:
                    break

        ref_facts = structural_facts(str(reference.get("productName") or ""))

        def structural_profile(product: dict[str, Any]) -> tuple[int, int]:
            cand_facts = structural_facts(str(product.get("productName") or ""))
            matches = sum(
                1 for field in ("size_cm", "doors", "drawers", "niches", "basculhante", "ripado", "models")
                if ref_facts.get(field) is not None and cand_facts.get(field) is not None
                and ref_facts.get(field) == cand_facts.get(field)
            )
            conflicts = sum(
                1 for field in ("size_cm", "doors", "drawers", "niches", "basculhante", "ripado", "models")
                if ref_facts.get(field) is not None and cand_facts.get(field) is not None
                and ref_facts.get(field) != cand_facts.get(field)
            )
            return matches, conflicts

        seed_candidates = []
        for product in records.values():
            matches, conflicts = structural_profile(product)
            if conflicts == 0 and matches >= 2:
                seed_candidates.append(product)
        for seed in seed_candidates[:3]:
            for keyword in _query_terms(str(seed.get("productName") or ""))[:6]:
                products = self.api.search_products(keyword, page=1, limit=50, sort_type=1)
                for product in products:
                    key = candidate_key(product)
                    if not key[0] or not key[1] or key == candidate_key(reference):
                        continue
                    records.setdefault(key, product)
                    if len(records) >= raw_pool_limit:
                        break
                if len(records) >= raw_pool_limit:
                    break
            if len(records) >= raw_pool_limit:
                break

        shop_search = getattr(self.api, "search_shop_products", None)
        if callable(shop_search):
            preliminary = sorted(records.values(), key=structural_profile, reverse=True)
            shops: list[str] = []
            for product in preliminary:
                matches, conflicts = structural_profile(product)
                shop_id = str(product.get("shopId") or "")
                if matches >= 1 and conflicts == 0 and shop_id and shop_id not in shops:
                    shops.append(shop_id)
                if len(shops) >= 3:
                    break
            for shop_id in shops:
                for page in (1, 2):
                    for product in shop_search(shop_id, page=page, limit=50):
                        key = candidate_key(product)
                        if not key[0] or not key[1] or key == candidate_key(reference):
                            continue
                        records.setdefault(key, product)

        def discovery_score(product: dict[str, Any]) -> tuple[float, int, float]:
            cats = {str(x) for x in (product.get("productCatIds") or [])}
            category_overlap = len(reference_cats & cats) / max(1, len(reference_cats | cats))
            cand_facts = structural_facts(str(product.get("productName") or ""))
            matches = sum(
                1 for field in ("size_cm", "doors", "drawers", "niches", "basculhante", "ripado", "models")
                if ref_facts.get(field) is not None and cand_facts.get(field) is not None
                and ref_facts.get(field) == cand_facts.get(field)
            )
            conflicts = sum(
                1 for field in ("size_cm", "doors", "drawers", "niches", "basculhante", "ripado", "models")
                if ref_facts.get(field) is not None and cand_facts.get(field) is not None
                and ref_facts.get(field) != cand_facts.get(field)
            )
            return category_overlap * 0.55 + matches * 0.15 - conflicts * 0.30, matches, category_overlap

        ranked = sorted(records.values(), key=discovery_score, reverse=True)

        # Explicit structural conflicts cannot crowd compatible candidates out.
        compatible = []
        neutral = []
        conflicting = []
        for product in ranked:
            matches, conflicts = structural_profile(product)
            if conflicts == 0 and matches >= 1:
                compatible.append(product)
            elif conflicts == 0:
                neutral.append(product)
            else:
                conflicting.append(product)
        shortlist = (compatible + neutral + conflicting)[: max(target * 2, target)]
        records = OrderedDict((candidate_key(product), product) for product in shortlist)


        evaluated: list[CandidateRecord] = [
            CandidateRecord(
                0, "original", original_url,
                str(reference.get("productLink") or original_url or ""),
                str(self.api.affiliate_link_for_product(reference)), str(reference.get("shopId") or ""),
                str(reference.get("itemId") or ""), str(reference.get("productName") or ""),
                str(reference.get("shopName") or ""), str(reference.get("imageUrl") or ""),
                tuple(str(x) for x in (reference.get("productCatIds") or [])),
                _to_float(reference.get("priceMin")), _to_float(reference.get("priceMax")),
                1.0, "ORIGINAL", "referência V1", {"source": "V1_EXACT"},
            )
        ]

        accepted: list[tuple[float, dict[str, Any], str, dict[str, Any]]] = []
        for product in records.values():
            is_valid, score, reason, evidence = self.reconciler.compare(reference, product)
            affiliate_url = str(product.get("offerLink") or "").strip()
            product_link = str(product.get("productLink") or "").strip()
            if is_valid and not affiliate_url and product_link:
                try:
                    affiliate_url = self.api.generate_short_link(product_link)
                except Exception:
                    affiliate_url = ""
            if is_valid and affiliate_url:
                try:
                    product = self.api.get_exact_product(
                        str(product.get("shopId")),
                        str(product.get("itemId")),
                    )
                    is_valid, score, reason, evidence = self.reconciler.compare(reference, product)
                    affiliate_url = str(product.get("offerLink") or "").strip()
                    if is_valid and not affiliate_url:
                        affiliate_url = self.api.generate_short_link(
                            str(product.get("productLink") or "")
                        )
                except Exception as exc:
                    is_valid = False
                    reason = f"revalidação falhou: {type(exc).__name__}: {exc}"
            decision = "ACCEPTED" if is_valid and affiliate_url else "REJECTED"
            if is_valid and affiliate_url:
                accepted.append((score, product, reason, evidence))
            evaluated.append(
                CandidateRecord(
                    0, "discovered", product_link, str(product.get("productLink") or ""),
                    affiliate_url, str(product.get("shopId") or ""), str(product.get("itemId") or ""),
                    str(product.get("productName") or ""), str(product.get("shopName") or ""),
                    str(product.get("imageUrl") or ""),
                    tuple(str(x) for x in (product.get("productCatIds") or [])),
                    _to_float(product.get("priceMin")), _to_float(product.get("priceMax")),
                    score, decision, reason, evidence,
                )
            )

        accepted.sort(key=lambda row: (-row[0], str(row[1].get("shopId")), str(row[1].get("itemId"))))
        accepted = accepted[:maximum]
        if len(accepted) < minimum:
            error = VisionCandidateError(
                f"V2 encontrou {len(accepted)} candidatos comprovados; mínimo={minimum}; descobertos={len(records)}"
            )
            error.accepted_count = len(accepted)
            error.minimum_required = minimum
            error.discovered_count = len(records)
            error.records = tuple(record.as_dict() for record in evaluated)
            raise error

        accepted_keys = {(str(p.get("shopId") or ""), str(p.get("itemId") or "")) for _, p, _, _ in accepted}
        final_links = [self.api.affiliate_link_for_product(reference)]
        final_records: list[dict[str, Any]] = [evaluated[0].as_dict()]
        next_order = 1
        for score, product, reason, evidence in accepted:
            key = (str(product.get("shopId") or ""), str(product.get("itemId") or ""))
            affiliate_url = str(product.get("offerLink") or "").strip()
            if not affiliate_url:
                affiliate_url = self.api.generate_short_link(str(product.get("productLink") or ""))
            final_links.append(affiliate_url)
            final_records.append(CandidateRecord(
                next_order, "discovered", str(product.get("productLink") or ""),
                str(product.get("productLink") or ""), affiliate_url,
                key[0], key[1], str(product.get("productName") or ""),
                str(product.get("shopName") or ""), str(product.get("imageUrl") or ""),
                tuple(str(x) for x in (product.get("productCatIds") or [])),
                _to_float(product.get("priceMin")), _to_float(product.get("priceMax")),
                score, "ACCEPTED", reason, evidence,
            ).as_dict())
            next_order += 1

        reject_order = next_order
        for record in evaluated[1:]:
            record_dict = record.as_dict()
            key = (record.shop_id, record.item_id)
            if record.decision == "ACCEPTED" and key in accepted_keys:
                continue
            if record.decision == "ACCEPTED" and key not in accepted_keys:
                record_dict["decision"] = "REJECTED"
                record_dict["reason"] = (
                    "mesmo produto, mas excedeu o máximo de "
                    f"{maximum} candidatos adicionais"
                )
            record_dict["candidate_order"] = reject_order
            final_records.append(record_dict)
            reject_order += 1

        return tuple(dict.fromkeys(final_links)), tuple(final_records)
