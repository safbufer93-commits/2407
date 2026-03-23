"""
Adapter for market.tmtr.ru

SPA (Vue/React). Likely requires authentication.
Without credentials: returns LOGIN_REQUIRED.
With credentials (MARKET_TMTR_LOGIN / MARKET_TMTR_PASSWORD env vars): attempts login, then searches.
"""
from __future__ import annotations
import asyncio
import re
from typing import List

from .base import BaseSiteAdapter, OfferParsed, ScrapeResult, TaskStatus, MatchType
from ..normalizer import normalize_brand, normalize_price, normalize_availability


class MarketTmtrAdapter(BaseSiteAdapter):
    site_id = "market_tmtr"
    base_url = "https://market.tmtr.ru/"

    async def scrape(self, page, sku_norm, sku_raw, config, artifacts_dir, log) -> ScrapeResult:
        log.info(f"Market.tmtr: searching for {sku_norm}")

        login = getattr(config, "MARKET_TMTR_LOGIN", None)
        password = getattr(config, "MARKET_TMTR_PASSWORD", None)

        try:
            await page.goto("https://market.tmtr.ru/", timeout=config.NAVIGATION_TIMEOUT_MS,
                            wait_until="networkidle")
        except Exception as exc:
            scr, html = await self._save_artifacts(page, artifacts_dir, f"tmtr_timeout_{sku_norm}")
            return ScrapeResult(status=TaskStatus.TIMEOUT, error_message=str(exc),
                                screenshot_path=scr, html_path=html)

        # Wait for SPA hydration
        await asyncio.sleep(3)
        content = await page.content()

        # Check for login requirement
        if self._needs_login(content):
            if not (login and password):
                scr, html = await self._save_artifacts(page, artifacts_dir, f"tmtr_loginreq_{sku_norm}")
                log.warning("Market.tmtr: login required but no credentials provided")
                return ScrapeResult(
                    status=TaskStatus.LOGIN_REQUIRED,
                    offers=[OfferParsed(
                        site=self.base_url, header_brand="", name="",
                        sku=sku_norm, price=None, availability="REQUIRES_LOGIN",
                        source_url=page.url, sku_queried=sku_raw,
                    )],
                    screenshot_path=scr,
                    html_path=html,
                )
            # Attempt login
            login_ok = await self._do_login(page, login, password, config, log)
            if not login_ok:
                scr, html = await self._save_artifacts(page, artifacts_dir, f"tmtr_loginfail_{sku_norm}")
                return ScrapeResult(status=TaskStatus.LOGIN_REQUIRED, screenshot_path=scr, html_path=html)

        # Search for SKU
        offers = await self._search_and_parse(page, sku_norm, sku_raw, config, log)

        if not offers:
            scr, html = await self._save_artifacts(page, artifacts_dir, f"tmtr_notfound_{sku_norm}")
            return ScrapeResult(status=TaskStatus.NOT_FOUND, screenshot_path=scr, html_path=html)

        return ScrapeResult(status=TaskStatus.COMPLETED, offers=offers)

    def _needs_login(self, content: str) -> bool:
        cl = content.lower()
        return any(kw in cl for kw in ("авторизация", "войти", "login", "sign in", "вход")) and \
               "loading" not in cl  # still loading is not a login wall

    async def _do_login(self, page, login, password, config, log) -> bool:
        try:
            login_input = page.locator("input[type='email'], input[name*='login'], input[name*='email']").first
            await login_input.fill(login)
            pw_input = page.locator("input[type='password']").first
            await pw_input.fill(password)
            submit = page.get_by_role("button", name=re.compile(r"войти|вход|login|sign in", re.IGNORECASE))
            await submit.first.click()
            await page.wait_for_load_state("networkidle")
            await asyncio.sleep(2)
            content = await page.content()
            return not self._needs_login(content)
        except Exception as exc:
            log.error(f"Market.tmtr login failed: {exc}")
            return False

    async def _search_and_parse(self, page, sku_norm, sku_raw, config, log) -> List[OfferParsed]:
        offers: List[OfferParsed] = []
        try:
            search_input = page.locator(
                "input[type='search'], input[placeholder*='артикул'], input[placeholder*='номер'], input[placeholder*='поиск']"
            ).first
            await search_input.fill(sku_norm)
            await search_input.press("Enter")
            await page.wait_for_load_state("networkidle")
            await asyncio.sleep(2)
        except Exception as exc:
            log.error(f"Market.tmtr search failed: {exc}")
            return offers

        card_url = page.url
        max_pages = getattr(config, "MAX_PAGES", 5)

        for page_no in range(1, max_pages + 1):
            try:
                rows = await page.locator("table tr, .product-row, .offer-row, .catalog-item").all()
                for row_no, row in enumerate(rows):
                    text = await row.inner_text()
                    price = normalize_price(text)
                    if price is None:
                        continue
                    brand_m = re.search(r"([А-ЯA-Z][А-ЯA-Za-z\- ]{2,})", text)
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
                await page.wait_for_load_state("networkidle")
                await asyncio.sleep(1)
            except Exception:
                break

        return offers
