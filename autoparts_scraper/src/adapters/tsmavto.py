"""
Adapter for tsmavto.ru

URL pattern: /search?pcode=<SKU>
Known issue: site may time out; use increased timeouts and save trace on failure.
"""
from __future__ import annotations
import re
from typing import List

from .base import BaseSiteAdapter, OfferParsed, ScrapeResult, TaskStatus, MatchType
from ..normalizer import normalize_brand, normalize_price, normalize_availability

# tsmavto gets extra timeout headroom due to known slowness
EXTRA_TIMEOUT_MS = 90_000


class TsmavtoAdapter(BaseSiteAdapter):
    site_id = "tsmavto"
    base_url = "https://tsmavto.ru/"

    async def scrape(self, page, sku_norm, sku_raw, config, artifacts_dir, log) -> ScrapeResult:
        search_url = f"https://tsmavto.ru/search?pcode={sku_norm}"
        log.info(f"Tsmavto: navigating to {search_url}")

        nav_timeout = max(config.NAVIGATION_TIMEOUT_MS, EXTRA_TIMEOUT_MS)

        try:
            await page.goto(search_url, timeout=nav_timeout, wait_until=config.PAGE_LOAD_STATE)
        except Exception as exc:
            scr, html = await self._save_artifacts(page, artifacts_dir, f"tsmavto_timeout_{sku_norm}")
            log.warning(f"Tsmavto: timeout/error for {sku_norm}: {exc}")
            return ScrapeResult(
                status=TaskStatus.TIMEOUT,
                error_message=str(exc),
                screenshot_path=scr,
                html_path=html,
            )

        blocked = await self._detect_blocked(page)
        if blocked:
            scr, html = await self._save_artifacts(page, artifacts_dir, f"tsmavto_blocked_{sku_norm}")
            return ScrapeResult(status=blocked, screenshot_path=scr, html_path=html)

        offers = await self._parse_results(page, sku_norm, sku_raw, config, log)

        if not offers:
            scr, html = await self._save_artifacts(page, artifacts_dir, f"tsmavto_notfound_{sku_norm}")
            return ScrapeResult(status=TaskStatus.NOT_FOUND, screenshot_path=scr, html_path=html)

        return ScrapeResult(status=TaskStatus.COMPLETED, offers=offers)

    async def _parse_results(self, page, sku_norm, sku_raw, config, log) -> List[OfferParsed]:
        offers: List[OfferParsed] = []
        card_url = page.url
        max_pages = getattr(config, "MAX_PAGES", 5)

        for page_no in range(1, max_pages + 1):
            try:
                rows = await page.locator("table tr, .product-item, .result-row, .catalog-row").all()
                for row_no, row in enumerate(rows):
                    text = await row.inner_text()
                    price = normalize_price(text)
                    if price is None:
                        continue
                    brand_m = re.search(r"([А-ЯA-Z][А-ЯA-Za-z\- ]{2,})", text)
                    brand = normalize_brand(brand_m.group(1) if brand_m else "")
                    avail = normalize_availability(text)
                    name_m = re.search(r"([А-Яа-яёA-Za-z][А-Яа-яёA-Za-z0-9 ,\-\.]{4,})", text)
                    name = name_m.group(1).strip()[:120] if name_m else ""
                    offers.append(OfferParsed(
                        site=self.base_url, header_brand=brand, name=name,
                        sku=sku_norm, price=price, availability=avail,
                        source_url=card_url, match_type=MatchType.UNKNOWN,
                        page_no=page_no, row_no=row_no, sku_queried=sku_raw,
                    ))
            except Exception:
                pass

            if page_no >= max_pages:
                break
            try:
                next_btn = page.locator(f"a:has-text('{page_no + 1}')").first
                if not await next_btn.is_visible(timeout=2000):
                    break
                await next_btn.click()
                await page.wait_for_load_state(config.PAGE_LOAD_STATE)
            except Exception:
                break

        return offers
