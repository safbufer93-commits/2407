"""
Adapter for sale-parts.ru

Known issue: site shows Security Check (captcha) on entry.
Behaviour: detect → return BLOCKED_CAPTCHA status with artifacts.
Optional: "manual solve window" (headed mode, wait for operator).
"""
from __future__ import annotations
from typing import List

from .base import BaseSiteAdapter, OfferParsed, ScrapeResult, TaskStatus, MatchType
from ..normalizer import normalize_brand, normalize_price, normalize_availability


CAPTCHA_KEYWORDS = (
    "security check", "enter the code", "captcha", "recaptcha",
    "cloudflare", "проверка", "введите код",
)


class SalepartsAdapter(BaseSiteAdapter):
    site_id = "saleparts"
    base_url = "https://sale-parts.ru/"

    async def scrape(self, page, sku_norm, sku_raw, config, artifacts_dir, log) -> ScrapeResult:
        log.info(f"Sale-parts: searching for {sku_norm}")

        try:
            await page.goto(f"https://sale-parts.ru/search/?q={sku_norm}",
                            timeout=config.NAVIGATION_TIMEOUT_MS,
                            wait_until=config.PAGE_LOAD_STATE)
        except Exception as exc:
            scr, html = await self._save_artifacts(page, artifacts_dir, f"saleparts_timeout_{sku_norm}")
            return ScrapeResult(status=TaskStatus.TIMEOUT, error_message=str(exc),
                                screenshot_path=scr, html_path=html)

        content = await page.content()
        if self._is_captcha(content):
            scr, html = await self._save_artifacts(page, artifacts_dir, f"saleparts_captcha_{sku_norm}")
            log.warning(f"Sale-parts: captcha detected for {sku_norm}")

            # Optional: manual solve window (headed browser, operator intervenes)
            if getattr(config, "SALEPARTS_MANUAL_CAPTCHA", False):
                log.info("Sale-parts: waiting for manual captcha solve (60s)...")
                import asyncio
                await asyncio.sleep(60)
                content2 = await page.content()
                if not self._is_captcha(content2):
                    return await self._continue_after_captcha(page, sku_norm, sku_raw, config, log)

            return ScrapeResult(
                status=TaskStatus.BLOCKED_CAPTCHA,
                offers=[OfferParsed(
                    site=self.base_url, header_brand="", name="",
                    sku=sku_norm, price=None, availability="REQUIRES_CAPTCHA",
                    source_url=page.url, sku_queried=sku_raw,
                )],
                screenshot_path=scr,
                html_path=html,
            )

        return await self._continue_after_captcha(page, sku_norm, sku_raw, config, log)

    def _is_captcha(self, content: str) -> bool:
        cl = content.lower()
        return any(kw in cl for kw in CAPTCHA_KEYWORDS)

    async def _continue_after_captcha(self, page, sku_norm, sku_raw, config, log) -> ScrapeResult:
        """Parse results after captcha is solved."""
        offers: List[OfferParsed] = []
        card_url = page.url
        max_pages = getattr(config, "MAX_PAGES", 5)

        for page_no in range(1, max_pages + 1):
            try:
                import re
                rows = await page.locator("table tr, .product-item, .offer-row, .search-result").all()
                for row_no, row in enumerate(rows):
                    text = await row.inner_text()
                    price = normalize_price(text)
                    if price is None:
                        continue
                    brand_m = re.search(r"([А-ЯA-Z][А-ЯA-Za-z\-]{2,})", text)
                    brand = normalize_brand(brand_m.group(1) if brand_m else "")
                    avail = normalize_availability(text)
                    offers.append(OfferParsed(
                        site=self.base_url, header_brand=brand, name="",
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

        if not offers:
            return ScrapeResult(status=TaskStatus.NOT_FOUND)
        return ScrapeResult(status=TaskStatus.COMPLETED, offers=offers)
