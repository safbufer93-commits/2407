"""
Extractor module: parses product cards and fitment data from HTML.
"""
import re
import logging
from dataclasses import dataclass, field
from typing import Optional, List, Tuple

from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

FITMENT_HDR = "Подходит для следующих автомобилей"
BRAND_NUM_PATTERNS = [
    re.compile(r"Бренд[:\s]+([^\n\t]+?)\s+Номер товара[:\s]+([A-Za-z0-9 .\-/]+)", re.IGNORECASE),
    re.compile(r"Бренд[:\s]+([^\n\t]+)", re.IGNORECASE),
]
PRICE_RE = re.compile(r"([\d\s]+(?:[.,]\d+)?)\s*(?:PLN|z(?:ł|l))", re.IGNORECASE)
PART_NUM_RE = re.compile(r"Номер товара[:\s]+([A-Za-z0-9 .\-/]+)", re.IGNORECASE)
PRODUCT_ID_RE = re.compile(r"/(\d+)(?:[/?#]|$)")
PRODUCT_ID_SLUG_RE = re.compile(r"-(\d{5,})(?:[/?#]|$)")
PART_TOKEN_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9\-./]{2,}")
PART_CHUNK_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9\-./]*$")


@dataclass
class FitmentRow:
    make: Optional[str]
    model: Optional[str]
    model_type: Optional[str] = None
    modification: Optional[str] = None
    raw_line: Optional[str] = None


@dataclass
class OriginalNumberRow:
    brand: Optional[str]
    number: Optional[str]


@dataclass
class AnalogRow:
    brand: Optional[str]
    number: Optional[str]


@dataclass
class ProductData:
    product_url: str
    product_id: Optional[int]
    name: Optional[str]
    brand: Optional[str]
    part_number_display: Optional[str]
    part_number_normalized: Optional[str]
    price_pln: Optional[float]
    vat_included: bool
    breadcrumb_path: Optional[str]
    characteristics: Optional[str] = None
    fitment_rows: List[FitmentRow] = field(default_factory=list)
    original_numbers: List[OriginalNumberRow] = field(default_factory=list)
    analog_rows: List[AnalogRow] = field(default_factory=list)
    parse_errors: List[str] = field(default_factory=list)


def normalize_part_number(s: Optional[str]) -> Optional[str]:
    if not s:
        return None
    return re.sub(r"[\s\-\.\u00A0/]+", "", s).upper()


def _clean_text(value: Optional[str]) -> str:
    return re.sub(r"\s+", " ", (value or "")).strip()


def _clean_product_name(raw_name: Optional[str]) -> Optional[str]:
    """
    Keep only the product title in NAME and strip inline meta fragments like:
    "Бренд: ...", "Номер товара: ...", "Brand: ...", "Part number: ...".
    """
    text = _clean_text(raw_name)
    if not text:
        return None

    for marker in ("Бренд:", "Номер товара:", "Brand:", "Part number:"):
        idx = text.lower().find(marker.lower())
        if idx > 0:
            text = text[:idx].strip()
            break

    # Defensive regex cleanup for compact formats without separators.
    text = re.sub(r"\s*(Бренд|Brand)\s*:.*$", "", text, flags=re.IGNORECASE).strip()
    text = re.sub(r"\s*(Номер товара|Part number)\s*:.*$", "", text, flags=re.IGNORECASE).strip()
    return text or None


def _looks_like_part_number(value: Optional[str]) -> bool:
    if not value:
        return False
    token = _clean_text(value).upper().replace(" ", "")
    if len(token) < 3 or len(token) > 48:
        return False
    if not any(ch.isdigit() for ch in token):
        return False
    return bool(re.fullmatch(r"[A-Z0-9][A-Z0-9\-./]*", token))


