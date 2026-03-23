"""
Normalisation helpers: SKU, price, availability, brand.
"""
from __future__ import annotations
import re
from typing import Optional

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import BRAND_SYNONYMS, SKU_STRIP_SEPARATORS_FOR_SEARCH

# Pre-build reverse lookup: alias -> canonical
_BRAND_ALIAS_MAP: dict[str, str] = {}
for canonical, aliases in BRAND_SYNONYMS.items():
    _BRAND_ALIAS_MAP[canonical.upper()] = canonical.upper()
    for alias in aliases:
        _BRAND_ALIAS_MAP[alias.upper()] = canonical.upper()

_RU_LETTERS_RE = re.compile(r"[А-ЯЁа-яё]")
_PRICE_RE = re.compile(r"([\d\u00a0\s]+(?:[.,]\d+)?)")
_QTY_RE = re.compile(r"(\d+)\s*шт", re.IGNORECASE)


def normalize_sku(raw: str) -> str:
    """Normalise SKU: uppercase, strip whitespace incl. NBSP."""
    if not raw:
        return ""
    s = str(raw).strip().replace("\u00a0", "").replace("\t", "").upper()
    s = re.sub(r"\s+", "", s)
    return s


def normalize_sku_for_search(sku_norm: str) -> str:
    """Further strip separators for site search boxes (configurable)."""
    if SKU_STRIP_SEPARATORS_FOR_SEARCH:
        return re.sub(r"[-.]", "", sku_norm)
    return sku_norm


def is_valid_sku(sku_norm: str) -> bool:
    if not sku_norm:
        return False
    if _RU_LETTERS_RE.search(sku_norm):
        return False
    return bool(re.search(r"[A-Z0-9]", sku_norm))


def normalize_price(raw: str) -> Optional[float]:
    """
    Parse a Russian-style price string like "136 171 ₽" or "от 135 295 ₽".
    Returns float (roubles) or None if unparseable.
    """
    if not raw:
        return None
    # Strip leading "от"
    raw = re.sub(r"^от\s*", "", raw.strip(), flags=re.IGNORECASE)
    # Remove ₽ / руб and non-numeric except digits, spaces, NBSP, comma, dot
    raw = re.sub(r"[₽рубRUB]", "", raw, flags=re.IGNORECASE)
    m = _PRICE_RE.search(raw)
    if not m:
        return None
    s = m.group(1)
    s = re.sub(r"[\u00a0\s]", "", s)  # remove all spaces/NBSP
    s = s.replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return None


def normalize_availability(raw: str) -> str:
    """
    Normalise availability string to canonical status or quantity info.

    Examples:
        "64 шт"    -> "QTY=64"
        "В наличии" -> "IN_STOCK"
        "под заказ" -> "PREORDER"
        "нет"       -> "OUT_OF_STOCK"
    """
    if not raw:
        return "UNKNOWN"
    s = raw.strip()
    # Quantity pattern: "64 шт"
    m = _QTY_RE.search(s)
    if m:
        qty = m.group(1)
        # Also look for ETA part
        eta_m = re.search(r"(\d+\s*дн|\d+\s*дней|[0-9]{1,2}\s*[а-яА-Я]+)", s, re.IGNORECASE)
        eta = eta_m.group(1).strip() if eta_m and eta_m.start() != m.start() else None
        result = f"QTY={qty}"
        if eta:
            result += f";ETA={eta}"
        return result
    # Status keywords — check negative patterns BEFORE positive to avoid false matches
    sl = s.lower()
    if any(w in sl for w in ("нет в наличии", "отсутствует", "out of stock", "нет на складе")):
        return "OUT_OF_STOCK"
    if any(w in sl for w in ("в наличии", "есть", "in stock", "на складе")):
        return "IN_STOCK"
    if any(w in sl for w in ("под заказ", "на заказ", "preorder", "предзаказ")):
        return "PREORDER"
    if any(w in sl for w in ("требует", "телефон", "phone", "requires_phone")):
        return "REQUIRES_PHONE"
    if any(w in sl for w in ("captcha", "security check", "blocked_captcha")):
        return "REQUIRES_CAPTCHA"
    if any(w in sl for w in ("авторизац", "login", "войти", "requires_login")):
        return "REQUIRES_LOGIN"
    # Fallback: return normalised raw value
    return s[:80]


def normalize_brand(raw: str) -> str:
    """Uppercase, strip, apply synonym mapping."""
    if not raw:
        return ""
    s = " ".join(str(raw).strip().split()).upper()
    return _BRAND_ALIAS_MAP.get(s, s)
