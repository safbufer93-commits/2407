"""
Adapter for armtek.ru

Flow: search via UI search box → product card → parse "Артикул / Бренд / Наличие / Цена" → paginate
"""
from __future__ import annotations
import asyncio
import re
from typing import List

from .base import BaseSiteAdapter, OfferParsed, ScrapeResult, TaskStatus, MatchType
from ..normalizer import normalize_brand, normalize_price, normalize_availability


class ArmtekAdapter(BaseSiteAdapter):
    site_id = "armtek"
    base_url = "https://armtek.ru/"

    async def scrape(self, page, sku_norm, sku_raw, config, artifacts_dir, log) -> ScrapeResult:
        log.info(f"Armtek: searching for {sku_norm}")

        # Navigate to main page and use the search box
        try:
            await page.goto("https://armtek.ru/", timeout=config.NAVIGATION_TIMEOUT_MS,
                            wait_until=config.PAGE_LOAD_STATE)
        except Exception as exc:
            scr, html = await self._save_artifacts(page, artifacts_dir, f"armtek_timeout_{sku_norm}")
            return ScrapeResult(status=TaskStatus.TIMEOUT, error_message=str(exc),
                                screenshot_path=scr, html_path=html)

        blocked = await self._detect_blocked(page)
        if blocked:
            scr, html = await self._save_artifacts(page, artifacts_dir, f"armtek_blocked_{sku_norm}")
            return ScrapeResult(status=blocked, screenshot_path=scr, html_path=html)

        # Find and fill search input
        try:
            search_input = page.locator("input[type='search'], input[placeholder*='Артикул'], input[placeholder*='артикул'], input[placeholder*='Пример'], input[name*='search'], input[name*='query']").first
            await search_input.wait_for(timeout=config.SELECTOR_TIMEOUT_MS)
            await search_input.fill(sku_norm)
            await search_input.press("Enter")
            await page.wait_for_load_state(config.PAGE_LOAD_STATE)
        except Exception as exc:
            scr, html = await self._save_artifacts(page, artifacts_dir, f"armtek_search_fail_{sku_norm}")
            return ScrapeResult(status=TaskStatus.TIMEOUT, error_message=f"Search input not found: {exc}",
                                screenshot_path=scr, html_path=html)

        # Check if we're on a product card directly or a search results page
        current_url = page.url
        offers: List[OfferParsed] = []

        if "/product/" in current_url or "/catalog/" in current_url:
            # Direct card
            offers = await self._parse_card(page, sku_norm, sku_raw, config, log)
        else:
            # Results page: find product links
            card_links = await self._find_product_links(page)
            for card_url in card_links[:getattr(config, "MAX_MANUFACTURERS", 3)]:
                try:
                    await page.goto(card_url, timeout=config.NAVIGATION_TIMEOUT_MS,
                                    wait_until=config.PAGE_LOAD_STATE)
                    page_offers = await self._parse_card(page, sku_norm, sku_raw, config, log)
                    offers.extend(page_offers)
                except Exception as exc:
                    log.error(f"Armtek: error on card {card_url}: {exc}")

        if not offers:
            scr, html = await self._save_artifacts(page, artifacts_dir, f"armtek_notfound_{sku_norm}")
            return ScrapeResult(status=TaskStatus.NOT_FOUND, screenshot_path=scr, html_path=html)

        return ScrapeResult(status=TaskStatus.COMPLETED, offers=offers)

    async def _find_product_links(self, page) -> List[str]:
        links = []
        try:
            anchors = await page.locator("a[href*='/product/']").all()
            for a in anchors:
                href = await a.get_attribute("href")
                if href:
                    full = href if href.startswith("http") else f"https://armtek.ru{href}"
                    if full not in links:
                        links.append(full)
        except Exception:
            pass
        return links

    async def _parse_card(self, page, sku_norm, sku_raw, config, log) -> List[OfferParsed]:
        offers: List[OfferParsed] = []
        card_url = page.url

        header_brand = normalize_brand(await self._get_field(page, "Бренд"))
        header_sku = (await self._get_field(page, "Артикул")).strip() or sku_norm
        product_name = await self._get_text_by_selector(page, "h1")

        max_pages = getattr(config, "MAX_PAGES", 5)
        page_no = 1

        while True:
            page_offers = await self._extract_offers_on_page(
                page, header_brand, product_name, header_sku, sku_raw, card_url, page_no
            )
            offers.extend(page_offers)

            if max_pages and page_no >= max_pages:
                break

            # Pagination: look for "next" page link
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

    async def _extract_offers_on_page(
        self, page, header_brand, product_name, header_sku, sku_raw, card_url, page_no
    ) -> List[OfferParsed]:
        offers: List[OfferParsed] = []
        try:
            rows = await page.locator("table tr, .offer-row, [class*='offer']").all()
        except Exception:
            return offers

        for row_no, row in enumerate(rows):
            try:
                text = await row.inner_text()
                price = normalize_price(text)
                if price is None:
                    continue
                # Availability: look for "шт" patterns
                avail = "UNKNOWN"
                m = re.search(r"(\d+)\s*шт", text, re.IGNORECASE)
                if m:
                    avail = normalize_availability(m.group(0))
                elif "Наличие" in text:
                    avail_m = re.search(r"Наличие[:\s]*(.*?)(?:\n|Отгрузка|₽|$)", text, re.DOTALL)
                    if avail_m:
                        avail = normalize_availability(avail_m.group(1).strip())

                offers.append(OfferParsed(
                    site=self.base_url,
                    header_brand=header_brand,
                    name=product_name,
                    sku=header_sku,
                    price=price,
                    availability=avail,
                    source_url=card_url,
                    match_type=MatchType.EXACT,
                    page_no=page_no,
                    row_no=row_no,
                    sku_queried=sku_raw,
                ))
            except Exception:
                continue

        return offers

    async def _get_field(self, page, label: str) -> str:
        try:
            el = page.get_by_text(label, exact=False).first
            parent = el.locator("..")
            text = await parent.inner_text()
            return text.replace(label, "").strip().strip(":").strip().split("\n")[0]
        except Exception:
            return ""

    async def _get_text_by_selector(self, page, selector: str) -> str:
        try:
            return (await page.locator(selector).first.inner_text()).strip()
        except Exception:
            return ""
