"""
Adapter for autopiter.ru

Pattern:
  GET /goods/<SKU>
    → страница с производителями: список ссылок вида /goods/<SKU>/<brand>/id<N>
    → карточка товара: заголовок, Артикул, Производитель, таблица предложений
    → пагинация: ссылки с номерами страниц
"""
from __future__ import annotations
import asyncio
import re
from typing import List, Optional

from .base import BaseSiteAdapter, OfferParsed, ScrapeResult, TaskStatus, MatchType
from ..normalizer import normalize_brand, normalize_price, normalize_availability


class AutopiterAdapter(BaseSiteAdapter):
    site_id = "autopiter"
    base_url = "https://autopiter.ru/"

    async def scrape(self, page, sku_norm, sku_raw, config, artifacts_dir, log) -> ScrapeResult:
        search_url = f"https://autopiter.ru/goods/{sku_norm}"
        log.info(f"Autopiter: navigating to {search_url}")

        try:
            await page.goto(search_url, timeout=config.NAVIGATION_TIMEOUT_MS,
                            wait_until=config.PAGE_LOAD_STATE)
        except Exception as exc:
            scr, html = await self._save_artifacts(page, artifacts_dir, f"autopiter_timeout_{sku_norm}")
            return ScrapeResult(status=TaskStatus.TIMEOUT, error_message=str(exc),
                                screenshot_path=scr, html_path=html)

        blocked = await self._detect_blocked(page)
        if blocked:
            scr, html = await self._save_artifacts(page, artifacts_dir, f"autopiter_blocked_{sku_norm}")
            return ScrapeResult(status=blocked, screenshot_path=scr, html_path=html)

        # Collect card URLs from /goods/<SKU> page
        # Real URL pattern: /goods/<sku>/<brand>/id<number>
        card_links = await self._collect_card_links(page, sku_norm, search_url)

        # If already redirected to a card page (single result)
        current_url = page.url
        if not card_links and re.search(r"/id\d+", current_url):
            card_links = [current_url]

        if not card_links:
            scr, html = await self._save_artifacts(page, artifacts_dir, f"autopiter_notfound_{sku_norm}")
            return ScrapeResult(status=TaskStatus.NOT_FOUND, screenshot_path=scr, html_path=html)

        all_offers: List[OfferParsed] = []
        max_mfr = getattr(config, "MAX_MANUFACTURERS", 3)

        for card_url in card_links[:max_mfr]:
            try:
                if page.url != card_url:
                    await page.goto(card_url, timeout=config.NAVIGATION_TIMEOUT_MS,
                                    wait_until=config.PAGE_LOAD_STATE)
                offers = await self._parse_card(page, sku_norm, sku_raw, card_url, config, log)
                all_offers.extend(offers)
                # Return to search page for next manufacturer
                if len(card_links) > 1:
                    await page.goto(search_url, timeout=config.NAVIGATION_TIMEOUT_MS,
                                    wait_until=config.PAGE_LOAD_STATE)
                    await asyncio.sleep(0.5)
            except Exception as exc:
                log.error(f"Autopiter: error parsing card {card_url}: {exc}")

        if not all_offers:
            return ScrapeResult(status=TaskStatus.NOT_FOUND)

        return ScrapeResult(status=TaskStatus.COMPLETED, offers=all_offers)

    # ------------------------------------------------------------------
    async def _collect_card_links(self, page, sku_norm: str, search_url: str) -> List[str]:
        """
        Collect product card URLs from the goods/<SKU> page.
        Card links match: /goods/<sku>/<anything>/id<number>
        """
        links: List[str] = []
        sku_lower = sku_norm.lower()
        try:
            # Look for anchor tags whose href contains the SKU (lowercase) and /id
            anchors = await page.locator("a").all()
            for a in anchors:
                href = await a.get_attribute("href")
                if not href:
                    continue
                href_l = href.lower()
                # Must contain the SKU and match /id<digits> pattern
                if sku_lower in href_l and re.search(r"/id\d+", href_l):
                    full = href if href.startswith("http") else f"https://autopiter.ru{href}"
                    # Strip query params for dedup
                    full_clean = full.split("?")[0]
                    if full_clean not in links:
                        links.append(full_clean)
        except Exception as exc:
            pass
        return links

    async def _parse_card(
        self, page, sku_norm: str, sku_raw: str, card_url: str, config, log
    ) -> List[OfferParsed]:
        """Parse offer rows on a product card page, handling pagination."""
        offers: List[OfferParsed] = []

        # Extract header brand + SKU from card header fields
        header_brand = normalize_brand(await self._get_field_value(page, "Производитель"))
        header_sku = (await self._get_field_value(page, "Артикул")) or sku_norm
        product_name = await self._get_h1(page)

        max_pages = getattr(config, "MAX_PAGES", 5)

        for page_no in range(1, (max_pages or 999) + 1):
            page_offers = await self._extract_offers_table(
                page, sku_norm, sku_raw, header_brand, product_name or header_sku,
                card_url, page_no, log
            )
            offers.extend(page_offers)

            # Find next page button
            next_page = page_no + 1
            next_btn = page.get_by_role("link", name=str(next_page)).first
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

    async def _extract_offers_table(
        self, page, sku_norm, sku_raw, header_brand, product_name,
        card_url, page_no, log
    ) -> List[OfferParsed]:
        """
        Parse the offers table on a card page.
        Autopiter table columns: Регион поставщика | Наличие | Цена | ...
        We find rows that have a numeric price (contains ₽).
        """
        offers: List[OfferParsed] = []
        try:
            # Target rows inside the "Запрошенный номер" section
            # All rows with a ₽ sign in any cell
            rows = await page.locator("tr").all()
        except Exception:
            return offers

        for row_no, row in enumerate(rows):
            try:
                cells = await row.locator("td").all()
                if len(cells) < 2:
                    continue

                cell_texts = []
                for c in cells:
                    cell_texts.append((await c.inner_text()).strip())

                full_text = " ".join(cell_texts)

                # Must contain ₽ to be an offer row (not a header)
                if "₽" not in full_text:
                    continue

                price_val: Optional[float] = None
                avail_val = "UNKNOWN"

                for text in cell_texts:
                    if "₽" in text and price_val is None:
                        p = normalize_price(text)
                        if p is not None:
                            price_val = p
                    if re.search(r"\d+\s*шт", text, re.IGNORECASE):
                        avail_val = normalize_availability(text)
                    elif any(kw in text.lower() for kw in ("в наличии", "под заказ", "нет")):
                        avail_val = normalize_availability(text)

                if price_val is None:
                    continue

                offers.append(OfferParsed(
                    site=self.base_url,
                    header_brand=header_brand,
                    name=product_name,
                    sku=header_sku if (header_sku := sku_norm) else sku_norm,
                    price=price_val,
                    availability=avail_val,
                    source_url=card_url,
                    match_type=MatchType.EXACT,
                    page_no=page_no,
                    row_no=row_no,
                    sku_queried=sku_raw,
                ))
            except Exception:
                continue

        return offers

    async def _get_field_value(self, page, label: str) -> str:
        """
        Find a definition-list style field: <dt>label</dt><dd>value</dd>
        or span/div with label text followed by value.
        """
        # Try dt/dd pattern first
        try:
            dt = page.locator(f"dt:has-text('{label}'), th:has-text('{label}')").first
            if await dt.count():
                sibling = page.locator(f"dt:has-text('{label}') + dd, th:has-text('{label}') + td").first
                if await sibling.count():
                    return (await sibling.inner_text()).strip()
        except Exception:
            pass
        # Fallback: find element with label text, get parent text, strip label
        try:
            el = page.get_by_text(label, exact=False).first
            parent = el.locator("..")
            text = await parent.inner_text()
            return text.replace(label, "").strip().strip(":").strip().split("\n")[0].strip()
        except Exception:
            return ""

    async def _get_h1(self, page) -> str:
        try:
            return (await page.locator("h1").first.inner_text()).strip()
        except Exception:
            return ""
