"""
Crawler module: traverses only subcategories within seed URL scope.
"""
import logging
import re
import time
import random
from typing import List, Set, Generator
from urllib.parse import urljoin, urlparse, urlunparse, parse_qs, urlencode

from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

FORBIDDEN_PREFIXES = ["/api/v1/", "/search/", "/pages/", "/blog/"]
BASE_DOMAIN = "2407.pl"

SLUG_WITH_ID_RE = re.compile(r"-(\d{5,})$")

DIRECTORY_MARKERS = ["Все категории", "Показать все категории"]
LISTING_MARKERS = ["Результаты:", "Показать элементы", "Показать еще", "Сортировать по"]
LISTING_CSS = ["CatalogueListItem", "ListItemstyle", "SparePartsItem", "SparePartsList"]
PRODUCT_CSS = ["CatalogueListItemTitle", "ListItemTitle", "SparePartsItemTitle",
               "SparePartsItemLink", "CatalogueListItemTitleLink"]


def is_forbidden_url(url: str) -> bool:
    parsed = urlparse(url)
    path = parsed.path
    for prefix in FORBIDDEN_PREFIXES:
        if path.startswith(prefix):
            return True
    return False


def normalize_url(url: str) -> str:
    parsed = urlparse(url)
    clean = urlunparse((parsed.scheme, parsed.netloc, parsed.path, "", "", ""))
    return clean.rstrip("/") + "/"


def detect_page_type(soup: BeautifulSoup) -> str:
    text = soup.get_text(" ", strip=True)

    if sum(1 for m in LISTING_MARKERS if m in text) >= 1:
        return "listing"
    if sum(1 for m in DIRECTORY_MARKERS if m in text) >= 1:
        return "directory"

    all_classes = set()
    for el in soup.find_all(class_=True):
        for c in el.get("class", []):
            all_classes.add(c)

    if any(any(pat in c for pat in LISTING_CSS) for c in all_classes):
        return "listing"

    product_links = extract_product_links(soup, "https://2407.pl")
    if len(product_links) > 2:
        return "listing"

    return "directory"


def has_product_id(path_parts: list) -> bool:
    """Check if path parts contain a product ID — either a pure numeric segment
    or a slug ending with a long number like 'amortyzator-sc2962-12946833'."""
    for p in path_parts:
        if p.isdigit() and len(p) > 3:
            return True
        if SLUG_WITH_ID_RE.search(p):
            return True
    return False


def extract_subcategory_links(soup: BeautifulSoup, base_url: str,
                               seed_path: str) -> List[str]:
    """
    Extract subcategory links that are children of seed_path.
    e.g. seed_path=/ru/filtry/ → only /ru/filtry/masljannye-filtry/ etc.
    """
    links = set()
    seed_path_norm = seed_path.rstrip("/") + "/"

    for a in soup.find_all("a", href=True):
        href = a["href"]
        full_url = urljoin(base_url, href)
        parsed = urlparse(full_url)

        if parsed.netloc not in (BASE_DOMAIN, "www." + BASE_DOMAIN):
            continue

        path = parsed.path
        if not path.startswith("/ru/"):
            continue

        # KEY FIX: only follow paths that start with the seed path
        if not path.startswith(seed_path_norm):
            continue

        # Skip forbidden
        if is_forbidden_url(full_url):
            continue

        # Skip files
        if any(ext in path for ext in [".jpg", ".png", ".pdf", ".js", ".css"]):
            continue

        path_parts = [p for p in path.strip("/").split("/") if p]

        # Must be deeper than seed (at least one more level)
        seed_parts = [p for p in seed_path_norm.strip("/").split("/") if p]
        if len(path_parts) <= len(seed_parts):
            continue

        # No product IDs = category page (skip pure-digit IDs and slug-IDs)
        if has_product_id(path_parts):
            continue

        # Skip trademark/brand filter URLs
        if "trademark=" in full_url or "brand=" in full_url:
            continue

        links.add(full_url)

    return list(links)


def extract_product_links(soup: BeautifulSoup, base_url: str) -> List[str]:
    links = []
    seen = set()

    # Try product CSS classes first
    for css_pat in PRODUCT_CSS:
        for el in soup.find_all(class_=re.compile(css_pat)):
            a = el if el.name == "a" else el.find("a", href=True)
            if a and a.get("href"):
                href = a["href"]
                full_url = urljoin(base_url, href)
                parsed = urlparse(full_url)
                path = parsed.path
                path_parts = [p for p in path.strip("/").split("/") if p]
                if has_product_id(path_parts):
                    clean = normalize_url(full_url)
                    if clean not in seen:
                        seen.add(clean)
                        links.append(full_url)

    # Fallback: any /ru/ link with numeric ID
    if not links:
        for a in soup.find_all("a", href=True):
            href = a["href"]
            full_url = urljoin(base_url, href)
            parsed = urlparse(full_url)
            if parsed.netloc not in (BASE_DOMAIN, "www." + BASE_DOMAIN):
                continue
            path = parsed.path
            if not path.startswith("/ru/"):
                continue
            path_parts = [p for p in path.strip("/").split("/") if p]
            if not has_product_id(path_parts):
                continue
            if is_forbidden_url(full_url):
                continue
            clean = normalize_url(full_url)
            if clean not in seen:
                seen.add(clean)
                links.append(full_url)

    return links


