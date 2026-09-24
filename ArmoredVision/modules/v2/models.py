from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any

@dataclass(frozen=True)
class CandidateRecord:
    candidate_order: int
    source_type: str
    source_url: str
    product_link: str
    affiliate_url: str
    shop_id: str
    item_id: str
    product_name: str
    shop_name: str
    image_url: str
    category_ids: tuple[str, ...] = ()
    price_min: float | None = None
    price_max: float | None = None
    score: float = 0.0
    decision: str = "DISCOVERED"
    reason: str = ""
    evidence: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "candidate_order": self.candidate_order,
            "source_type": self.source_type,
            "source_url": self.source_url,
            "product_link": self.product_link,
            "affiliate_url": self.affiliate_url,
            "shop_id": self.shop_id,
            "item_id": self.item_id,
            "product_name": self.product_name,
            "shop_name": self.shop_name,
            "image_url": self.image_url,
            "category_ids": list(self.category_ids),
            "price_min": self.price_min,
            "price_max": self.price_max,
            "score": self.score,
            "decision": self.decision,
            "reason": self.reason,
            "evidence": self.evidence,
        }
