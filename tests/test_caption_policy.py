from __future__ import annotations

import os
import unittest

from ArmoredVision.modules.caption.generator import CaptionGenerator
from ArmoredVision.modules.caption.policy import CaptionPolicyError, validate_caption


class CaptionPolicyTests(unittest.TestCase):
    def tearDown(self):
        os.environ.pop("ARMORED_CAPTION_ENABLED", None)
        os.environ.pop("GEMINI_API_KEY", None)
        os.environ.pop("ARMORED_CAPTION_ALLOW_DETERMINISTIC_FALLBACK", None)

    def test_accepts_required_shape(self):
        result = validate_caption(
            "Olha esse charme ✨\n#beleza #rotina",
            product_name="Batom Matte Vermelho",
        )
        self.assertEqual(result, "Olha esse charme ✨\n#beleza #rotina")

    def test_rejects_forbidden_and_invalid_rules(self):
        invalid = (
            "Que embalagem linda ✨\n#casa",
            "Olha só esse produto incrível ✨\n#casa",
            "Olha esse charme 😍✨\n#casa",
            "Olha esse charme ✨\n#casa #rotina #achadinhos",
            "Olha esse charme demais ✨\n#casa",
        )
        for caption in invalid:
            with self.subTest(caption=caption):
                with self.assertRaises(CaptionPolicyError):
                    validate_caption(caption, product_name="Organizador de cozinha")

    def test_generator_has_safe_deterministic_fallback(self):
        os.environ["ARMORED_CAPTION_ENABLED"] = "1"
        os.environ.pop("GEMINI_API_KEY", None)
        os.environ["ARMORED_CAPTION_ALLOW_DETERMINISTIC_FALLBACK"] = "1"

        caption = CaptionGenerator().generate({
            "productName": "Kit de cuidados de beleza",
            "category_name": "beleza",
        })

        self.assertEqual(
            validate_caption(caption, product_name="Kit de cuidados de beleza"),
            caption,
        )


if __name__ == "__main__":
    unittest.main()
