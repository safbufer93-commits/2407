"""
Adapter for market.tmtr.ru

SPA (Vue/React). Requires authentication.
Without credentials: returns LOGIN_REQUIRED immediately.
With credentials (env vars MARKET_TMTR_LOGIN / MARKET_TMTR_PASSWORD):
  attempts login, waits for SPA hydration, then searches by SKU.
"""
from __future__ import annotations
import asyncio
import re
from typing import List

from .base import BaseSiteAdapter, OfferParsed, ScrapeResult, TaskStatus, MatchType
from ..normalizer import normalize_brand, normalize_price, normalize_availability

# SPA needs longer initial load
SPA_SETTLE_SEC = 4


class MarketTmtrAdapter(BaseSiteAdapter):
    site_id = "market_tmtr"
    base_url = "https://market.tmtr.ru/"

    async def scrape(self, page, sku_norm, sku_raw, config, artifacts_dir, log) -> ScrapeResult:
        log.info(f"Market.tmtr: searching for {sku_norm}")

        login = getattr(config, "MARKET_TMTR_LOGIN", None)
        password = getattr(config, "MARKET_TMTR_PASSWORD", None)

        try:
            await page.goto("https://market.tmtr.ru/",
                            timeout=config.NAVIGATION_TIMEOUT_MS,
                            wait_until=config.PAGE_LOAD_STATE)
        except Exception as exc:
            scr, html = await self._save_artifacts(page, artifacts_dir, f"tmtr_timeout_{sku_norm}")
            return ScrapeResult(status=TaskStatus.TIMEOUT, error_message=str(exc),
                                screenshot_path=scr, html_path=html)

        # Wait for SPA to hydrate
        await asyncio.sleep(SPA_SETTLE_SEC)

        if self._is_login_wall(await page.content()):
            if not (login and password):
                scr, html = await self._save_artifacts(page, artifacts_dir, f"tmtr_loginreq_{sku_norm}")
                log.warning("Market.tmtr: login required — set MARKET_TMTR_LOGIN / MARKET_TMTR_PASSWORD env vars")
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

            login_ok = await self._do_login(page, login, password, config, log)
            if not login_ok:
                scr, html = await self._save_artifacts(page, artifacts_dir, f"tmtr_loginfail_{sku_norm}")
                return ScrapeResult(status=TaskStatus.LOGIN_REQUIRED,
                                    error_message="Login failed",
                                    screenshot_path=scr, html_path=html)

        offers = await self._search_and_parse(page, sku_norm, sku_raw, config, log)

        if not offers:
            scr, html = await self._save_artifacts(page, artifacts_dir, f"tmtr_notfound_{sku_norm}")
            return ScrapeResult(status=TaskStatus.NOT_FOUND, screenshot_path=scr, html_path=html)

        return ScrapeResult(status=TaskStatus.COMPLETED, offers=offers)

    def _is_login_wall(self, content: str) -> bool:
        """
        True only when the page shows a login form and no content/catalog.
        Checks for login form elements, not just the word 'войти' (which appears in nav).
        """
        cl = content.lower()
        has_login_form = any(kw in cl for kw in (
            "input[type=\"password\"]", 'type="password"', "type='password'",
            "авторизация", "войти в систему", "вход в аккаунт",
        ))
        has_content = any(kw in cl for kw in ("catalog", "каталог", "артикул", "search"))
        # It's a login wall if there's a login form AND no catalog content
        return has_login_form and not has_content

    async def _do_login(self, page, login, password, config, log) -> bool:
        try:
            # Try to find login/email input
            login_input = None
            for sel in ["input[type='email']", "input[name='login']",
                        "input[name='email']", "input[name='username']"]:
                try:
                    el = page.locator(sel).first
                    if await el.is_visible(timeout=3000):
                        login_input = el
                        break
                except Exception:
                    continue

            if login_input is None:
                log.error("Market.tmtr: login input not found")
                return False

            await login_input.fill(login)

            pw_input = page.locator("input[type='password']").first
            await pw_input.fill(password)

            # Submit button
            submitted = False
            for btn_sel in [
                "button[type='submit']",
                "input[type='submit']",
                "button:has-text('Войти')",
                "button:has-text('Вход')",
            ]:
                try:
                    btn = page.locator(btn_sel).first
                    if await btn.is_visible(timeout=2000):
                        await btn.click()
                        submitted = True
                        break
                except Exception:
                    continue

            if not submitted:
                await page.keyboard.press("Enter")

            # Wait for SPA navigation after login
            await page.wait_for_load_state(config.PAGE_LOAD_STATE)
            await asyncio.sleep(SPA_SETTLE_SEC)

            return not self._is_login_wall(await page.content())
        except Exception as exc:
            log.error(f"Market.tmtr login failed: {exc}")
            return False

    async def _search_and_parse(self, page, sku_norm, sku_raw, config, log) -> List[OfferParsed]:
        offers: List[OfferParsed] = []

        # Find and fill search input
        search_filled = False
        for sel in [
            "input[type='search']",
            "input[placeholder*='артикул']",
            "input[placeholder*='номер']",
            "input[placeholder*='поиск']",
            "input[name*='search']",
            "input[name*='query']",
        ]:
            try:
                inp = page.locator(sel).first
                if await inp.is_visible(timeout=3000):
                    await inp.fill(sku_norm)
                    await inp.press("Enter")
                    search_filled = True
                    break
            except Exception:
                continue

        if not search_filled:
            log.error("Market.tmtr: search input not found")
            return offers

        # Wait for SPA to load results
        await page.wait_for_load_state(config.PAGE_LOAD_STATE)
        await asyncio.sleep(SPA_SETTLE_SEC)

        card_url = page.url
        max_pages = getattr(config, "MAX_PAGES", 5)
        page_no = 1

        while True:
            try:
                rows = await page.locator("table tbody tr, .product-row, .offer-row, .catalog-item").all()
                for row_no, row in enumerate(rows):
                    try:
                        text = await row.inner_text()
                        if "₽" not in text:
                            continue
                        price = normalize_price(text)
                        if price is None:
                            continue
                        brand_m = re.search(r"([A-ZА-Я][A-ZА-Яa-zа-я\- ]{2,})", text)
                        brand = normalize_brand(brand_m.group(1) if brand_m else "")
                        avail = normalize_availability(text)
                        offers.append(OfferParsed(
                            site=self.base_url, header_brand=brand, name="",
                            sku=sku_norm, price=price, availability=avail,
                            source_url=card_url, match_type=MatchType.UNKNOWN,
                            page_no=page_no, row_no=row_no, sku_queried=sku_raw,
                        ))
                    except Exception:
                        continue
            except Exception:
                pass

            if max_pages and page_no >= max_pages:
                break

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
                await asyncio.sleep(1)
                page_no += 1
            except Exception:
                break

        return offers
