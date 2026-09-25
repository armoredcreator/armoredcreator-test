from __future__ import annotations

import re
import unicodedata

FORBIDDEN_WORDS = {"embalagem", "tampa", "frasco", "lacre"}
SALES_WORDS = {
    "compre", "comprar", "garanta", "garantir", "imperdivel", "imperdível",
    "aproveite", "oferta", "ofertas", "promocao", "promoção", "desconto",
    "corra", "nao perca", "não perca",
}
EMOJI_RE = re.compile("[\U0001F000-\U0001FAFF\U00002600-\U000027BF]")
HASHTAG_RE = re.compile(r"(?<!\w)#([\wÀ-ÿ]+)", re.UNICODE)
WORD_RE = re.compile(r"[A-Za-zÀ-ÿ0-9]+(?:['-][A-Za-zÀ-ÿ0-9]+)?", re.UNICODE)

class CaptionPolicyError(ValueError):
    pass

def _fold(text: str) -> str:
    value = unicodedata.normalize("NFKD", str(text or ""))
    return "".join(ch for ch in value if not unicodedata.combining(ch)).casefold()

def _meaningful_product_tokens(product_name: str) -> set[str]:
    stop = {
        "a","as","ao","aos","com","da","das","de","do","dos","e","em",
        "para","por","sem","um","uma","kit","conjunto","original","novo","nova",
    }
    return {
        token for token in re.findall(r"[a-z0-9]+", _fold(product_name))
        if token not in stop and len(token) >= 4
    }

def validate_caption(caption: str, *, product_name: str = "") -> str:
    text = str(caption or "").replace("\r", "").strip()
    text = re.sub(r"^[*_~\s]+|[*_~\s]+$", "", text)
    if not text:
        raise CaptionPolicyError("legenda vazia")

    hashtags = HASHTAG_RE.findall(text)
    if not 1 <= len(hashtags) <= 2:
        raise CaptionPolicyError("a legenda precisa ter 1 ou 2 hashtags")
    if any(len(tag) > 20 for tag in hashtags):
        raise CaptionPolicyError("hashtag longa demais")
    if len(EMOJI_RE.findall(text)) != 1:
        raise CaptionPolicyError("a legenda precisa ter exatamente 1 emoji")

    folded = _fold(text)
    for forbidden in FORBIDDEN_WORDS:
        if re.search(rf"\b{re.escape(_fold(forbidden))}\b", folded):
            raise CaptionPolicyError(f"palavra proibida: {forbidden}")
    for sales_word in SALES_WORDS:
        if _fold(sales_word) in folded:
            raise CaptionPolicyError(f"linguagem comercial proibida: {sales_word}")

    lines = [line.strip() for line in text.splitlines() if line.strip()]
    main_lines = [line for line in lines if "#" not in line]
    if len(main_lines) != 1:
        raise CaptionPolicyError("texto principal deve ocupar uma única linha")

    main = main_lines[0]
    words = WORD_RE.findall(EMOJI_RE.sub("", main))
    if not 2 <= len(words) <= 3:
        raise CaptionPolicyError("texto principal deve conter 2 ou 3 palavras")

    product_tokens = _meaningful_product_tokens(product_name)
    caption_tokens = set(re.findall(r"[a-z0-9]+", _fold(EMOJI_RE.sub("", text))))
    overlap = caption_tokens & product_tokens
    if overlap:
        raise CaptionPolicyError("legenda menciona explicitamente o produto")

    hashtag_tokens = {_fold(tag) for tag in hashtags}
    if hashtag_tokens & product_tokens:
        raise CaptionPolicyError("hashtag menciona explicitamente o produto")

    return f"{main}\n{' '.join('#' + tag for tag in hashtags)}"
