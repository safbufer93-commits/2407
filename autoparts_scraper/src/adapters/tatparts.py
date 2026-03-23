"""
Adapter for tatparts.ru

Two-step flow:
  1) Enter SKU in "введите номер детали" field → click "найти"
  2) Select manufacturer (or auto-selected)
  3) Parse offers in interface 1 or 2 with pagination
"""
from __future__ import annotations
import asyncio
import re
from typing import List

from .base import BaseSiteAdapter, OfferParsed, ScrapeResult, TaskStatus, MatchType
from ..normalizer import normalize_brand, normalize_price, normalize_availability


class TatpartsAdapter(BaseSiteAdapter):
    site_id = "tatparts"
    base_url = "https://www.tatparts.ru/"

    async def scrape(self, page, sku_norm, sku_raw, config, artifacts_dir, log) -> ScrapeResult:
        log.info(f"Tatparts: searching for {sku_norm}")

        try:
            await page.goto("https://www.tatparts.ru/", timeout=config.NAVIGATION_TIMEOUT_MS,
                            wait_until=config.PAGE_LOAD_STATE)
        except Exception as exc:
            scr, html = await self._save_artifacts(page, artifacts_dir, f"tatparts_timeout_{sku_norm}")
            return ScrapeResult(status=TaskStatus.TIMEOUT, error_message=str(exc),
                                screenshot_path=scr, html_path=html)

        blocked = await self._detect_blocked(page)
        if blocked:
            scr, html = await self._save_artifacts(page, artifacts_dir, f"tatparts_blocked_{sku_norm}")
            return ScrapeResult(status=blocked, screenshot_path=scr, html_path=html)

        # Step 1: find search input by placeholder
        try:
            search_input = page.get_by_placeholder(re.compile(r"введите номер", re.IGNORECASE))
            if not await search_input.count():
                search_input = page.locator("input[type='text']").first
            await search_input.fill(sku_norm)
            # Find and click the search button
            search_btn = page.get_by_role("button", name=re.compile(r"найти|поиск|search", re.IGNORECASE))
            if not await search_btn.count():
                await search_input.press("Enter")
            else:
                await search_btn.first.click()
            await page.wait_for_load_state(config.PAGE_LOAD_STATE)
        except Exception as exc:
            scr, html = await self._save_artifacts(page, artifacts_dir, f"tatparts_searchfail_{sku_norm}")
            return ScrapeResult(status=TaskStatus.TIMEOUT, error_message=f"Search step failed: {exc}",
                                screenshot_path=scr, html_path=html)

        # Step 2: choose manufacturer(s)
        manufacturer_links = await self._collect_manufacturer_links(page)
        offers: List[OfferParsed] = []
        max_mfr = getattr(config, "MAX_MANUFACTURERS", 3)

        if not manufacturer_links:
            # Maybe we're already on results page
            result_offers = await self._parse_results_page(page, sku_norm, sku_raw, config, log)
            if result_offers:
                offers.extend(result_offers)
            else:
                scr, html = await self._save_artifacts(page, artifacts_dir, f"tatparts_notfound_{sku_norm}")
                return ScrapeResult(status=TaskStatus.NOT_FOUND, screenshot_path=scr, html_path=html)
        else:
            for link in manufacturer_links[:max_mfr]:
                try:
                    await page.goto(link, timeout=config.NAVIGATION_TIMEOUT_MS,
                                    wait_until=config.PAGE_LOAD_STATE)
                    page_offers = await self._parse_results_page(page, sku_norm, sku_raw, config, log)
                    offers.extend(page_offers)
                    await page.go_back()
                    await asyncio.sleep(0.5)
                except Exception as exc:
                    log.error(f"Tatparts: error for manufacturer {link}: {exc}")

        if not offers:
            return ScrapeResult(status=TaskStatus.NOT_FOUND)

        return ScrapeResult(status=TaskStatus.COMPLETED, offers=offers)

    async def _collect_manufacturer_links(self, page) -> List[str]:
        links = []
        try:
            # Manufacturer selection: look for links with brand/producer names
            anchors = await page.locator("a[href*='brand'], a[href*='producer'], a[href*='maker'], a[href*='vendor']").all()
            for a in anchors[:10]:
                href = await a.get_attribute("href")
                if href:
                    full = href if href.startswith("http") else f"https://www.tatparts.ru{href}"
                    links.append(full)
        except Exception:
            pass
        return links

    async def _parse_results_page(
        self, page, sku_norm, sku_raw, config, log
    ) -> List[OfferParsed]:
        """Parse interface 1 or 2 results with pagination."""
        offers: List[OfferParsed] = []
        card_url = page.url

        # Detect interface type
        content = await page.content()
        is_iface2 = any(kw in content for kw in ("Запрошенный артикул", "Оригинальные замены", "Аналоги"))

        max_pages = getattr(config, "MAX_PAGES", 5)
        page_no = 1

        while True:
            if is_iface2:
                page_offers = await self._extract_iface2_rows(page, sku_norm, sku_raw, card_url, page_no)
            else:
                page_offers = await self._extract_iface1_rows(page, sku_norm, sku_raw, card_url, page_no)
            offers.extend(page_offers)

            if max_pages and page_no >= max_pages:
                break

            # Pagination
            next_btn = page.locator(f"a:has-text('{page_no + 1}')").first
            try:
                visible = await next_btn.is_visible(timeout=2000)
            except Exception:
                visible = False
            if not visible:
                break
            try:
                await next_btn.click()
                await page.wait_for_load_state(config.PAGE_LOAD_STATE)
                page_no += 1
            except Exception:
                break

        return offers

    async def _extract_iface1_rows(self, page, sku_norm, sku_raw, card_url, page_no) -> List[OfferParsed]:
        offers: List[OfferParsed] = []
        try:
            rows = await page.locator("table tr, .catalog-row, .product-row").all()
            for row_no, row in enumerate(rows):
                text = await row.inner_text()
                price = normalize_price(text)
                if price is None:
                    continue
                brand_m = re.search(r"([А-ЯA-Z][А-ЯA-Za-z\- ]+)", text)
                brand = normalize_brand(brand_m.group(1) if brand_m else "")
                avail = normalize_availability(text)
                offers.append(OfferParsed(
                    site=self.base_url, header_brand=brand, name="",
                    sku=sku_norm, price=price, availability=avail,
                    source_url=card_url, match_type=MatchType.EXACT,
                    page_no=page_no, row_no=row_no, sku_queried=sku_raw,
                ))
        except Exception:
            pass
        return offers

    async def _extract_iface2_rows(self, page, sku_norm, sku_raw, card_url, page_no) -> List[OfferParsed]:
        """Interface 2: groups 'Запрошенный артикул / Оригинальные замены / Аналоги'."""
        offers: List[OfferParsed] = []
        match_type = MatchType.EXACT
        try:
            # Walk section headers + row blocks
            sections = await page.locator("h2, h3, .section-header, .group-title").all()
            section_texts = [(await s.inner_text()).strip() for s in sections]
        except Exception:
            section_texts = []

        try:
            rows = await page.locator("table tr, .offer-item, .search-result-row").all()
            for row_no, row in enumerate(rows):
                text = await row.inner_text()
                if any(h in text for h in ("Запрошенный", "Оригинальные", "Аналоги")):
                    if "Аналоги" in text:
                        match_type = MatchType.ANALOG
                    elif "Оригинальные" in text:
                        match_type = MatchType.REPLACEMENT
                    else:
                        match_type = MatchType.EXACT
                    continue
                price = normalize_price(text)
                if price is None:
                    continue
                brand_m = re.search(r"([А-ЯA-Z][А-ЯA-Za-z\- ]+)", text)
                brand = normalize_brand(brand_m.group(1) if brand_m else "")
                avail = normalize_availability(text)
                offers.append(OfferParsed(
                    site=self.base_url, header_brand=brand, name="",
                    sku=sku_norm, price=price, availability=avail,
                    source_url=card_url, match_type=match_type,
                    page_no=page_no, row_no=row_no, sku_queried=sku_raw,
                ))
        except Exception:
            pass
        return offers
