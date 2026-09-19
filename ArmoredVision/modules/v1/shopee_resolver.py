from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlparse

import requests

PATTERNS = (
    re.compile(r"/opaanlp/(\d+)/(\d+)", re.I),
    re.compile(r"/product/(\d+)/(\d+)", re.I),
    re.compile(r"/(\d+)/(\d+)(?:[/?#]|$)", re.I),
)


@dataclass(frozen=True)
class ShopeeResolvedLink:
    original_url: str
    resolved_url: str
    shop_id: str
    item_id: str


def _extract(url: str):
    path = urlparse(url).path or ""
    for pattern in PATTERNS:
        match = pattern.search(path)
        if match:
            return match.group(1), match.group(2)
    return None


def resolve_short_url(url: str, timeout: int = 20) -> ShopeeResolvedLink:
    original = str(url or "").strip()
    if not original:
        raise ValueError("URL Shopee vazia")

    direct = _extract(original)
    if direct:
        return ShopeeResolvedLink(
            original,
            original,
            direct[0],
            direct[1],
        )

    response = requests.get(
        original,
        headers={"User-Agent": "Mozilla/5.0"},
        allow_redirects=True,
        timeout=timeout,
        stream=True,
    )
    try:
        response.raise_for_status()
        resolved = response.url
    finally:
        response.close()

    extracted = _extract(resolved)
    if extracted:
        return ShopeeResolvedLink(
            original,
            resolved,
            extracted[0],
            extracted[1],
        )

    raise ValueError(
        f"Não foi possível extrair shop_id/item_id: {resolved}"
    )
