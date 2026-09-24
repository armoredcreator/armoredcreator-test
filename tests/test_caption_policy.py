from __future__ import annotations

import pytest

from ArmoredVision.modules.caption.generator import CaptionGenerator
from ArmoredVision.modules.caption.policy import CaptionPolicyError, validate_caption


def test_caption_policy_accepts_required_shape():
    result = validate_caption(
        "Olha esse charme ✨\n#beleza #rotina",
        product_name="Batom Matte Vermelho",
    )
    assert result == "Olha esse charme ✨\n#beleza #rotina"


@pytest.mark.parametrize(
    "caption",
    [
        "Que embalagem linda ✨\n#casa",
        "Olha só esse produto incrível ✨\n#casa",
        "Olha esse charme 😍✨\n#casa",
        "Olha esse charme ✨\n#casa #rotina #achadinhos",
        "Olha esse charme demais ✨\n#casa",
    ],
)
def test_caption_policy_rejects_invalid_rules(caption):
    with pytest.raises(CaptionPolicyError):
        validate_caption(caption, product_name="Organizador de cozinha")


def test_caption_generator_has_safe_deterministic_fallback(monkeypatch):
    monkeypatch.setenv("ARMORED_CAPTION_ENABLED", "1")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setenv("ARMORED_CAPTION_ALLOW_DETERMINISTIC_FALLBACK", "1")

    caption = CaptionGenerator().generate({
        "productName": "Kit de cuidados de beleza",
        "category_name": "beleza",
    })

    assert validate_caption(caption, product_name="Kit de cuidados de beleza") == caption
