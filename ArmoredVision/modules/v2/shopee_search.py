from __future__ import annotations
import hashlib
import json
import os
import time
from typing import Any
import requests

PRODUCT_SEARCH_QUERY = """
query ProductSearch($keyword: String, $productCatId: Int, $shopId: Int64, $itemId: Int64, $listType: Int, $matchId: Int64, $sortType: Int, $page: Int, $limit: Int) {
  productOfferV2(keyword: $keyword, productCatId: $productCatId, shopId: $shopId, itemId: $itemId, listType: $listType, matchId: $matchId, sortType: $sortType, page: $page, limit: $limit) {
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

MINIMAL_PRODUCT_SEARCH_QUERY = """
query ProductSearchMinimal($keyword: String, $productCatId: Int, $shopId: Int64, $itemId: Int64, $page: Int, $limit: Int) {
  productOfferV2(keyword: $keyword, productCatId: $productCatId, shopId: $shopId, itemId: $itemId, page: $page, limit: $limit) {
    nodes {
      itemId productCatIds imageUrl productName shopId shopName
      productLink offerLink priceMin priceMax
    }
    pageInfo { page limit hasNextPage }
  }
}
"""


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
                    timeout=(
                        float(os.getenv("SHOPEE_API_CONNECT_TIMEOUT", os.getenv("SHOPEE_API_TIMEOUT", "10"))),
                        float(os.getenv("SHOPEE_API_READ_TIMEOUT", os.getenv("SHOPEE_API_TIMEOUT", "20"))),
                    ),
                )
                response.raise_for_status()
                data = response.json()
                if data.get("errors"):
                    raise ShopeeCandidateAPIError(str(data["errors"]))
                return data
            except (requests.RequestException, ValueError, ShopeeCandidateAPIError) as exc:
                last = exc
                # GraphQL application errors are deterministic for this request;
                # retrying the identical payload only multiplies latency.
                if isinstance(exc, ShopeeCandidateAPIError):
                    raise
                if attempt < retries:
                    time.sleep(float(os.getenv("SHOPEE_API_RETRY_BASE_SECONDS", "2")) * attempt)
        raise ShopeeCandidateAPIError(f"Falha Shopee V2: {last}")

    def _post_product_search_with_fallback(
        self,
        variables: dict[str, Any],
        *,
        fallback_variables: dict[str, Any],
    ) -> dict[str, Any]:
        try:
            return self._post(PRODUCT_SEARCH_QUERY, variables)
        except ShopeeCandidateAPIError as exc:
            # Shopee sometimes returns GraphQL 10010 ("got null for non-null")
            # for otherwise valid productOfferV2 filter/sort combinations.
            # Retry with the minimal stable argument set instead of aborting V2.
            if "10010" not in str(exc):
                raise
            try:
                return self._post(MINIMAL_PRODUCT_SEARCH_QUERY, fallback_variables)
            except ShopeeCandidateAPIError as fallback_exc:
                raise ShopeeCandidateAPIError(
                    f"Falha Shopee V2 após fallback mínimo: {fallback_exc}"
                ) from fallback_exc

    @staticmethod
    def _nodes(data: dict[str, Any]) -> list[dict[str, Any]]:
        return list((data.get("data", {}).get("productOfferV2", {}) or {}).get("nodes") or [])

    def search_products(self, keyword: str, *, page: int = 1, limit: int = 20, sort_type: int = 1) -> list[dict[str, Any]]:
        variables = {
            "keyword": str(keyword),
            "page": int(page),
            "limit": min(50, int(limit)),
            "sortType": int(sort_type),
        }
        data = self._post_product_search_with_fallback(
            variables,
            fallback_variables={
                "keyword": str(keyword),
                "page": int(page),
                "limit": min(50, int(limit)),
            },
        )
        return self._nodes(data)


    def search_shop_products(self, shop_id: str, *, page: int = 1, limit: int = 50) -> list[dict[str, Any]]:
        data = self._post_product_search_with_fallback(
            {"shopId": str(shop_id), "page": int(page), "limit": min(50, int(limit)), "sortType": 1},
            {"shopId": str(shop_id), "page": int(page), "limit": min(50, int(limit))},
        )
        return self._nodes(data)

    def search_shop_products_sorted(
        self,
        shop_id: str,
        *,
        page: int = 1,
        limit: int = 50,
        sort_type: int = 1,
    ) -> list[dict[str, Any]]:
        data = self._post_product_search_with_fallback(
            {
                "shopId": str(shop_id),
                "listType": 5,
                "matchId": int(shop_id),
                "page": int(page),
                "limit": min(50, int(limit)),
                "sortType": int(sort_type),
            },
            {"shopId": str(shop_id), "page": int(page), "limit": min(50, int(limit))},
        )
        return self._nodes(data)

    def search_category_products(
        self,
        category_id: str,
        *,
        page: int = 1,
        limit: int = 50,
        list_type: int = 4,
    ) -> list[dict[str, Any]]:
        data = self._post_product_search_with_fallback(
            {
                "productCatId": int(category_id),
                "listType": int(list_type),
                "matchId": int(category_id),
                "page": int(page),
                "limit": min(50, int(limit)),
                "sortType": 1,
            },
            {"productCatId": int(category_id), "page": int(page), "limit": min(50, int(limit))},
        )
        return self._nodes(data)

    def generate_short_link(self, origin_url: str) -> str:
        q = f"""mutation {{ generateShortLink(input: {{ originUrl: {json.dumps(str(origin_url))} }}) {{ shortLink }} }}"""
        link = ((self._post(q).get("data", {}).get("generateShortLink") or {}).get("shortLink"))
        if not link:
            raise ShopeeCandidateAPIError("Shopee não retornou shortLink V2")
        return str(link).strip()

    def affiliate_link_for_product(self, product: dict[str, Any]) -> str:
        offer = str(product.get("offerLink") or "").strip()
        if offer:
            return offer
        product_link = str(product.get("productLink") or "").strip()
        if not product_link:
            raise ShopeeCandidateAPIError("Produto não possui productLink canônico para gerar afiliação")
        return self.generate_short_link(product_link)

    def get_exact_product(self, shop_id: str, item_id: str) -> dict[str, Any]:
        data = self._post_product_search_with_fallback(
            {"shopId": str(shop_id), "itemId": str(item_id), "page": 1, "limit": 1, "sortType": 1},
            {"shopId": str(shop_id), "itemId": str(item_id), "page": 1, "limit": 1},
        )
        nodes = self._nodes(data)
        if not nodes:
            raise ShopeeCandidateAPIError(f"V2 candidato não encontrado: {shop_id}:{item_id}")
        product = nodes[0]
        if str(product.get("shopId")) != str(shop_id) or str(product.get("itemId")) != str(item_id):
            raise ShopeeCandidateAPIError("V2 candidato divergente na revalidação")
        return product
