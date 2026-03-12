"""
Extractor module: parses product cards and fitment data from HTML.
"""
import re
import logging
from dataclasses import dataclass, field
from typing import Optional, List, Tuple
from urllib.parse import urlparse

from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

FITMENT_HDR = "Подходит для следующих автомобилей"
BRAND_NUM_PATTERNS = [
    re.compile(r"Бренд[:\s]+([^\n\t]+?)\s+Номер товара[:\s]+([A-Za-z0-9 .\-/]+)", re.IGNORECASE),
    re.compile(r"Бренд[:\s]+([^\n\t]+)", re.IGNORECASE),
]
PRICE_PLN_RE = re.compile(r"([\d\s]+(?:[.,]\d+)?)\s*PLN", re.IGNORECASE)
PART_NUM_RE = re.compile(r"Номер товара[:\s]+([A-Za-z0-9 .\-/]+)", re.IGNORECASE)
PRODUCT_ID_RE = re.compile(r"/(\d+)(?:[/?#]|$)")


@dataclass
class FitmentRow:
    make: Optional[str]
    model: Optional[str]
    raw_line: Optional[str]


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
    fitment_rows: List[FitmentRow] = field(default_factory=list)
    parse_errors: List[str] = field(default_factory=list)


def normalize_part_number(s: Optional[str]) -> Optional[str]:
    if not s:
        return None
    return re.sub(r"[\s\-\.\u00A0/]+", "", s).upper()


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
    matches = PRICE_PLN_RE.findall(text)
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
            if "PLN" in el_text or "zł" in el_text.lower():
                m = PRICE_PLN_RE.search(el_text)
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


def extract_product(url: str, html: str) -> ProductData:
    """Extract all product data from HTML of a product page."""
    soup = BeautifulSoup(html, "lxml")
    text = soup.get_text("\n", strip=True)
    text_space = soup.get_text(" ", strip=True)

    product_id = extract_product_id(url)
    breadcrumb = extract_breadcrumbs(soup)

    # Name from H1
    h1 = soup.find("h1")
    name = h1.get_text(strip=True) if h1 else None

    # Brand and part number
    brand, part_display = extract_brand_and_part(soup, text_space)

    # Price
    price_pln, vat_included = extract_price(soup, text_space)

    # Check currency context
    parse_errors = []
    if price_pln is not None:
        if "PLN" not in text_space and "zł" not in text_space.lower():
            parse_errors.append("currency_not_pln")
            logger.warning(f"Non-PLN currency detected at {url}")

    # Fitment
    fitment_rows = parse_fitment_block(text)
    if not fitment_rows:
        fitment_rows = [FitmentRow(make=None, model=None, raw_line=None)]
        logger.debug(f"No fitment found for {url}")
    else:
        # Deduplicate within this product
        seen = set()
        unique_rows = []
        for r in fitment_rows:
            key = (r.make, r.model)
            if key not in seen:
                seen.add(key)
                unique_rows.append(r)
        fitment_rows = unique_rows

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
        fitment_rows=fitment_rows,
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
