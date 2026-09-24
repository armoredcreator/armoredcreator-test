from __future__ import annotations

from difflib import SequenceMatcher
from typing import Any, Callable
import requests

from .normalize import category_overlap, normalized_name, quantity_facts, tokens

def _as_float(value: object) -> float | None:
    try:
        if value is None or str(value).strip() == "":
            return None
        return float(str(value).replace(",", "."))
    except (TypeError, ValueError):
        return None

def _text_score(left: str, right: str) -> float:
    a = set(tokens(left))
    b = set(tokens(right))
    if not a or not b:
        return 0.0
    jaccard = len(a & b) / len(a | b)
    sequence = SequenceMatcher(None, normalized_name(left), normalized_name(right)).ratio()
    containment = max(len(a & b) / max(1, len(a)), len(a & b) / max(1, len(b)))
    return max(0.0, min(1.0, 0.45 * jaccard + 0.35 * sequence + 0.20 * containment))

def _price_score(original: dict[str, Any], candidate: dict[str, Any]) -> float | None:
    o_min = _as_float(original.get("priceMin") or original.get("price"))
    o_max = _as_float(original.get("priceMax"))
    c_min = _as_float(candidate.get("priceMin") or candidate.get("price"))
    c_max = _as_float(candidate.get("priceMax"))
    if o_min is None or c_min is None:
        return None
    o_ref = (o_min + (o_max if o_max is not None else o_min)) / 2
    c_ref = (c_min + (c_max if c_max is not None else c_min)) / 2
    if o_ref <= 0 or c_ref <= 0:
        return None
    relative = abs(o_ref - c_ref) / max(o_ref, c_ref)
    return max(0.0, 1.0 - min(1.0, relative))

def _download_hash(url: str, timeout: int) -> tuple[int, ...] | None:
    try:
        import cv2
        import numpy as np
    except ImportError:
        return None
    try:
        response = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=timeout)
        response.raise_for_status()
        image = cv2.imdecode(
            np.frombuffer(response.content, dtype=np.uint8),
            cv2.IMREAD_GRAYSCALE,
        )
        if image is None or image.size == 0:
            return None
        image = cv2.resize(image, (33, 32), interpolation=cv2.INTER_AREA)
        diff = image[:, 1:] >= image[:, :-1]
        return tuple(int(x) for x in diff.flatten())
    except (requests.RequestException, ValueError):
        return None

def _image_score(original_url: str, candidate_url: str) -> float | None:
    if not original_url or not candidate_url:
        return None
    if original_url.rstrip("/") == candidate_url.rstrip("/"):
        return 1.0
    first = _download_hash(original_url, 8)
    second = _download_hash(candidate_url, 8)
    if first is None or second is None or len(first) != len(second):
        return None
    distance = sum(a != b for a, b in zip(first, second))
    return 1.0 - distance / len(first)

class CandidateReconciler:
    """Conservative identity matcher; no keyword result is accepted blindly."""

    def __init__(self, *, image_scorer: Callable[[str, str], float | None] | None = None):
        self.image_scorer = image_scorer or _image_score

    def compare(self, original: dict[str, Any], candidate: dict[str, Any]) -> tuple[bool, float, str, dict[str, Any]]:
        original_name = str(original.get("productName") or "")
        candidate_name = str(candidate.get("productName") or "")
        name_score = _text_score(original_name, candidate_name)

        original_facts = quantity_facts(original_name)
        candidate_facts = quantity_facts(candidate_name)
        conflicts: list[str] = []
        matches: list[str] = []
        for key in set(original_facts) & set(candidate_facts):
            ov, ou = original_facts[key]
            cv, cu = candidate_facts[key]
            tolerance = max(1.0, abs(ov) * 0.01)
            if abs(ov - cv) > tolerance or ou != cu:
                conflicts.append(key)
            else:
                matches.append(key)

        cat_score = category_overlap(original.get("productCatIds"), candidate.get("productCatIds"))
        same_shop = str(original.get("shopId") or "") == str(candidate.get("shopId") or "")
        price_score = _price_score(original, candidate)
        image_score = None
        if name_score >= 0.55:
            image_score = self.image_scorer(
                str(original.get("imageUrl") or ""),
                str(candidate.get("imageUrl") or ""),
            )

        evidence = {
            "name_score": round(name_score, 4),
            "category_overlap": None if cat_score is None else round(cat_score, 4),
            "same_shop": same_shop,
            "attribute_matches": matches,
            "attribute_conflicts": conflicts,
            "price_score": None if price_score is None else round(price_score, 4),
            "image_score": None if image_score is None else round(image_score, 4),
        }

        if conflicts:
            return False, 0.0, f"atributo incompatível: {', '.join(conflicts)}", evidence

        compatible_category = cat_score is not None and cat_score >= 0.50
        strong_name = name_score >= 0.80
        strong_image = image_score is not None and image_score >= 0.88
        weighted = (
            0.55 * name_score
            + 0.18 * (cat_score if cat_score is not None else 0.0)
            + 0.08 * (1.0 if same_shop else 0.0)
            + 0.07 * len(matches)
            + 0.07 * (price_score if price_score is not None else 0.0)
            + 0.05 * (image_score if image_score is not None else 0.0)
        )
        weighted = max(0.0, min(1.0, weighted))

        if strong_name and compatible_category and (matches or same_shop or strong_image):
            return True, weighted, "nome forte + categoria + evidência adicional", evidence
        if name_score >= 0.90 and (matches or strong_image):
            return True, weighted, "nome muito forte + atributos/imagem", evidence
        if strong_image and name_score >= 0.65 and (compatible_category or matches):
            return True, weighted, "imagem forte + identidade textual/atributos", evidence
        if name_score >= 0.74 and matches and (compatible_category or same_shop):
            return True, weighted, "nome e atributos compatíveis", evidence
        return False, weighted, "evidência insuficiente para mesma identidade", evidence
