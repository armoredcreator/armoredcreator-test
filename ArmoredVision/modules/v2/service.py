from __future__ import annotations

import os
from collections import OrderedDict
from typing import Any

from .models import CandidateRecord
from .reconcile import CandidateReconciler, candidate_key
from .shopee_search import ShopeeCandidateAPI

class VisionCandidateError(RuntimeError):
    pass

def _query_terms(product_name: str) -> list[str]:
    words = [w for w in str(product_name or "").split() if len(w) >= 2]
    if not words:
        return []
    distinctive = [w for w in words if any(ch.isdigit() for ch in w) or len(w) >= 5]
    raw = [" ".join(words[:6]), " ".join(words[:4]), " ".join(distinctive[:5]), " ".join(words[-5:])]
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
        for keyword in _query_terms(str(reference.get("productName") or "")):
            products = self.api.search_products(keyword, page=1, limit=20, sort_type=1)
            for product in products:
                key = candidate_key(product)
                if not key[0] or not key[1]:
                    continue
                if str(product.get("shopId")) == str(reference.get("shopId")) and str(product.get("itemId")) == str(reference.get("itemId")):
                    continue
                records.setdefault(key, product)
                if len(records) >= target:
                    break
            if len(records) >= target:
                break

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
            raise VisionCandidateError(
                f"V2 encontrou {len(accepted)} candidatos comprovados; mínimo={minimum}; descobertos={len(records)}"
            )

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