def _extract_part_number_from_text(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    source = _clean_text(value).upper()
    tokens = [t.strip(".,;:()[]{}") for t in source.split()]
    for i in range(len(tokens)):
        if not PART_CHUNK_RE.match(tokens[i]):
            continue
        # Start from tokens that already carry digits to avoid capturing brand words.
        if not any(ch.isdigit() for ch in tokens[i]):
            continue
        parts = []
        best = None
        for j in range(i, min(i + 6, len(tokens))):
            token = tokens[j].strip(".,;:()[]{}")
            if not PART_CHUNK_RE.match(token):
                break
            parts.append(token)
            candidate = " ".join(parts).strip()
            if _looks_like_part_number(candidate):
                best = candidate
        if best:
            return best
    for token in PART_TOKEN_RE.findall(source):
        normalized = token.strip(".,;:()[]{}")
        if _looks_like_part_number(normalized):
            return normalized
    return None


def _split_brand_and_number(line: Optional[str]) -> Tuple[Optional[str], Optional[str]]:
    """
    Split a string like "VAG 1K0 411 315 R Стойка..." into:
      brand = "VAG"
      number = "1K0 411 315 R"
    """
    text = _clean_text(line)
    if not text:
        return None, None

    tokens = [t.strip(".,;:()[]{}") for t in text.split() if t.strip(".,;:()[]{}")]
    if len(tokens) < 2:
        return None, None

    # Brand tokens: before first token containing a digit.
    first_digit_idx = None
    for idx, token in enumerate(tokens):
        if any(ch.isdigit() for ch in token):
            first_digit_idx = idx
            break
    if first_digit_idx is None:
        return None, None

    if first_digit_idx == 0:
        # Some brands may contain digits; treat first token as brand.
        brand_tokens = [tokens[0]]
        start_idx = 1
    else:
        brand_tokens = tokens[:first_digit_idx]
        start_idx = first_digit_idx

    if not brand_tokens or start_idx >= len(tokens):
        return None, None

    number_tokens = []
    for token in tokens[start_idx:]:
        if not PART_CHUNK_RE.match(token):
            break
        number_tokens.append(token)
        if len(number_tokens) >= 6:
            break

    brand = " ".join(brand_tokens).strip() or None
    number = " ".join(number_tokens).strip() or None
    if not _looks_like_part_number(number):
        return brand, None

    if brand and len(brand) > 60:
        brand = None

    return brand, number


def _extract_text_section(text: str, start_markers: List[str],
                          end_markers: List[str], max_len: int = 12000) -> str:
    lower = text.lower()
    start_pos = -1
    start_len = 0

    for marker in start_markers:
        idx = lower.find(marker.lower())
        if idx != -1 and (start_pos == -1 or idx < start_pos):
            start_pos = idx
            start_len = len(marker)

    if start_pos == -1:
        return ""

    section = text[start_pos + start_len:]
    section_lower = section.lower()
    cut_idx = len(section)

    for marker in end_markers:
        idx = section_lower.find(marker.lower())
        if idx != -1 and idx < cut_idx:
            cut_idx = idx

    return section[:min(cut_idx, max_len)]


def _dedupe_pairs(pairs: List[Tuple[Optional[str], Optional[str]]]) -> List[Tuple[Optional[str], Optional[str]]]:
    seen = set()
    unique = []
    for brand, number in pairs:
        brand_clean = _clean_text(brand) or None
        number_clean = _clean_text(number) or None
        if not number_clean:
            continue
        key = ((brand_clean or "").lower(), number_clean.upper())
        if key in seen:
            continue
        seen.add(key)
        unique.append((brand_clean, number_clean))
    return unique


def extract_product_id(url: str) -> Optional[int]:
    """Extract numeric product ID from URL."""
    # Try last numeric segment
    parts = url.rstrip("/").split("/")
    for part in reversed(parts):
        if part.isdigit():
            return int(part)
    # Try regex
    m = PRODUCT_ID_RE.search(url)
    if m:
        return int(m.group(1))
    # Common slug format: /slug-12345678/
    m = PRODUCT_ID_SLUG_RE.search(url)
    if m:
        return int(m.group(1))
    return None


def extract_breadcrumbs(soup: BeautifulSoup) -> Optional[str]:
    """Extract breadcrumb navigation path."""
    # Common breadcrumb selectors
    selectors = [
        ".breadcrumb", "[class*='breadcrumb']", "nav[aria-label*='bread']",
        ".crumbs", "[class*='crumb']", "ol.breadcrumb", "ul.breadcrumb",
        "[itemtype*='BreadcrumbList']"
    ]
    for sel in selectors:
        el = soup.select_one(sel)
        if el:
            parts = [a.get_text(strip=True) for a in el.find_all(["a", "span", "li"])
                     if a.get_text(strip=True)]
            if parts:
                return " > ".join(parts)

    # Try schema.org breadcrumb
    items = soup.select("[itemprop='name']")
    if items:
        parts = [i.get_text(strip=True) for i in items if i.get_text(strip=True)]
        if len(parts) > 1:
            return " > ".join(parts)
    return None


def extract_price(soup: BeautifulSoup, text: str) -> Tuple[Optional[float], bool]:
    """Extract price in PLN and VAT flag."""
    vat_included = "с НДС" in text or "z VAT" in text.lower() or "brutto" in text.lower()

    # Search in text
    matches = PRICE_RE.findall(text)
    if matches:
        for m in matches:
            clean = m.replace(" ", "").replace("\xa0", "").replace(",", ".")
            try:
                val = float(clean)
                if val > 0:
                    return val, vat_included
            except ValueError:
                pass

    # Try price elements
    price_selectors = [
        "[class*='price']", "[class*='Price']", "[itemprop='price']",
        ".price", "#price", "[class*='cost']"
    ]
    for sel in price_selectors:
        for el in soup.select(sel):
            el_text = el.get_text(" ", strip=True)
            m = PRICE_RE.search(el_text)
            if m:
                clean = m.group(1).replace(" ", "").replace("\xa0", "").replace(",", ".")
                try:
                    val = float(clean)
                    if val > 0:
                        vat_here = "с НДС" in el_text or "z VAT" in el_text.lower()
                        return val, vat_here or vat_included
                except ValueError:
                    pass
    return None, vat_included


def extract_brand_and_part(soup: BeautifulSoup, text: str) -> Tuple[Optional[str], Optional[str]]:
    """Extract brand and part number."""
    # Try structured elements first
    brand = None
    part_num = None

    # Look for labeled elements
    for el in soup.find_all(["dt", "th", "span", "div", "td", "li"]):
        el_text = el.get_text(strip=True)
        if el_text.lower() in ("бренд", "brand", "производитель"):
            next_el = el.find_next_sibling()
            if next_el:
                brand = next_el.get_text(strip=True)
                break
        if el_text.lower() in ("номер товара", "артикул", "part number", "sku"):
            next_el = el.find_next_sibling()
            if next_el:
                part_num = next_el.get_text(strip=True)

    # Fallback: regex on full text
    if not brand:
        for pattern in BRAND_NUM_PATTERNS:
            m = pattern.search(text)
            if m:
                brand = m.group(1).strip()
                if len(m.groups()) > 1:
                    part_num = m.group(2).strip()
                break

    if not part_num:
        m = PART_NUM_RE.search(text)
        if m:
            part_num = m.group(1).strip()

    # Clean brand (remove extra words)
    if brand:
        brand = brand.split("\n")[0].split("  ")[0].strip()
        if len(brand) > 100:
            brand = None

    return brand, part_num


def parse_fitment_block(text: str) -> List[FitmentRow]:
    """Parse the fitment block from product page text."""
    rows = []

    # Find fitment header
    hdr_variants = [
        "Подходит для следующих автомобилей:",
        "Подходит для следующих автомобилей",
        "Совместимость с автомобилями",
        "Подходящие автомобили",
    ]

    block_start = -1
    for hdr in hdr_variants:
        idx = text.find(hdr)
        if idx != -1:
            block_start = idx + len(hdr)
            break

    if block_start == -1:
        return []

    # Extract block until next major section
    block = text[block_start:]

    # Cut at next section markers
    section_markers = ["\n##", "\n# ", "\nКупить", "\nДобавить в корзину",
                       "\nОписание\n", "\nХарактеристики\n", "\nОтзывы\n"]
    for marker in section_markers:
        idx = block.find(marker)
        if idx != -1 and idx < 5000:
            block = block[:idx]

    # Limit block size
    block = block[:5000]

    # Parse lines
    lines = [ln.strip(" *•\t-") for ln in block.splitlines()]
    for line in lines:
        line = line.strip()
        if not line or len(line) < 3:
            continue
        if ":" not in line:
            continue
        # Skip lines that look like labels/headers
        lower = line.lower()
        if any(skip in lower for skip in ["подходит", "совместим", "автомобил", "купить",
                                           "добавить", "цена", "описание"]):
            continue

        colon_idx = line.index(":")
        make = line[:colon_idx].strip()
        models_str = line[colon_idx + 1:].strip()

        # Validate make (should be a car brand, not a long description)
        if len(make) < 1 or len(make) > 50 or "\n" in make:
            continue
        if not make or make.isdigit():
            continue

        # Split models by comma
        models = [m.strip() for m in models_str.split(",") if m.strip()]
        if not models:
            # Try semicolons
            models = [m.strip() for m in models_str.split(";") if m.strip()]
        if not models and models_str.strip():
            models = [models_str.strip()]

        for model in models:
            if model and len(model) <= 100:
                rows.append(FitmentRow(
                    make=make,
                    model=model,
                    raw_line=line
                ))

    return rows


def _normalize_fitment_cell(value: Optional[str]) -> Optional[str]:
    text = _clean_text(value)
    if not text:
        return None
    low = text.lower()
    if low in {"-", "—", "n/a", "null", "none"}:
        return None
    if low in {
        "марка", "модель", "тип модели", "модификации",
        "model", "modification", "type", "make", "brand",
    }:
        return None
    return text


def parse_fitment_table(soup: BeautifulSoup) -> List[FitmentRow]:
    """Parse compatibility table on the 'Совместимость с автомобилем' tab."""
    rows: List[FitmentRow] = []

    for table in soup.find_all("table"):
        tr_nodes = table.find_all("tr")
        if len(tr_nodes) < 2:
            continue

        header_cells = tr_nodes[0].find_all(["th", "td"])
        headers = [_clean_text(c.get_text(" ", strip=True)).lower() for c in header_cells]
        if not headers:
            continue

        has_make_col = any(("марка" in h) or ("make" in h) or (h == "brand") for h in headers)
        has_modelish_col = any(
            ("модель" in h) or ("model" in h) or ("тип" in h) or ("модификац" in h)
            for h in headers
        )
        if not (has_make_col and has_modelish_col):
            continue

        idx_make = next(
            (i for i, h in enumerate(headers) if ("марка" in h) or ("make" in h) or (h == "brand")),
            None,
        )
        idx_model = next((i for i, h in enumerate(headers) if ("модель" in h) or ("model" in h)), None)
        idx_type = next((i for i, h in enumerate(headers) if ("тип" in h) and ("мод" in h or "model" in h)), None)
        idx_mod = next((i for i, h in enumerate(headers) if ("модификац" in h) or ("modification" in h)), None)
        if idx_make is None:
            continue

        for tr in tr_nodes[1:]:
            cells = tr.find_all(["td", "th"])
            if not cells:
                continue
            values = [_clean_text(c.get_text(" ", strip=True)) for c in cells]
            if not any(values):
                continue

            make = _normalize_fitment_cell(values[idx_make] if idx_make < len(values) else None)
            model = _normalize_fitment_cell(values[idx_model] if idx_model is not None and idx_model < len(values) else None)
            model_type = _normalize_fitment_cell(values[idx_type] if idx_type is not None and idx_type < len(values) else None)
            modification = _normalize_fitment_cell(values[idx_mod] if idx_mod is not None and idx_mod < len(values) else None)

            if not any([make, model, model_type, modification]):
                continue

            raw_chunks = [c for c in [make, model, model_type, modification] if c]
            raw_line = " | ".join(raw_chunks) if raw_chunks else None
            rows.append(
                FitmentRow(
                    make=make,
                    model=model,
                    model_type=model_type,
                    modification=modification,
                    raw_line=raw_line,
                )
            )

    return rows


def parse_fitment_section(text: str) -> List[FitmentRow]:
    """
    Parse compatibility section text where table cells can flatten to plain lines,
    e.g. only 'Nissan' without colon-formatted rows.
    """
    rows: List[FitmentRow] = []
    section = _extract_text_section(
        text,
        ["Совместимость с автомобилем", "Совместимость с автомобилями", "Compatible vehicles"],
        ["Отзывы", "Вас также может заинтересовать", "Оригинальные номера", "Аналоги", "Оригинальные предложения"],
        max_len=8000,
    )
    if not section:
        return rows

    skip_exact = {
        "марка", "модель", "тип модели", "модификации",
        "choose modification", "выберите модификацию", "совместимость с автомобилем",
    }

    for raw in section.splitlines():
        line = _clean_text(raw)
        if not line or len(line) < 2:
            continue

        lower = line.lower()
        if lower in skip_exact:
            continue
        if "совместим" in lower or "автомобил" in lower:
            continue
        if _looks_like_part_number(line):
            continue

        # Preserve old "Make: model1, model2" behavior.
        if ":" in line:
            left, right = line.split(":", 1)
            make = _clean_text(left)
            models = [_clean_text(x) for x in re.split(r"[,;]", right) if _clean_text(x)]
            if not make or make.isdigit():
                continue
            if not models:
                rows.append(FitmentRow(make=make, model=None, raw_line=line))
                continue
            for model in models:
                rows.append(FitmentRow(make=make, model=model, raw_line=line))
            continue

        # Single-line make (e.g. "Nissan") from compatibility tab.
        if len(line) <= 60 and any(ch.isalpha() for ch in line):
            rows.append(FitmentRow(make=line, model=None, raw_line=line))

    return rows


def _dedupe_fitment_rows(rows: List[FitmentRow]) -> List[FitmentRow]:
    seen = set()
    unique: List[FitmentRow] = []
    for row in rows:
        key = (
            (row.make or "").strip().lower(),
            (row.model or "").strip().lower(),
            (row.model_type or "").strip().lower(),
            (row.modification or "").strip().lower(),
        )
        if key in seen:
            continue
        seen.add(key)
        unique.append(row)
    return unique


def extract_characteristics(soup: BeautifulSoup, text: str = "") -> Optional[str]:
    """Extract product characteristics/specs as a semicolon-separated string."""
    # Try common specs table patterns
    for selector in [
        "[class*='haracteristic']", "[class*='Characteristic']",
        "[class*='pecification']", "[class*='Specification']",
        "[class*='Params']", "[class*='params']",
        "[class*='Attrs']", "[class*='attrs']",
        "table.params", ".product-params",
    ]:
        container = soup.select_one(selector)
        if container:
            rows = container.find_all("tr")
            if rows:
                parts = []
                for row in rows:
                    cells = row.find_all(["td", "th"])
                    if len(cells) >= 2:
                        key = cells[0].get_text(strip=True)
                        val = cells[1].get_text(strip=True)
                        if key and val:
                            parts.append(f"{key}: {val}")
                if parts:
                    return "; ".join(parts)
            # No table rows — try key/value divs
            keys = container.find_all(class_=re.compile(r"[Ll]abel|[Nn]ame|[Kk]ey"))
            vals = container.find_all(class_=re.compile(r"[Vv]alue|[Dd]ata"))
            if keys and vals:
                parts = []
                for k, v in zip(keys, vals):
                    kk = k.get_text(strip=True)
                    vv = v.get_text(strip=True)
                    if kk and vv:
                        parts.append(f"{kk}: {vv}")
                if parts:
                    return "; ".join(parts)

    # Fallback: parse plain text section under 'Характеристики'.
    if text:
        section = _extract_text_section(
            text,
            ["Характеристики", "Характеристики\n", "Specifications"],
            [
                "Информация о производителе",
                "О производителе",
                "Оригинальные предложения",
                "Аналоги",
                "Оригинальные номера",
                "Совместимость с автомобилем",
                "Отзывы",
            ],
            max_len=6000,
        )
        if section:
            raw_lines = [_clean_text(x) for x in section.splitlines()]
            raw_lines = [x for x in raw_lines if x]

            parts: List[str] = []
            skipped_words = {"характеристики", "specifications"}

            i = 0
            while i < len(raw_lines):
                line = raw_lines[i]
                lower = line.lower()
                if lower in skipped_words:
                    i += 1
                    continue

                if ":" in line:
                    key, value = line.split(":", 1)
                    key = _clean_text(key)
                    value = _clean_text(value)

                    # Layout with key on one line and value on next line:
                    # "Диаметр трубы [мм]:" + "10"
                    if key and not value and i + 1 < len(raw_lines):
                        next_line = _clean_text(raw_lines[i + 1])
                        next_low = next_line.lower()
                        if next_line and ":" not in next_line and next_low not in skipped_words:
                            value = next_line
                            i += 1

                    if key and value and len(key) <= 120 and len(value) <= 220:
                        parts.append(f"{key}: {value}")
                    i += 1
                    continue

                i += 1

            # Fallback for two-column text flattening:
            # [key1:, key2:, ... value1, value2, ...]
            if not parts:
                for start in range(len(raw_lines)):
                    keys = []
                    j = start
                    while j < len(raw_lines):
                        candidate = _clean_text(raw_lines[j])
                        if not candidate or candidate.lower() in skipped_words:
                            j += 1
                            continue
                        if not candidate.endswith(":"):
                            break
                        key = _clean_text(candidate[:-1])
                        if not key or len(key) > 120:
                            break
                        keys.append(key)
                        j += 1

                    if len(keys) < 2:
                        continue

                    values = []
                    k = j
                    while k < len(raw_lines) and len(values) < len(keys):
                        candidate = _clean_text(raw_lines[k])
                        low = candidate.lower()
                        if not candidate or low in skipped_words:
                            k += 1
                            continue
                        if candidate.endswith(":"):
                            break
                        if ":" in candidate:
                            break
                        if len(candidate) > 220:
                            break
                        values.append(candidate)
                        k += 1

                    if values:
                        for key, value in zip(keys, values):
                            if key and value:
                                parts.append(f"{key}: {value}")
                        if parts:
                            break

            if parts:
                # Keep stable order while removing duplicates
                deduped = list(dict.fromkeys(parts))
                return "; ".join(deduped)

    return None


def extract_original_numbers(soup: BeautifulSoup, text: str) -> List[OriginalNumberRow]:
    """Extract OE/original numbers as brand + number pairs."""
    pairs: List[Tuple[Optional[str], Optional[str]]] = []

    # 1) Structured table parse.
    for table in soup.find_all("table"):
        tr_nodes = table.find_all("tr")
        if len(tr_nodes) < 2:
            continue

        headers = [_clean_text(x.get_text(" ", strip=True)).lower()
                   for x in tr_nodes[0].find_all(["th", "td"])]
        if not headers:
            continue

        has_brand_col = any(("марка" in h) or ("бренд" in h) or ("brand" in h) or ("maker" in h)
                            for h in headers)
        has_num_col = any(("номер" in h) or ("артикул" in h) or ("number" in h) or ("oe" in h)
                          for h in headers)
        if not (has_brand_col and has_num_col):
            continue

        idx_brand = next(
            (i for i, h in enumerate(headers) if ("марка" in h) or ("бренд" in h) or ("brand" in h) or ("maker" in h)),
            None,
        )
        idx_num = next(
            (i for i, h in enumerate(headers) if ("номер" in h) or ("артикул" in h) or ("number" in h) or ("oe" in h)),
            None,
        )
        if idx_num is None:
            continue

        for tr in tr_nodes[1:]:
            cells = tr.find_all(["td", "th"])
            if not cells:
                continue
            values = [_clean_text(c.get_text(" ", strip=True)) for c in cells]
            if idx_num >= len(values):
                continue
            brand = values[idx_brand] if (idx_brand is not None and idx_brand < len(values)) else None
            number = _extract_part_number_from_text(values[idx_num])
            if number:
                pairs.append((brand, number))

    # 2) Text fallback from "Оригинальные номера" tab/section.
    section = _extract_text_section(
        text,
        ["Оригинальные номера", "Original numbers", "OE numbers"],
        ["Совместимость с автомобилем", "Отзывы", "Вас также может заинтересовать"],
        max_len=7000,
    )
    if section:
        pending_brand: Optional[str] = None
        for raw in section.splitlines():
            line = _clean_text(raw)
            if not line:
                continue
            low = line.lower()
            if any(marker in low for marker in ("оригиналь", "номер", "brand", "марка", "oe", "oem")):
                continue

            # Combined line: "Nissan 48611-26J00" or "VAG 1K0 411 315".
            brand_split, number_split = _split_brand_and_number(line)
            if number_split:
                pairs.append((brand_split, number_split))
                pending_brand = None
                continue

            num = _extract_part_number_from_text(line)
            if num and pending_brand:
                pairs.append((pending_brand, num))
                pending_brand = None
                continue

            if not num and len(line) <= 60 and any(ch.isalpha() for ch in line):
                pending_brand = line

    unique_pairs = _dedupe_pairs(pairs)
    return [OriginalNumberRow(brand=b, number=n) for b, n in unique_pairs]


def extract_analogs(soup: BeautifulSoup, text: str) -> List[AnalogRow]:
    """Extract analog/replacement rows as brand + number pairs."""
    pairs: List[Tuple[Optional[str], Optional[str]]] = []

    # 1) Structured analog table parse.
    for table in soup.find_all("table"):
        tr_nodes = table.find_all("tr")
        if len(tr_nodes) < 2:
            continue
        headers = [_clean_text(x.get_text(" ", strip=True)).lower()
                   for x in tr_nodes[0].find_all(["th", "td"])]
        if not headers:
            continue

        # Typical analog table headers: "Наименование", "Количество", "Цена ..."
        if not any("наимен" in h or "name" in h for h in headers):
            continue
        if not any(("цен" in h) or ("price" in h) or ("колич" in h) or ("qty" in h) for h in headers):
            continue

        for tr in tr_nodes[1:]:
            cells = tr.find_all(["td", "th"])
            if not cells:
                continue
            first_cell = cells[0]
            links = [_clean_text(a.get_text(" ", strip=True)) for a in first_cell.find_all("a")]

            brand = None
            number = None
            if len(links) >= 2 and _looks_like_part_number(links[1]):
                brand = links[0]
                number = _clean_text(links[1])
            else:
                cell_text = _clean_text(first_cell.get_text(" ", strip=True))
                brand, number = _split_brand_and_number(cell_text)

            if number:
                pairs.append((brand, number))

    # 2) Text fallback for tab content.
    section = _extract_text_section(
        text,
        ["Аналоги (заменители)", "Аналоги", "Заменители", "Replacements", "Analogs"],
        ["Оригинальные номера", "Совместимость с автомобилем", "Отзывы", "Вас также может заинтересовать"],
        max_len=9000,
    )
    if section:
        for raw in section.splitlines():
            line = _clean_text(raw)
            if not line:
                continue
            lower = line.lower()
            if any(s in lower for s in ("аналоги", "заменител", "наименование", "цена", "количество", "купить")):
                continue
            brand, number = _split_brand_and_number(line)
            if number:
                pairs.append((brand, number))

    unique_pairs = _dedupe_pairs(pairs)
    return [AnalogRow(brand=b, number=n) for b, n in unique_pairs]


def extract_product(url: str, html: str) -> ProductData:
    """Extract all product data from HTML of a product page."""
    soup = BeautifulSoup(html, "lxml")
    text = soup.get_text("\n", strip=True)
    text_space = soup.get_text(" ", strip=True)

    product_id = extract_product_id(url)
    breadcrumb = extract_breadcrumbs(soup)

    # Name from H1 (title only, without inline brand/part meta)
    h1 = soup.find("h1")
    name = _clean_product_name(h1.get_text(" ", strip=True) if h1 else None)

    # Brand and part number
    brand, part_display = extract_brand_and_part(soup, text_space)

    # Price
    price_pln, vat_included = extract_price(soup, text_space)

    # Check currency context
    parse_errors = []
    if price_pln is not None:
        if not re.search(r"\bPLN\b|z(?:ł|l)", text_space, re.IGNORECASE):
            parse_errors.append("currency_not_pln")
            logger.warning(f"Non-PLN currency detected at {url}")

    # Characteristics
    characteristics = extract_characteristics(soup, text)

    # Additional product matrices
    original_numbers = extract_original_numbers(soup, text)
    analog_rows = extract_analogs(soup, text)

    # Fitment (combine table and text-based parsers)
    fitment_rows = []
    fitment_rows.extend(parse_fitment_table(soup))
    fitment_rows.extend(parse_fitment_block(text))
    fitment_rows.extend(parse_fitment_section(text))
    fitment_rows = _dedupe_fitment_rows(fitment_rows)

    if not fitment_rows:
        fitment_rows = [FitmentRow(make=None, model=None, raw_line=None)]
        logger.debug(f"No fitment found for {url}")

    return ProductData(
        product_url=url,
        product_id=product_id,
        name=name,
        brand=brand,
        part_number_display=part_display,
        part_number_normalized=normalize_part_number(part_display),
        price_pln=price_pln,
        vat_included=vat_included,
        breadcrumb_path=breadcrumb,
        characteristics=characteristics,
        fitment_rows=fitment_rows,
        original_numbers=original_numbers,
        analog_rows=analog_rows,
        parse_errors=parse_errors
    )


def looks_complete(html: str) -> bool:
    """Check if HTML appears to have full product data."""
    if not html:
        return False
    soup = BeautifulSoup(html, "lxml")
    has_h1 = soup.find("h1") is not None
    text = soup.get_text(" ", strip=True)
    has_price = "PLN" in text or "zł" in text.lower()
    # Check not just a JS error page
    has_content = len(text) > 200
    return has_h1 and has_content
