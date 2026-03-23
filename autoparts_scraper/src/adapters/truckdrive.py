"""
Adapter for truckdrive.ru

Searches via "Точный по номеру" UI mode.
COMPLIANCE: By default, never enters phone number. Returns REQUIRES_PHONE status
when the site asks for it. Phone mode only if explicitly enabled in config.
"""
from __future__ import annotations
import re
from typing import List

from .base import BaseSiteAdapter, OfferParsed, ScrapeResult, TaskStatus, MatchType
from ..normalizer import normalize_brand, normalize_price, normalize_availability


class TruckdriveAdapter(BaseSiteAdapter):
    site_id = "truckdrive"
    base_url = "https://truckdrive.ru/"

    async def scrape(self, page, sku_norm, sku_raw, config, artifacts_dir, log) -> ScrapeResult:
        log.info(f"Truckdrive: searching for {sku_norm}")

        try:
            await page.goto("https://truckdrive.ru/", timeout=config.NAVIGATION_TIMEOUT_MS,
                            wait_until=config.PAGE_LOAD_STATE)
        except Exception as exc:
            scr, html = await self._save_artifacts(page, artifacts_dir, f"truckdrive_timeout_{sku_norm}")
            return ScrapeResult(status=TaskStatus.TIMEOUT, error_message=str(exc),
                                screenshot_path=scr, html_path=html)

        blocked = await self._detect_blocked(page)
        if blocked:
            scr, html = await self._save_artifacts(page, artifacts_dir, f"truckdrive_blocked_{sku_norm}")
            return ScrapeResult(status=blocked, screenshot_path=scr, html_path=html)

        # Try to find "Точный по номеру" search mode button
        try:
            exact_btn = page.get_by_text(re.compile(r"точный по номеру", re.IGNORECASE))
            if await exact_btn.count():
                await exact_btn.first.click()
                await page.wait_for_load_state(config.PAGE_LOAD_STATE)
        except Exception:
            pass  # Not always present; proceed with default search

        # Enter SKU in search box
        try:
            search_input = page.locator(
                "input[type='search'], input[placeholder*='номер'], input[placeholder*='артикул'], input[name*='query'], input[name*='search']"
            ).first
            await search_input.fill(sku_norm)
            await search_input.press("Enter")
            await page.wait_for_load_state(config.PAGE_LOAD_STATE)
        except Exception as exc:
            scr, html = await self._save_artifacts(page, artifacts_dir, f"truckdrive_searchfail_{sku_norm}")
            return ScrapeResult(status=TaskStatus.TIMEOUT, error_message=f"Search failed: {exc}",
                                screenshot_path=scr, html_path=html)

        # Compliance check: detect phone gate
        content = await page.content()
        if self._requires_phone(content):
            scr, html = await self._save_artifacts(page, artifacts_dir, f"truckdrive_phone_{sku_norm}")
            # Check if phone mode is enabled in config
            if getattr(config, "TRUCKDRIVE_PHONE_MODE", False) and getattr(config, "TRUCKDRIVE_PHONE", None):
                log.warning("Truckdrive: phone mode enabled — entering corporate phone")
                offers = await self._enter_phone_and_parse(page, sku_norm, sku_raw, config, log)
                if offers:
                    return ScrapeResult(status=TaskStatus.COMPLETED, offers=offers)
            return ScrapeResult(
                status=TaskStatus.REQUIRES_PHONE,
                offers=[OfferParsed(
                    site=self.base_url, header_brand="", name="",
                    sku=sku_norm, price=None, availability="REQUIRES_PHONE",
                    source_url=page.url, sku_queried=sku_raw,
                )],
                screenshot_path=scr,
                html_path=html,
            )

        offers = await self._parse_results(page, sku_norm, sku_raw, config, log)

        if not offers:
            scr, html = await self._save_artifacts(page, artifacts_dir, f"truckdrive_notfound_{sku_norm}")
            return ScrapeResult(status=TaskStatus.NOT_FOUND, screenshot_path=scr, html_path=html)

        return ScrapeResult(status=TaskStatus.COMPLETED, offers=offers)

    def _requires_phone(self, content: str) -> bool:
        return any(kw in content.lower() for kw in (
            "напишите ваш номер телефона", "чтобы узнать цену", "введите номер телефона",
            "укажите ваш номер", "phone", "телефон",
        ))

    async def _enter_phone_and_parse(self, page, sku_norm, sku_raw, config, log) -> List[OfferParsed]:
        """Enter corporate phone and parse results — only called with explicit config permission."""
        phone = config.TRUCKDRIVE_PHONE
        try:
            phone_input = page.locator("input[type='tel'], input[placeholder*='телефон'], input[name*='phone']").first
            await phone_input.fill(phone)
            # Find and click consent/submit button
            submit = page.get_by_role("button", name=re.compile(r"узнать|получить|отправить|submit", re.IGNORECASE))
            if await submit.count():
                await submit.first.click()
                await page.wait_for_load_state(config.PAGE_LOAD_STATE)
            return await self._parse_results(page, sku_norm, sku_raw, config, log)
        except Exception as exc:
            log.error(f"Truckdrive phone mode failed: {exc}")
            return []

    async def _parse_results(self, page, sku_norm, sku_raw, config, log) -> List[OfferParsed]:
        offers: List[OfferParsed] = []
        card_url = page.url
        max_pages = getattr(config, "MAX_PAGES", 5)

        for page_no in range(1, max_pages + 1):
            try:
                rows = await page.locator("table tr, .product-row, .catalog-item, .search-item").all()
                for row_no, row in enumerate(rows):
                    text = await row.inner_text()
                    price = normalize_price(text)
                    if price is None:
                        continue
                    brand_m = re.search(r"([А-ЯA-Z][А-ЯA-Za-z\- ]{2,})", text)
                    brand = normalize_brand(brand_m.group(1) if brand_m else "")
                    avail = normalize_availability(text)
                    name_m = re.search(r"([А-ЯA-Za-z][А-ЯA-Za-zа-яё0-9 ,\-\.]+)", text)
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
            next_btn = page.locator(f"a:has-text('{page_no + 1}')").first
            try:
                if not await next_btn.is_visible(timeout=2000):
                    break
                await next_btn.click()
                await page.wait_for_load_state(config.PAGE_LOAD_STATE)
            except Exception:
                break

        return offers
