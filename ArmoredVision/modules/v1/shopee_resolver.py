from __future__ import annotations
import re
from dataclasses import dataclass
from urllib.parse import urlparse
import requests
PATTERNS=(re.compile(r"/opaanlp/(\d+)/(\d+)",re.I),re.compile(r"/product/(\d+)/(\d+)",re.I),re.compile(r"/(\d+)/(\d+)(?:[/?#]|$)",re.I))
@dataclass(frozen=True)
class ShopeeResolvedLink:
    original_url:str
    resolved_url:str
    shop_id:str
    item_id:str
def resolve_short_url(url:str,timeout:int=20)->ShopeeResolvedLink:
    original=str(url or "").strip()
    if not original: raise ValueError("URL Shopee vazia")
    r=requests.get(original,headers={"User-Agent":"Mozilla/5.0"},allow_redirects=True,timeout=timeout,stream=True)
    try:
        r.raise_for_status(); resolved=r.url
    finally: r.close()
    for p in PATTERNS:
        m=p.search(urlparse(resolved).path or "")
        if m: return ShopeeResolvedLink(original,resolved,m.group(1),m.group(2))
    raise ValueError(f"Não foi possível extrair shop_id/item_id: {resolved}")
