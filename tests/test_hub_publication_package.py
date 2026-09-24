from __future__ import annotations

from pathlib import Path

from ArmoredHub.service import ArmoredHub
from armored_core.models import Item, State


def make_item(**kwargs):
    base = dict(
        content_id="1",
        telegram_message_id="1",
        state=State.PUBLISHING,
        workspace=Path("."),
        original_path=Path("original.mp4"),
        working_path=None,
        result_path=Path("result.mp4"),
        affiliate_name="Produto",
        affiliate_url="https://s.shopee.com.br/original",
    )
    base.update(kwargs)
    return Item(**base)


def test_hub_formats_caption_plus_all_links():
    item = make_item(
        publication_caption="Olha esse charme ✨\n#casa #decoracao",
        affiliate_urls=(
            "https://s.shopee.com.br/o",
            "https://s.shopee.com.br/1",
            "https://s.shopee.com.br/2",
        ),
    )
    package = ArmoredHub._publication_text(item)
    assert package == (
        "Olha esse charme ✨\n#casa #decoracao\n\n"
        "https://s.shopee.com.br/o\n"
        "https://s.shopee.com.br/1\n"
        "https://s.shopee.com.br/2"
    )


def test_hub_keeps_legacy_link_only_publications_compatible():
    item = make_item()
    assert ArmoredHub._publication_text(item) == "https://s.shopee.com.br/original"
