from __future__ import annotations
import re
import unicodedata

STOPWORDS = {
    "a", "as", "ao", "aos", "com", "da", "das", "de", "do", "dos", "e",
    "em", "para", "por", "sem", "um", "uma", "uns", "umas", "o", "os",
    "na", "nas", "no", "nos", "que", "kit", "conjunto", "novo", "nova",
    "original", "oficial", "promocao", "promoção", "oferta", "frete",
    "gratis", "grátis", "shopee",
}
UNIT_ALIASES = {
    "ml": "ml", "mls": "ml", "l": "l", "litro": "l", "litros": "l",
    "g": "g", "grama": "g", "gramas": "g", "kg": "kg", "quilo": "kg", "quilos": "kg",
    "mm": "mm", "milimetro": "mm", "milímetros": "mm", "cm": "cm",
    "centimetro": "cm", "centímetros": "cm", "m": "m", "metro": "m", "metros": "m",
    "w": "w", "watts": "w", "watt": "w", "v": "v", "volt": "v", "volts": "v",
    "un": "un", "und": "un", "unidade": "un", "unidades": "un", "pc": "un",
    "pcs": "un", "peca": "un", "peças": "un", "pecas": "un",
}

def fold(text: str) -> str:
    value = unicodedata.normalize("NFKD", str(text or ""))
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    value = value.casefold()
    value = re.sub(r"[^a-z0-9]+", " ", value)
    return re.sub(r"\s+", " ", value).strip()

def tokens(text: str) -> tuple[str, ...]:
    return tuple(t for t in fold(text).split() if t not in STOPWORDS)

def normalized_name(text: str) -> str:
    return " ".join(tokens(text))

def quantity_facts(text: str) -> dict[str, tuple[float, str]]:
    normalized = fold(text)
    facts: dict[str, tuple[float, str]] = {}
    pattern = re.compile(
        r"(?<!\w)(\d+(?:[\.,]\d+)?)\s*"
        r"(mls?|litros?|l|g|gramas?|kg|quilos?|mm|milimetros?|cm|centimetros?|"
        r"m|metros?|w|watts?|v|volts?|un|und|unidades?|pcs?|pecas?|pecas?)\b"
    )
    for match in pattern.finditer(normalized):
        raw_unit = match.group(2)
        unit = UNIT_ALIASES.get(raw_unit, raw_unit)
        key = {
            "ml": "volume", "l": "volume", "g": "weight", "kg": "weight",
            "mm": "size", "cm": "size", "m": "size", "w": "power",
            "v": "voltage", "un": "quantity",
        }.get(unit, unit)
        value = float(match.group(1).replace(",", "."))
        if key == "volume" and unit == "l":
            value *= 1000
            unit = "ml"
        elif key == "weight" and unit == "kg":
            value *= 1000
            unit = "g"
        elif key == "size" and unit == "m":
            value *= 100
            unit = "cm"
        facts[key] = (value, unit)
    return facts

def category_overlap(a: object, b: object) -> float | None:
    left = {str(x) for x in (a or []) if str(x).strip()}
    right = {str(x) for x in (b or []) if str(x).strip()}
    if not left or not right:
        return None
    return len(left & right) / max(1, len(left | right))