def extract_pagination_urls(soup: BeautifulSoup, current_url: str,
                             base_url: str) -> List[str]:
    pages = []
    seen = {normalize_url(current_url)}
    selectors = [
        "[class*='pagination'] a", "[class*='Pagination'] a",
        "[class*='pager'] a", "a[rel='next']",
        "[class*='next'] a", "a.next",
        "a[href*='page=']", "a[href*='/page/']",
    ]
    for sel in selectors:
        for a in soup.select(sel):
            href = a.get("href", "")
            if not href:
                continue
            full_url = urljoin(base_url, href)
            parsed = urlparse(full_url)
            if parsed.netloc not in (BASE_DOMAIN, "www." + BASE_DOMAIN):
                continue
            norm = normalize_url(full_url)
            if norm not in seen:
                seen.add(norm)
                pages.append(full_url)
    return pages


def build_per_page_url(url: str, per_page: int = 50) -> str:
    parsed = urlparse(url)
    params = parse_qs(parsed.query, keep_blank_values=True)
    for param in ["limit", "per_page", "count", "items", "pageSize", "show", "perPage"]:
        if param in params:
            params[param] = [str(per_page)]
            new_query = urlencode({k: v[0] for k, v in params.items()})
            return urlunparse((parsed.scheme, parsed.netloc, parsed.path,
                               parsed.params, new_query, parsed.fragment))
    return url


class SitemapParser:
    def __init__(self, renderer):
        self.renderer = renderer

    def get_urls_for_sections(self, sitemap_url: str,
                               section_prefixes: List[str]) -> Set[str]:
        urls = set()
        try:
            html = self.renderer.fetch_html(sitemap_url)
            if not html:
                return urls
            soup = BeautifulSoup(html, "lxml-xml")
            sitemaps = soup.find_all("sitemap")
            if sitemaps:
                for sm in sitemaps:
                    loc = sm.find("loc")
                    if loc:
                        sub = self.get_urls_for_sections(loc.text.strip(), section_prefixes)
                        urls.update(sub)
                return urls
            for url_el in soup.find_all("url"):
                loc = url_el.find("loc")
                if loc:
                    u = loc.text.strip()
                    if any(urlparse(u).path.startswith(p) for p in section_prefixes):
                        urls.add(u)
        except Exception as e:
            logger.warning(f"Sitemap error: {e}")
        return urls


class CategoryCrawler:
    def __init__(self, renderer, base_url: str = "https://2407.pl"):
        self.renderer = renderer
        self.base_url = base_url
        self.visited_dirs: Set[str] = set()
        self.visited_listings: Set[str] = set()

    def crawl_seed(self, seed: dict) -> Generator[dict, None, None]:
        url = seed["url"]
        section = seed["section"]
        subsection = seed.get("subsection", url.rstrip("/").split("/")[-1])
        # seed_path is the root path for this seed — only crawl within it
        seed_path = urlparse(url).path
        logger.info(f"Seed: {section} — {url} (scope: {seed_path})")
        yield from self._crawl_url(url, section, subsection,
                                    source_url=url, seed_path=seed_path, depth=0)

    def _crawl_url(self, url: str, section: str, subsection: str,
                   source_url: str, seed_path: str,
                   depth: int = 0) -> Generator[dict, None, None]:
        if depth > 6:
            logger.warning(f"Max depth at {url}")
            return
        if is_forbidden_url(url):
            return

        norm = normalize_url(url)
        if norm in self.visited_dirs:
            return
        self.visited_dirs.add(norm)

        logger.info(f"Crawling (depth={depth}): {url}")
        html = self.renderer.fetch_html(url)
        if not html:
            logger.warning(f"Empty response: {url}")
            return

        soup = BeautifulSoup(html, "lxml")
        page_type = detect_page_type(soup)
        logger.info(f"Page type: {page_type} — {url}")

        if page_type == "listing":
            yield from self._crawl_listing(url, soup, section, subsection, source_url)
        else:
            # directory or unknown: find subcategories WITHIN seed scope
            subcat_links = extract_subcategory_links(soup, self.base_url, seed_path)
            logger.info(f"Found {len(subcat_links)} subcats within {seed_path}")

            if subcat_links:
                for sub_url in subcat_links:
                    yield from self._crawl_url(sub_url, section, subsection,
                                                source_url=source_url,
                                                seed_path=seed_path,
                                                depth=depth + 1)
            else:
                # No subcats found — treat as listing
                product_links = extract_product_links(soup, self.base_url)
                if product_links:
                    logger.info(f"No subcats, treating as listing: {url}")
                    yield from self._crawl_listing(url, soup, section, subsection, source_url)
                else:
                    logger.warning(f"No subcats and no products at: {url}")

    def _crawl_listing(self, listing_url: str, soup: BeautifulSoup,
                        section: str, subsection: str,
                        source_url: str) -> Generator[dict, None, None]:
        norm = normalize_url(listing_url)
        if norm in self.visited_listings:
            return
        self.visited_listings.add(norm)

        max_url = build_per_page_url(listing_url, 50)
        if max_url != listing_url:
            html2 = self.renderer.fetch_html(max_url)
            if html2:
                listing_url = max_url
                soup = BeautifulSoup(html2, "lxml")

        pages_to_visit = [listing_url]
        visited_pages = {normalize_url(listing_url)}
        page_idx = 0

        while page_idx < len(pages_to_visit):
            page_url = pages_to_visit[page_idx]
            page_idx += 1

            if page_idx > 1:
                html = self.renderer.fetch_html(page_url)
                if not html:
                    continue
                soup = BeautifulSoup(html, "lxml")

            product_links = extract_product_links(soup, self.base_url)
            logger.info(f"Page {page_idx}: {len(product_links)} products at {page_url}")

            for product_url in product_links:
                yield {
                    "product_url": product_url,
                    "source_section": section,
                    "source_subsection": subsection,
                    "source_url": page_url,
                }

            new_pages = extract_pagination_urls(soup, page_url, self.base_url)
            for np in new_pages:
                norm_np = normalize_url(np)
                if norm_np not in visited_pages:
                    visited_pages.add(norm_np)
                    pages_to_visit.append(np)
