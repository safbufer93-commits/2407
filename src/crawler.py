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

FORBIDDEN_PREFIXES = ["/api/v1/", "/search/", "/pages/", "/blog/",
                      "/ru/pages/", "/ru/blog/", "/ru/catalogue/"]

# Patterns in path that indicate car-brand / make filter pages (not product categories)
BRAND_PATH_RE = re.compile(r"/([\w-]+-cars|[\w-]+-brand|[\w-]+-auto)/?$", re.I)
BASE_DOMAIN = "2407.pl"

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

    product_links = extract_product_links(soup, "https://2407.pl")  # no category_url, type detection only
    if len(product_links) > 2:
        return "listing"

    return "directory"


def _has_tile_image(a_tag) -> bool:
    """Return True if <a> is a category tile (has an <img> in self, parent, or grandparent).
    Category tiles always have a product/category image; plain nav links do not.
    """
    if a_tag.find("img"):
        return True
    parent = a_tag.parent
    if parent and parent.find("img"):
        return True
    gp = parent.parent if parent else None
    if gp and gp.find("img"):
        return True
    return False


def extract_subcategory_links(soup: BeautifulSoup, base_url: str,
                               seed_path: str,
                               strict: bool = True) -> List[str]:
    """
    Extract subcategory links found on a directory page.
    strict=True: only links nested under seed_path (e.g. /ru/filtry/sub/)
    strict=False: content-area /ru/category/ links only (excludes nav/header/footer)
    """
    links = set()
    seed_path_norm = seed_path.rstrip("/") + "/"
    seed_parts = [p for p in seed_path_norm.strip("/").split("/") if p]

    for a in soup.find_all("a", href=True):
        # In flat mode only follow tile-style links (those with a nearby image)
        if not strict and not _has_tile_image(a):
            continue

        href = a["href"]
        full_url = urljoin(base_url, href)
        parsed = urlparse(full_url)

        if parsed.netloc not in (BASE_DOMAIN, "www." + BASE_DOMAIN):
            continue

        path = parsed.path
        if not path.startswith("/ru/"):
            continue

        # In strict mode, only follow paths nested under seed_path
        if strict and not path.startswith(seed_path_norm):
            continue

        # Skip forbidden (includes /ru/pages/, /ru/blog/, /ru/catalogue/)
        if is_forbidden_url(full_url):
            continue

        # Skip car-brand / make filter pages
        if BRAND_PATH_RE.search(path):
            continue

        # Skip files
        if any(ext in path for ext in [".jpg", ".png", ".pdf", ".js", ".css"]):
            continue

        path_parts = [p for p in path.strip("/").split("/") if p]

        # Must be at least /ru/something/ (depth >= 2)
        if len(path_parts) < 2:
            continue

        # In strict mode must be deeper than seed; in flat mode must be >= seed depth
        if strict and len(path_parts) <= len(seed_parts):
            continue

        # Skip the seed page itself
        if path == seed_path_norm or path.rstrip("/") == seed_path_norm.rstrip("/"):
            continue

        # No numeric IDs = category page
        if any(p.isdigit() and len(p) > 4 for p in path_parts):
            continue

        # Skip trademark/brand filter URLs
        if "trademark=" in full_url or "brand=" in full_url:
            continue

        links.add(full_url)

    return list(links)


CAR_FILTER_RE = re.compile(r"/([\w-]+-cars|[\w-]+-brand|[\w-]+-auto)/", re.I)


def _is_car_filter_url(path: str) -> bool:
    """Return True if path contains a car-make/brand filter segment anywhere."""
    return bool(BRAND_PATH_RE.search(path) or CAR_FILTER_RE.search(path))


