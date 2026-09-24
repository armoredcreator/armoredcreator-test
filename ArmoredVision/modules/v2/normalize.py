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
COUNT_WORDS = {
    "unidades", "unidade", "itens", "item", "pecas", "peças", "peca",
    "potes", "pote", "garrafas", "garrafa", "frascos", "frasco",
    "pares", "par", "jogos", "jogo", "kits", "kit",
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

    count_pattern = re.compile(
        r"(?<!\w)(\d+)\s+(?:" + "|".join(sorted(COUNT_WORDS, key=len, reverse=True)) + r")\b"
    )
    for match in count_pattern.finditer(normalized):
        facts["quantity"] = (float(match.group(1)), "un")

    return facts


def structural_facts(text: str) -> dict[str, object]:
    """Extract explicit product-identity structure; unknown stays None."""
    normalized = fold(text)
    facts: dict[str, object] = {
        "size_cm": None, "doors": None, "drawers": None, "niches": None,
        "basculhante": None, "ripado": None, "models": (),
    }
    size = quantity_facts(text).get("size")
    if size:
        facts["size_cm"] = round(float(size[0]), 3)

    door = re.search(r"(?<!\w)(\d+)\s*(?:portas?|porta)\b", normalized)
    if door:
        facts["doors"] = int(door.group(1))
    elif re.search(r"\b(?:com|c)\s+porta\b|\bporta\s+basculhante\b", normalized):
        facts["doors"] = 1
    elif re.search(r"\bsem\s+portas?\b", normalized):
        facts["doors"] = 0

    drawer = re.search(r"(?<!\w)(\d+)\s*(?:gavetas?|gaveta)\b", normalized)
    if drawer:
        facts["drawers"] = int(drawer.group(1))
    elif re.search(r"\b(?:com|c)\s+gaveta\b", normalized):
        facts["drawers"] = 1
    elif re.search(r"\bsem\s+gavetas?\b", normalized):
        facts["drawers"] = 0

    if re.search(r"\bnicho\b", normalized):
        facts["niches"] = True
    elif re.search(r"\bsem\s+nicho\b", normalized):
        facts["niches"] = False
    if re.search(r"\bbasculhante\b", normalized):
        facts["basculhante"] = True
    elif re.search(r"\b(?:sem|nao)\s+basculhante\b", normalized):
        facts["basculhante"] = False
    if re.search(r"\bripado\b", normalized):
        facts["ripado"] = True
    elif re.search(r"\b(?:sem|nao)\s+ripado\b", normalized):
        facts["ripado"] = False

    model_hits = []
    for match in re.finditer(r"\b(?:modelo|linha)\s+([a-z0-9][a-z0-9\-]{2,})\b", normalized):
        model_hits.append(match.group(1))
    for match in re.finditer(r"\b(vegas|life)\s*([0-9]+(?:[\.,][0-9]+)?)?\b", normalized):
        value, suffix = match.group(1), match.group(2)
        model_hits.append(value if not suffix else f"{value}{suffix}")
    facts["models"] = tuple(sorted(set(model_hits)))
    return facts


def structural_compare(original: dict[str, object], candidate: dict[str, object]) -> tuple[list[str], list[str]]:
    left = structural_facts(str(original.get("productName") or ""))
    right = structural_facts(str(candidate.get("productName") or ""))
    matches: list[str] = []
    conflicts: list[str] = []
    for key, lv in left.items():
        rv = right.get(key)
        if lv is None or rv is None:
            continue
        if key == "models":
            if lv and rv:
                if set(lv) != set(rv):
                    conflicts.append(key)
                else:
                    matches.append(key)
            continue
        if lv != rv:
            conflicts.append(key)
        else:
            matches.append(key)
    return matches, conflicts

def category_overlap(a: object, b: object) -> float | None:
    left = {str(x) for x in (a or []) if str(x).strip()}
    right = {str(x) for x in (b or []) if str(x).strip()}
    if not left or not right:
        return None
    return len(left & right) / max(1, len(left | right))
