from __future__ import annotations
import hashlib
import json
import os
import time
from typing import Any
import requests

PRODUCT_SEARCH_QUERY = """
query ProductSearch($keyword: String, $productCatId: Int, $shopId: Int64, $itemId: Int64, $sortType: Int, $page: Int, $limit: Int) {
  productOfferV2(keyword: $keyword, productCatId: $productCatId, shopId: $shopId, itemId: $itemId, sortType: $sortType, page: $page, limit: $limit) {
    nodes {
      itemId commissionRate sellerCommissionRate shopeeCommissionRate commission sales
      priceMin priceMax productCatIds ratingStar priceDiscountRate imageUrl productName
      shopId shopName shopType productLink offerLink periodStartTime periodEndTime
    }
    pageInfo { page limit hasNextPage }
  }
}
"""

class ShopeeCandidateAPIError(RuntimeError):
    pass

class ShopeeCandidateAPI:
    """V2-only Shopee client; ArmoredVision V1 remains untouched."""

    def __init__(self, app_id: str | None = None, secret_key: str | None = None):
        self.app_id = app_id or os.getenv("SHOPEE_APP_ID")
        self.secret_key = secret_key or os.getenv("SHOPEE_SECRET_KEY")
        if not self.app_id or not self.secret_key:
            raise ShopeeCandidateAPIError("SHOPEE_APP_ID/SHOPEE_SECRET_KEY não configurados")

    def _post(self, query: str, variables: dict[str, Any] | None = None) -> dict[str, Any]:
        payload = json.dumps({"query": query, "variables": variables or {}}, ensure_ascii=False, separators=(",", ":"))
        retries = max(1, int(os.getenv("SHOPEE_API_MAX_RETRIES", "3")))
        last: Exception | None = None
        for attempt in range(1, retries + 1):
            timestamp = int(time.time())
            signature = hashlib.sha256(f"{self.app_id}{timestamp}{payload}{self.secret_key}".encode()).hexdigest()
            try:
                response = requests.post(
                    os.getenv("SHOPEE_AFFILIATE_API_URL", "https://open-api.affiliate.shopee.com.br/graphql"),
                    data=payload.encode(),
                    headers={"Content-Type": "application/json", "Authorization": f"SHA256 Credential={self.app_id},Timestamp={timestamp},Signature={signature}"},
                    timeout=int(os.getenv("SHOPEE_API_TIMEOUT", "30")),
                )
                response.raise_for_status()
                data = response.json()
                if data.get("errors"):
                    raise ShopeeCandidateAPIError(str(data["errors"]))
                return data
            except (requests.RequestException, ValueError, ShopeeCandidateAPIError) as exc:
                last = exc
                if attempt < retries:
                    time.sleep(float(os.getenv("SHOPEE_API_RETRY_BASE_SECONDS", "2")) * attempt)
        raise ShopeeCandidateAPIError(f"Falha Shopee V2: {last}")

    def search_products(self, keyword: str, *, page: int = 1, limit: int = 20, sort_type: int = 1) -> list[dict[str, Any]]:
        data = self._post(PRODUCT_SEARCH_QUERY, {"keyword": str(keyword), "page": int(page), "limit": min(20, int(limit)), "sortType": int(sort_type)})
        return list((data.get("data", {}).get("productOfferV2", {}) or {}).get("nodes") or [])

    def get_exact_product(self, shop_id: str, item_id: str) -> dict[str, Any]:
        data = self._post(PRODUCT_SEARCH_QUERY, {"shopId": str(shop_id), "itemId": str(item_id), "page": 1, "limit": 1, "sortType": 1})
        nodes = list((data.get("data", {}).get("productOfferV2", {}) or {}).get("nodes") or [])
        if not nodes:
            raise ShopeeCandidateAPIError(f"V2 candidato não encontrado: {shop_id}:{item_id}")
        product = nodes[0]
        if str(product.get("shopId")) != str(shop_id) or str(product.get("itemId")) != str(item_id):
            raise ShopeeCandidateAPIError("V2 candidato divergente na revalidação")
        return product