def extract_product_links(soup: BeautifulSoup, base_url: str,
                          category_url: str = None) -> List[str]:
    links = []
    seen = set()

    # Try product CSS classes first — accept any depth >= 3 (/ru/cat/product/)
    for css_pat in PRODUCT_CSS:
        for el in soup.find_all(class_=re.compile(css_pat)):
            a = el if el.name == "a" else el.find("a", href=True)
            if a and a.get("href"):
                href = a["href"]
                full_url = urljoin(base_url, href)
                parsed = urlparse(full_url)
                if parsed.netloc not in (BASE_DOMAIN, "www." + BASE_DOMAIN):
                    continue
                path = parsed.path
                if _is_car_filter_url(path):
                    continue
                if is_forbidden_url(full_url):
                    continue
                path_parts = [p for p in path.strip("/").split("/") if p]
                # Skip trademark/brand filter pages — they are listings, not products
                if any(p.startswith("trademark=") or p.startswith("brand=")
                       for p in path_parts):
                    continue
                # Product pages are at least /ru/category/product/ (depth 3+)
                if len(path_parts) < 3:
                    continue
                clean = normalize_url(full_url)
                if clean not in seen:
                    seen.add(clean)
                    links.append(full_url)

    # Depth-based fallback: find links exactly one level deeper than the category page.
    # This handles sites where product URLs are slug-based without numeric IDs,
    # e.g. /ru/barabany.../bosch-0986477133/ vs /ru/barabany.../
    if not links and category_url:
        cat_path = urlparse(category_url).path.rstrip("/") + "/"
        # Strip trademark/brand filter segments (e.g. trademark=ashika) so that
        # product URLs like /ru/category/product/ are correctly matched against
        # the real parent category path /ru/category/ instead of /ru/category/trademark=ashika/
        raw_parts = [p for p in cat_path.strip("/").split("/") if p]
        clean_parts = [p for p in raw_parts
                       if not p.startswith("trademark=") and not p.startswith("brand=")]
        if clean_parts != raw_parts:
            cat_path = "/" + "/".join(clean_parts) + "/"
        cat_parts = [p for p in cat_path.strip("/").split("/") if p]
        expected_depth = len(cat_parts) + 1

        for a in soup.find_all("a", href=True):
            href = a["href"]
            full_url = urljoin(base_url, href)
            parsed = urlparse(full_url)
            if parsed.netloc not in (BASE_DOMAIN, "www." + BASE_DOMAIN):
                continue
            path = parsed.path
            if not path.startswith(cat_path):
                continue
            if _is_car_filter_url(path):
                continue
            if is_forbidden_url(full_url):
                continue
            path_parts = [p for p in path.strip("/").split("/") if p]
            # Skip trademark/brand filter pages — they are listings, not products
            if any(p.startswith("trademark=") or p.startswith("brand=")
                   for p in path_parts):
                continue
            if len(path_parts) != expected_depth:
                continue
            clean = normalize_url(full_url)
            if clean not in seen:
                seen.add(clean)
                links.append(full_url)

    # Numeric ID fallback (original approach for other site structures)
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
            if _is_car_filter_url(path):
                continue
            path_parts = [p for p in path.strip("/").split("/") if p]
            if not any(p.isdigit() and len(p) > 3 for p in path_parts):
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
            # directory or unknown: find subcategories WITHIN seed scope (strict)
            subcat_links = extract_subcategory_links(soup, self.base_url, seed_path, strict=True)
            logger.info(f"Found {len(subcat_links)} nested subcats within {seed_path}")

            if not subcat_links:
                # Site uses flat URL structure: subcategories are siblings, not nested.
                # Fall back to any /ru/category/ link found on this page.
                subcat_links = extract_subcategory_links(soup, self.base_url, seed_path, strict=False)
                logger.info(f"Flat fallback: {len(subcat_links)} subcats at {url}")

            if subcat_links:
                for sub_url in subcat_links:
                    yield from self._crawl_url(sub_url, section, subsection,
                                                source_url=source_url,
                                                seed_path=seed_path,
                                                depth=depth + 1)
            else:
                # No subcats found — treat as listing
                product_links = extract_product_links(soup, self.base_url, url)
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

        # Save original category path for product link depth detection
        cat_url = listing_url

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

            product_links = extract_product_links(soup, self.base_url, cat_url)
            logger.info(f"Page {page_idx}: {len(product_links)} products at {page_url}")

            # Debug: dump HTML and all /ru/ links when no products found on page 1
            if page_idx == 1 and not product_links:
                import os as _os
                debug_path = _os.path.join(_os.getcwd(), "debug_page.html")
                try:
                    with open(debug_path, "w", encoding="utf-8") as _f:
                        _f.write(str(soup))
                    logger.warning(f"[DEBUG] 0 products — HTML saved to {debug_path}")
                except Exception as _e:
                    logger.warning(f"[DEBUG] Could not save HTML: {_e}")
                ru_links = [a["href"] for a in soup.find_all("a", href=True)
                            if "/ru/" in a.get("href", "")][:30]
                logger.warning(f"[DEBUG] Sample /ru/ links on page: {ru_links}")

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

    def crawl_category_direct(self, url: str, section: str,
                               subsection: str) -> Generator[dict, None, None]:
        """Crawl a single known category URL directly (from a categories file).
        Treats the URL as a listing; if the page turns out to be a directory
        (subcategory tiles), descends one level only.
        """
        norm = normalize_url(url)
        if norm in self.visited_dirs:
            return
        self.visited_dirs.add(norm)

        if is_forbidden_url(url):
            return

        logger.info(f"Direct category: {url}")
        html = self.renderer.fetch_html(url)
        if not html:
            logger.warning(f"Empty response: {url}")
            return

        soup = BeautifulSoup(html, "lxml")
        page_type = detect_page_type(soup)

        if page_type == "listing":
            yield from self._crawl_listing(url, soup, section, subsection, url)
        else:
            # Try as listing first (products may still be present)
            product_links = extract_product_links(soup, self.base_url, url)
            if product_links:
                yield from self._crawl_listing(url, soup, section, subsection, url)
            else:
                # Directory: descend one level using flat (tile-image) subcats
                subcat_links = extract_subcategory_links(
                    soup, self.base_url, urlparse(url).path, strict=False)
                logger.info(f"  -> directory, found {len(subcat_links)} subcats")
                for sub_url in subcat_links:
                    sub_norm = normalize_url(sub_url)
                    if sub_norm in self.visited_dirs:
                        continue
                    self.visited_dirs.add(sub_norm)
                    sub_html = self.renderer.fetch_html(sub_url)
                    if not sub_html:
                        continue
                    sub_soup = BeautifulSoup(sub_html, "lxml")
                    yield from self._crawl_listing(
                        sub_url, sub_soup, section, subsection, sub_url)
