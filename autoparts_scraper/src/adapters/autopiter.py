"""
Adapter for autopiter.ru

Pattern: /goods/<SKU>  →  list of manufacturers  →  product card  →  offers table  →  pagination
"""
from __future__ import annotations
import asyncio
import re
from typing import List

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
            return ScrapeResult(
                status=TaskStatus.TIMEOUT,
                error_message=str(exc),
                screenshot_path=scr,
                html_path=html,
            )

        blocked = await self._detect_blocked(page)
        if blocked:
            scr, html = await self._save_artifacts(page, artifacts_dir, f"autopiter_blocked_{sku_norm}")
            return ScrapeResult(status=blocked, screenshot_path=scr, html_path=html)

        # Collect product card links (manufacturer cards)
        card_links = await self._collect_card_links(page, sku_norm)
        if not card_links:
            # Try direct: maybe only one result with immediate redirect to card
            current_url = page.url
            if "/id" in current_url or "/goods/" in current_url and current_url != search_url:
                card_links = [current_url]
            else:
                scr, html = await self._save_artifacts(page, artifacts_dir, f"autopiter_notfound_{sku_norm}")
                return ScrapeResult(status=TaskStatus.NOT_FOUND, screenshot_path=scr, html_path=html)

        all_offers: List[OfferParsed] = []
        max_mfr = getattr(config, "MAX_MANUFACTURERS", 3)

        for card_url in card_links[:max_mfr]:
            try:
                if page.url != card_url:
                    await page.goto(card_url, timeout=config.NAVIGATION_TIMEOUT_MS,
                                    wait_until=config.PAGE_LOAD_STATE)
                offers = await self._parse_card(page, sku_norm, sku_raw, config, artifacts_dir, log)
                all_offers.extend(offers)
                # navigate back for next manufacturer iteration
                await page.go_back()
                await asyncio.sleep(0.5)
            except Exception as exc:
                log.error(f"Autopiter: error parsing card {card_url}: {exc}")

        if not all_offers:
            return ScrapeResult(status=TaskStatus.NOT_FOUND)

        return ScrapeResult(status=TaskStatus.COMPLETED, offers=all_offers)

    # ------------------------------------------------------------------
    async def _collect_card_links(self, page, sku_norm: str) -> List[str]:
        """Extract product card URLs from the /goods/<SKU> page."""
        links = []
        try:
            # Look for links containing /id in the href (product card links)
            anchors = await page.locator("a[href*='/id']").all()
            for a in anchors:
                href = await a.get_attribute("href")
                if href and "/id" in href:
                    full = href if href.startswith("http") else f"https://autopiter.ru{href}"
                    if full not in links:
                        links.append(full)
        except Exception:
            pass
        return links

    async def _parse_card(self, page, sku_norm: str, sku_raw: str, config, artifacts_dir, log) -> List[OfferParsed]:
        """Parse offer rows on a product card page, handling pagination."""
        offers: List[OfferParsed] = []
        card_url = page.url

        # Extract header-brand and SKU from the card
        header_brand = await self._get_text_after(page, "Производитель")
        header_sku = await self._get_text_after(page, "Артикул")
        product_name = await self._get_text_by_selector(page, "h1") or ""

        header_brand = normalize_brand(header_brand or "")

        max_pages = getattr(config, "MAX_PAGES", 5)
        page_no = 1

        while True:
            page_offers = await self._parse_offers_table(
                page, sku_norm, sku_raw, header_brand, product_name, card_url, page_no, log
            )
            offers.extend(page_offers)

            if max_pages and page_no >= max_pages:
                break

            # Try next page button
            next_btn = page.locator(f"a:has-text('{page_no + 1}')").first
            try:
                visible = await next_btn.is_visible(timeout=3000)
            except Exception:
                visible = False
            if not visible:
                break

            try:
                await next_btn.click(timeout=config.SELECTOR_TIMEOUT_MS)
                await page.wait_for_load_state(config.PAGE_LOAD_STATE)
                page_no += 1
            except Exception:
                break

        return offers

    async def _parse_offers_table(
        self, page, sku_norm, sku_raw, header_brand, product_name, card_url, page_no, log
    ) -> List[OfferParsed]:
        offers: List[OfferParsed] = []

        # Find table rows in the offers section
        # Autopiter shows "Запрошенный номер" section with a table
        try:
            rows = await page.locator("table tr").all()
        except Exception:
            return offers

        for row_no, row in enumerate(rows):
            try:
                cells = await row.locator("td").all()
                if len(cells) < 3:
                    continue
                texts = []
                for cell in cells:
                    texts.append((await cell.inner_text()).strip())

                # Heuristic: find price (contains ₽ or digits) and availability (contains шт or статус)
                price_val = None
                avail_val = "UNKNOWN"
                brand_val = header_brand
                name_val = product_name
                sku_val = sku_norm

                for text in texts:
                    if "₽" in text or re.search(r"\d{3,}", text):
                        p = normalize_price(text)
                        if p is not None and price_val is None:
                            price_val = p
                    if "шт" in text.lower() or any(
                        kw in text.lower() for kw in ("в наличии", "под заказ", "нет")
                    ):
                        avail_val = normalize_availability(text)

                # Skip header rows (no numeric price)
                if price_val is None and avail_val == "UNKNOWN":
                    continue

                offers.append(OfferParsed(
                    site=self.base_url,
                    header_brand=brand_val,
                    name=name_val,
                    sku=sku_val,
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

    async def _get_text_after(self, page, label: str) -> str:
        """Find a label text and return the following sibling/adjacent text."""
        try:
            el = page.get_by_text(label, exact=False).first
            parent = el.locator("..")
            text = await parent.inner_text()
            # Strip the label itself
            text = text.replace(label, "").strip().strip(":").strip()
            return text.split("\n")[0].strip()
        except Exception:
            return ""

    async def _get_text_by_selector(self, page, selector: str) -> str:
        try:
            return (await page.locator(selector).first.inner_text()).strip()
        except Exception:
            return ""
