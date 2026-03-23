"""
Adapter for tatparts.ru

Two-step flow (per site documentation):
  1) Ввести артикул в поле «введите номер детали» → нажать «найти»
  2) Выбрать производителя из появившегося списка
  3) Парсить предложения в интерфейсе 1 или 2; пагинация в интерфейсе 2

Real URL pattern after search: typically /?query=<SKU> or /search/?...
Manufacturer selection: links or buttons with brand name text.
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

        # Step 1: find and fill the search input
        # Try multiple selector strategies for the search field
        search_filled = False
        for selector in [
            "input[placeholder*='номер детали']",
            "input[placeholder*='номер']",
            "input[placeholder*='артикул']",
            "input[name='query']",
            "input[name='search']",
            "input[name='q']",
            "input[type='text']:visible",
        ]:
            try:
                inp = page.locator(selector).first
                if await inp.is_visible(timeout=3000):
                    await inp.fill(sku_norm)
                    search_filled = True
                    break
            except Exception:
                continue

        if not search_filled:
            scr, html = await self._save_artifacts(page, artifacts_dir, f"tatparts_noinput_{sku_norm}")
            return ScrapeResult(status=TaskStatus.PARSE_ERROR,
                                error_message="Search input not found",
                                screenshot_path=scr, html_path=html)

        # Submit search
        submitted = False
        for btn_selector in [
            "button[type='submit']",
            "input[type='submit']",
            "button:has-text('найти')",
            "button:has-text('поиск')",
            "button:has-text('Найти')",
        ]:
            try:
                btn = page.locator(btn_selector).first
                if await btn.is_visible(timeout=2000):
                    await btn.click()
                    submitted = True
                    break
            except Exception:
                continue

        if not submitted:
            # Fallback: press Enter in the search field
            try:
                await page.keyboard.press("Enter")
                submitted = True
            except Exception:
                pass

        if not submitted:
            scr, html = await self._save_artifacts(page, artifacts_dir, f"tatparts_nosubmit_{sku_norm}")
            return ScrapeResult(status=TaskStatus.PARSE_ERROR, error_message="Could not submit search",
                                screenshot_path=scr, html_path=html)

        try:
            await page.wait_for_load_state(config.PAGE_LOAD_STATE)
            # Extra wait for JS-rendered manufacturer list
            await asyncio.sleep(1.5)
        except Exception:
            pass

        # Step 2: collect manufacturer links
        # tatparts shows manufacturer buttons/links with brand names after search
        manufacturer_items = await self._collect_manufacturers(page, sku_norm)

        offers: List[OfferParsed] = []
        max_mfr = getattr(config, "MAX_MANUFACTURERS", 3)
        results_url = page.url  # base URL to return to between manufacturers

        if not manufacturer_items:
            # No manufacturer selection page — already on results
            result_offers = await self._parse_results_page(page, sku_norm, sku_raw, config, log)
            if result_offers:
                offers.extend(result_offers)
            else:
                scr, html = await self._save_artifacts(page, artifacts_dir, f"tatparts_notfound_{sku_norm}")
                return ScrapeResult(status=TaskStatus.NOT_FOUND, screenshot_path=scr, html_path=html)
        else:
            for item in manufacturer_items[:max_mfr]:
                try:
                    href, label = item
                    if href:
                        await page.goto(href, timeout=config.NAVIGATION_TIMEOUT_MS,
                                        wait_until=config.PAGE_LOAD_STATE)
                    else:
                        # It's a button/link by text — click it
                        btn = page.get_by_text(label, exact=True).first
                        await btn.click()
                        await page.wait_for_load_state(config.PAGE_LOAD_STATE)

                    await asyncio.sleep(0.8)
                    page_offers = await self._parse_results_page(page, sku_norm, sku_raw, config, log)
                    offers.extend(page_offers)

                    # Go back to manufacturer selection
                    if len(manufacturer_items) > 1:
                        await page.goto(results_url, timeout=config.NAVIGATION_TIMEOUT_MS,
                                        wait_until=config.PAGE_LOAD_STATE)
                        await asyncio.sleep(0.5)
                except Exception as exc:
                    log.error(f"Tatparts: error for manufacturer {item}: {exc}")

        if not offers:
            return ScrapeResult(status=TaskStatus.NOT_FOUND)

        return ScrapeResult(status=TaskStatus.COMPLETED, offers=offers)

    async def _collect_manufacturers(self, page) -> List[tuple]:
        """
        Returns list of (href_or_None, label) tuples for each manufacturer.
        tatparts shows brand selection as links or buttons after entering an article.
        """
        items: List[tuple] = []
        try:
            # Strategy 1: links that look like brand navigation
            anchors = await page.locator("a").all()
            for a in anchors:
                href = await a.get_attribute("href")
                text = (await a.inner_text()).strip()
                if not text or len(text) > 60:
                    continue
                href_l = (href or "").lower()
                # tatparts typically uses /brand/<name>/ or ?brand= or /search?brand=
                if href and any(kw in href_l for kw in ("brand=", "/brand/", "maker=", "producer=")):
                    full = href if href.startswith("http") else f"https://www.tatparts.ru{href}"
                    items.append((full, text))
        except Exception:
            pass

        if items:
            return items

        # Strategy 2: list items / buttons in a "choose manufacturer" block
        try:
            # Look for a section header and adjacent list
            brand_section = page.locator(
                "text='производитель', text='Производитель', text='выберите', text='Выберите'"
            ).first
            if await brand_section.count():
                container = brand_section.locator("..").locator("..")
                links = await container.locator("a, button").all()
                for el in links:
                    text = (await el.inner_text()).strip()
                    if not text or len(text) > 40:
                        continue
                    href = await el.get_attribute("href")
                    full = (href if href and href.startswith("http")
                            else f"https://www.tatparts.ru{href}" if href else None)
                    items.append((full, text))
        except Exception:
            pass

        return items

    async def _parse_results_page(
        self, page, sku_norm, sku_raw, config, log
    ) -> List[OfferParsed]:
        """Parse interface 1 or 2 results with pagination."""
        offers: List[OfferParsed] = []
        card_url = page.url

        # Detect interface: interface 2 has explicit group headers
        try:
            content = await page.content()
        except Exception:
            content = ""
        is_iface2 = any(kw in content for kw in (
            "Запрошенный артикул", "Оригинальные замены", "Аналоги"
        ))

        max_pages = getattr(config, "MAX_PAGES", 5)

        for page_no in range(1, (max_pages or 999) + 1):
            if is_iface2:
                page_offers = await self._extract_iface2_rows(page, sku_norm, sku_raw, card_url, page_no)
            else:
                page_offers = await self._extract_generic_rows(page, sku_norm, sku_raw, card_url, page_no)
            offers.extend(page_offers)

            # Pagination
            next_btn = page.get_by_role("link", name=str(page_no + 1)).first
            try:
                visible = await next_btn.is_visible(timeout=2000)
            except Exception:
                visible = False
            if not visible:
                break
            try:
                await next_btn.click()
                await page.wait_for_load_state(config.PAGE_LOAD_STATE)
            except Exception:
                break

        return offers

    async def _extract_generic_rows(self, page, sku_norm, sku_raw, card_url, page_no) -> List[OfferParsed]:
        offers: List[OfferParsed] = []
        try:
            rows = await page.locator("table tbody tr, .catalog-row, .product-row").all()
            for row_no, row in enumerate(rows):
                text = await row.inner_text()
                if "₽" not in text:
                    continue
                price = normalize_price(text)
                if price is None:
                    continue
                brand_m = re.search(r"([A-ZА-Я][A-ZА-Яa-zа-я\-]{2,})", text)
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
        """Interface 2: sections 'Запрошенный артикул / Оригинальные замены / Аналоги'."""
        offers: List[OfferParsed] = []
        match_type = MatchType.EXACT
        try:
            # Walk all rows; detect section changes by header text
            rows = await page.locator("table tbody tr, .offer-item, .search-result-row, tr").all()
            for row_no, row in enumerate(rows):
                try:
                    text = (await row.inner_text()).strip()
                    if not text:
                        continue
                    # Section header detection
                    if "Аналог" in text and "₽" not in text:
                        match_type = MatchType.ANALOG
                        continue
                    if "Оригинальн" in text and "₽" not in text:
                        match_type = MatchType.REPLACEMENT
                        continue
                    if "Запрошенный" in text and "₽" not in text:
                        match_type = MatchType.EXACT
                        continue

                    if "₽" not in text:
                        continue
                    price = normalize_price(text)
                    if price is None:
                        continue
                    brand_m = re.search(r"([A-ZА-Я][A-ZА-Яa-zа-я\-]{2,})", text)
                    brand = normalize_brand(brand_m.group(1) if brand_m else "")
                    avail = normalize_availability(text)
                    offers.append(OfferParsed(
                        site=self.base_url, header_brand=brand, name="",
                        sku=sku_norm, price=price, availability=avail,
                        source_url=card_url, match_type=match_type,
                        page_no=page_no, row_no=row_no, sku_queried=sku_raw,
                    ))
                except Exception:
                    continue
        except Exception:
            pass
        return offers
