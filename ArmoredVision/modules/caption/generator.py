from __future__ import annotations

import base64
import os
from typing import Any, Callable

import requests

from .policy import CaptionPolicyError, validate_caption

DEFAULT_REACTIONS = (
    ("beleza", ("Olha esse charme ✨", ("#beleza", "#autocuidado"))),
    ("maqui", ("Fiquei encantada 😍", ("#beleza", "#maquiagem"))),
    ("casa", ("Que achado lindo ✨", ("#casa", "#decoracao"))),
    ("decor", ("Que charme aqui ✨", ("#decoracao", "#casa"))),
    ("moda", ("Olha esse look ✨", ("#moda", "#estilo"))),
    ("cozinha", ("Olha que pratico ✨", ("#casa", "#cozinha"))),
)

class CaptionGenerationError(RuntimeError):
    pass

def _deterministic_caption(product: dict[str, Any]) -> str:
    context = str(product.get("category_name") or product.get("category") or "").casefold()
    for key, (main, tags) in DEFAULT_REACTIONS:
        if key in context:
            return main + "\n" + " ".join(tags)
    return "Olha esse charme ✨\n#achadinhos #rotina"

class CaptionGenerator:
    """Gemini-backed generator with a deterministic fallback and hard validation."""

    def __init__(self, requester: Callable[..., Any] | None = None):
        self.requester = requester or requests.post

    def generate(self, product: dict[str, Any]) -> str:
        if os.getenv("ARMORED_CAPTION_ENABLED", "0") != "1":
            raise CaptionGenerationError("gerador de legenda desativado")

        api_key = (os.getenv("GEMINI_API_KEY") or "").strip()
        if not api_key:
            if os.getenv("ARMORED_CAPTION_ALLOW_DETERMINISTIC_FALLBACK", "1") == "1":
                return validate_caption(
                    _deterministic_caption(product),
                    product_name=str(product.get("productName") or ""),
                )
            raise CaptionGenerationError("GEMINI_API_KEY não configurada")

        model = os.getenv("ARMORED_CAPTION_MODEL", "gemini-3.8-flash")
        prompt = """
Crie uma legenda em português do Brasil para um vídeo curto de descoberta.
Ela deve soar como uma reação espontânea a um detalhe visual, acabamento ou
sensação transmitida pelo conteúdo. NÃO mencione explicitamente o produto,
nome, marca ou modelo. Texto principal: EXATAMENTE 2 ou 3 palavras e
exatamente 1 emoji. Segunda linha: exatamente 1 ou 2 hashtags curtas e
compatíveis com o público/contexto. NUNCA use embalagem, tampa, frasco ou
lacre. NUNCA use linguagem de venda, urgência, promoção ou desconto.
Retorne somente as duas linhas finais, sem aspas e sem explicações.
""".strip()

        parts: list[dict[str, Any]] = [{
            "text": (
                prompt
                + f"\n\nNome interno (não repetir): {product.get('productName', '')}"
                + f"\nCategoria/contexto: {product.get('category_name', '') or product.get('category', '')}"
                + f"\nLoja (não repetir): {product.get('shopName', '')}"
            )
        }]
        image_url = str(product.get("imageUrl") or "").strip()
        if image_url:
            try:
                image = requests.get(
                    image_url,
                    headers={"User-Agent": "Mozilla/5.0"},
                    timeout=int(os.getenv("ARMORED_CAPTION_IMAGE_TIMEOUT", "10")),
                )
                image.raise_for_status()
                mime = image.headers.get("Content-Type", "image/jpeg").split(";", 1)[0]
                parts.append({
                    "inline_data": {
                        "mime_type": mime,
                        "data": base64.b64encode(image.content).decode("ascii"),
                    }
                })
            except requests.RequestException:
                pass

        response = self.requester(
            f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
            headers={"x-goog-api-key": api_key, "Content-Type": "application/json"},
            json={
                "contents": [{"parts": parts}],
                "generationConfig": {"maxOutputTokens": 80},
            },
            timeout=int(os.getenv("ARMORED_CAPTION_API_TIMEOUT", "30")),
        )
        response.raise_for_status()
        data = response.json()
        try:
            generated = data["candidates"][0]["content"]["parts"][0]["text"].strip()
        except (KeyError, IndexError, TypeError) as exc:
            raise CaptionGenerationError("Gemini não retornou texto de legenda") from exc
        try:
            return validate_caption(
                generated,
                product_name=str(product.get("productName") or ""),
            )
        except CaptionPolicyError as exc:
            raise CaptionGenerationError(f"Gemini gerou legenda fora da política: {exc}") from exc
