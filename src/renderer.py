"""
Renderer module: uses Dolphin Anty browser via local CDP API.
Falls back to direct Playwright if Dolphin not available.
"""
import logging
import os
import random
import re
import time
from typing import Optional
from urllib.parse import urlparse
import requests

logger = logging.getLogger(__name__)

DOLPHIN_API_URL = "http://localhost:3001/v1.0"
DOLPHIN_PROFILE_ID = os.environ.get("DOLPHIN_PROFILE_ID", "759890630")
MAX_SHOW_MORE_CLICKS = int(os.environ.get("MAX_SHOW_MORE_CLICKS", "25"))

# Markers in HTML body that indicate Cloudflare interstitial/challenge page
_CF_BODY_MARKERS = [
    "challenge-error-text",
    "cf-challenge",
    "__cf_chl_",
    "cf_chl_prog",
    "cf-spinner",
    "checking your browser",
    "just a moment",
    "cierpliwości",
    "cloudflare ray id",
    "ddos protection by cloudflare",
    "access denied",
    "gateway time-out",
    "504 gateway time-out",
    "<title>504",
    "dolphin-anty-mirror",
    "nginx",
]


class RendererUnavailableError(RuntimeError):
    pass


class DolphinRenderer:
    """Renders pages using Dolphin Anty antidetect browser."""

    def __init__(
        self,
        profile_id: str = DOLPHIN_PROFILE_ID,
        delay_min: float = 2.0,
        delay_max: float = 5.0,
        max_retries: int = 3,
    ):
        self.profile_id = profile_id
        self.delay_min = delay_min
        self.delay_max = delay_max
        self.max_retries = max_retries
        self._browser = None
        self._context = None
        self._page = None
        self._pw = None
        self._ws_endpoint = None
        self.max_show_more_clicks = max(0, MAX_SHOW_MORE_CLICKS)
        self._consecutive_start_failures = 0
        self._start_failure_threshold = 3

    @staticmethod
    def _is_duplicate_running_error(message: str) -> bool:
        low = (message or "").lower()
        return (
            "already running" in low
            or "e_browser_run_duplicate" in low
            or "browser run duplicate" in low
            or "profile is running" in low
        )

    @staticmethod
    def _extract_ws_endpoint_from_payload(payload) -> Optional[str]:
        def _walk(obj):
            if isinstance(obj, dict):
                ws = obj.get("wsEndpoint")
                port = obj.get("port")
                if ws:
                    ws_str = str(ws)
                    if ws_str.startswith("ws://") or ws_str.startswith("wss://"):
                        return ws_str
                    if port:
                        return f"ws://localhost:{port}{ws_str}"
                for value in obj.values():
                    found = _walk(value)
                    if found:
                        return found
            elif isinstance(obj, list):
                for value in obj:
                    found = _walk(value)
                    if found:
                        return found
            return None

        return _walk(payload)

    def _fetch_running_ws_endpoint(self) -> Optional[str]:
        candidates = [
            f"{DOLPHIN_API_URL}/browser_profiles/{self.profile_id}",
            f"{DOLPHIN_API_URL}/browser_profiles/{self.profile_id}/automation",
        ]
        for endpoint in candidates:
            try:
                r = requests.get(endpoint, timeout=15)
                if not r.ok:
                    continue
                try:
                    data = r.json()
                except Exception:
                    continue
                ws_endpoint = self._extract_ws_endpoint_from_payload(data)
                if ws_endpoint:
                    return ws_endpoint
            except Exception:
                continue
        return None

    def _start_profile(self) -> str:
        """Start Dolphin profile, return ws endpoint."""
        url = f"{DOLPHIN_API_URL}/browser_profiles/{self.profile_id}/start?automation=1"
        last_error = None
        for attempt in range(3):
            try:
                logger.info(
                    f"Starting Dolphin profile {self.profile_id} (attempt {attempt + 1}/3)..."
                )
                r = requests.get(url, timeout=30)
                try:
                    data = r.json()
                except Exception:
                    data = {}
                if not r.ok:
                    message = (
                        data.get("error")
                        or data.get("message")
                        or data.get("msg")
                        or r.text[:300]
                    )
                    message = str(message)
                    if self._is_duplicate_running_error(message):
                        ws_endpoint = self._extract_ws_endpoint_from_payload(data)
                        if not ws_endpoint:
                            ws_endpoint = self._fetch_running_ws_endpoint()
                        if ws_endpoint:
                            logger.warning(
                                "Dolphin profile already running, using existing automation endpoint"
                            )
                            self._consecutive_start_failures = 0
                            return ws_endpoint
                        logger.warning(
                            "Dolphin reports profile already running but no ws endpoint found; "
                            "trying stop/start recovery"
                        )
                        self._stop_profile()
                        time.sleep(2)
                        continue
                    raise Exception(
                        f"Dolphin start HTTP {r.status_code}: {message}"
                    )
                if not data.get("success"):
                    message = str(
                        data.get("error") or data.get("message") or data
                    )
                    if self._is_duplicate_running_error(message):
                        ws_endpoint = self._extract_ws_endpoint_from_payload(data)
                        if not ws_endpoint:
                            ws_endpoint = self._fetch_running_ws_endpoint()
                        if ws_endpoint:
                            logger.warning(
                                "Dolphin profile already running, using existing automation endpoint"
                            )
                            self._consecutive_start_failures = 0
                            return ws_endpoint
                        logger.warning(
                            "Dolphin reports duplicate-running without ws endpoint; "
                            "trying stop/start recovery"
                        )
                        self._stop_profile()
                        time.sleep(2)
                        continue
                    raise Exception(f"Dolphin start failed: {message}")
                automation = data.get("automation") or {}
                port = automation.get("port")
                ws_path = automation.get("wsEndpoint")
                if not port or not ws_path:
                    raise Exception(f"Dolphin start response missing automation data: {data}")
                ws_endpoint = f"ws://localhost:{port}{ws_path}"
                logger.info(f"Dolphin started: {ws_endpoint}")
                self._consecutive_start_failures = 0
                return ws_endpoint
            except Exception as e:
                last_error = e
                logger.warning(f"Dolphin start failed on attempt {attempt + 1}: {e}")
                time.sleep(5)
        self._consecutive_start_failures += 1
        if self._consecutive_start_failures >= self._start_failure_threshold:
            raise RendererUnavailableError(
                f"Dolphin API unavailable after repeated start failures: {last_error}"
            )
        raise Exception(f"Dolphin start request failed: {last_error}")

    def _stop_profile(self):
        try:
            url = f"{DOLPHIN_API_URL}/browser_profiles/{self.profile_id}/stop"
            requests.get(url, timeout=10)
            logger.info("Dolphin profile stopped")
        except Exception:
            pass

    def _attach_to_ws(self, ws_endpoint: str):
        from playwright.sync_api import sync_playwright
        if self._pw is None:
            self._pw = sync_playwright().__enter__()
        self._browser = self._pw.chromium.connect_over_cdp(ws_endpoint)
        contexts = self._browser.contexts
        if contexts:
            self._context = contexts[0]
        else:
            self._context = self._browser.new_context()
        pages = self._context.pages
        self._page = pages[0] if pages else self._context.new_page()
        self._ws_endpoint = ws_endpoint
        logger.info("Connected to Dolphin browser")

    @staticmethod
    def _is_sync_api_in_async_loop_error(exc: Exception) -> bool:
        msg = str(exc).lower()
        return (
            "sync api inside the asyncio loop" in msg
            or "please use the async api instead" in msg
        )

    def _connect(self):
        """
        Connect to Dolphin.
        Reuse existing ws endpoint when possible.
        """
        if self._page is not None:
            return
        last_error = None
        if self._ws_endpoint:
            try:
                self._attach_to_ws(self._ws_endpoint)
                return
            except Exception as e:
                last_error = e
                logger.warning(f"Failed to reattach to existing ws endpoint: {e}")
                self._disconnect(keep_ws=False, keep_pw_runtime=True)
        try:
            ws_endpoint = self._start_profile()
            time.sleep(3)
            self._attach_to_ws(ws_endpoint)
            return
        except Exception as e:
            last_error = e
            msg = str(e)
            if "already running" in msg or "e_browser_run_duplicate" in msg.lower():
                candidate_ws = self._fetch_running_ws_endpoint() or self._ws_endpoint
                if candidate_ws:
                    logger.warning(
                        "Profile already running, trying to attach via discovered ws endpoint"
                    )
                    try:
                        time.sleep(2)
                        self._attach_to_ws(candidate_ws)
                        return
                    except Exception as e2:
                        last_error = e2
                        logger.error(f"Reattach after duplicate-running failed: {e2}")
            if isinstance(last_error, RendererUnavailableError):
                raise last_error
            raise Exception(f"Connect failed: {last_error}")

    def _disconnect(self, keep_ws: bool = True, keep_pw_runtime: bool = False):
        try:
            if self._page:
                try:
                    self._page.close()
                except Exception:
                    pass
            if self._context:
                try:
                    self._context.close()
                except Exception:
                    pass
            if self._browser:
                try:
                    self._browser.close()
                except Exception:
                    pass
            if self._pw and not keep_pw_runtime:
                try:
                    self._pw.__exit__(None, None, None)
                except Exception:
                    pass
        except Exception:
            pass
        self._browser = None
        self._context = None
        self._page = None
        if not keep_pw_runtime:
            self._pw = None
        if not keep_ws:
            self._ws_endpoint = None

    @staticmethod
    def _looks_like_product_url(url: str) -> bool:
        """Heuristic: product pages usually end with a long numeric id in slug."""
        path = urlparse(url).path
        parts = [p for p in path.strip("/").split("/") if p]
        if len(parts) < 3:
            return False
        if any(p.startswith("trademark=") or p.startswith("brand=") for p in parts):
            return False
        slug = parts[-1]
        return bool(re.search(r"\d{4,}", slug))

    def _click_tab_if_present(self, tab_label: str) -> bool:
        if self._page is None:
            return False
        locators = [
            self._page.get_by_role("tab", name=tab_label, exact=False),
            self._page.get_by_role("button", name=tab_label, exact=False),
            self._page.get_by_text(tab_label, exact=False),
        ]
        for locator in locators:
            try:
                if locator.count() > 0:
                    locator.first.click(timeout=3000)
                    try:
                        self._page.wait_for_load_state("networkidle", timeout=4000)
                    except Exception:
                        pass
                    self._page.wait_for_timeout(900)
                    return True
            except Exception:
                continue
        return False

    def _prime_product_tabs(self, url: str):
        """
        Some product tab content is loaded lazily after click.
        Prime important tabs before snapshotting page HTML.
        """
        if self._page is None or not self._looks_like_product_url(url):
            return
        for label in [
            "Совместимость с автомобилем",
            "Compatible vehicles",
            "Оригинальные предложения",
            "Аналоги (заменители)",
            "Оригинальные номера",
        ]:
            self._click_tab_if_present(label)

    def _expand_listing_show_more(self, url: str):
        """
        On listing pages 2407 can lazy-load extra products via "Показать еще".
        Click it repeatedly before collecting HTML.
        """
        if self._page is None or self._looks_like_product_url(url):
            return
        if self.max_show_more_clicks <= 0:
            return
        click_count = 0
        for _ in range(self.max_show_more_clicks):
            locator = self._page.get_by_role(
                "button",
                name=re.compile(r"Показать\s+еще|Show\s+more", re.I),
            )
            if locator.count() == 0:
                locator = self._page.get_by_text(
                    re.compile(r"Показать\s+еще|Show\s+more", re.I)
                )
            if locator.count() == 0:
                break
            try:
                btn = locator.first
                btn.scroll_into_view_if_needed(timeout=2000)
                btn.click(timeout=4000)
                click_count += 1
                try:
                    self._page.wait_for_load_state("networkidle", timeout=4500)
                except Exception:
                    pass
                self._page.wait_for_timeout(1200)
            except Exception:
                break
        if click_count:
            logger.debug(f"Expanded listing via show-more clicks: {click_count} at {url}")

    def _is_error_html(self, html: str, url: str, silent: bool = False) -> bool:
        """
        Detect Cloudflare / waiting / gateway / Dolphin error pages
        that should NOT be treated as valid category/product HTML.
        """
        if not html:
            return True
        low = html.lower()
        for marker in _CF_BODY_MARKERS:
            if marker in low:
                if not silent:
                    logger.warning(f"Detected interstitial/error HTML for {url}: marker={marker}")
                return True
        return False

    def _ensure_connected_page(self):
        if self._page is not None:
            return
        self._connect()
        if self._page is None:
            raise RuntimeError("Dolphin page is not initialized after connect")

    def fetch_html(self, url: str) -> Optional[str]:
        self._ensure_connected_page()

        for attempt in range(self.max_retries):
            try:
                self._ensure_connected_page()
                time.sleep(random.uniform(self.delay_min, self.delay_max))

                # Use "load" so Turnstile/Cloudflare JS has time to initialise
                self._page.goto(url, wait_until="load", timeout=60000)

                # Give Turnstile JS time to execute before first check
                time.sleep(12)

                # Wait for Cloudflare challenge to resolve — check title AND body
                for wait_i in range(25):
                    try:
                        title = self._page.title()
                        html_snap = self._page.content()
                    except Exception as page_err:
                        logger.warning(
                            f"Page read error during CF wait ({wait_i + 1}/25): {page_err}"
                        )
                        break
                    low_title = title.lower()
                    cf_in_title = (
                        "момент" in low_title
                        or "moment" in low_title
                        or "checking" in low_title
                        or "just a moment" in low_title
                        or "verify" in low_title
                    )
                    cf_in_body = self._is_error_html(html_snap, url, silent=True)
                    if not cf_in_title and not cf_in_body:
                        break
                    logger.debug(
                        f"Waiting for Cloudflare ({wait_i + 1}/25): title={title!r}"
                    )
                    time.sleep(6)

                self._expand_listing_show_more(url)
                self._prime_product_tabs(url)
                time.sleep(2)

                try:
                    html = self._page.content()
                except Exception as page_err:
                    logger.warning(f"Page content read failed after CF wait: {page_err}")
                    raise RuntimeError(
                        f"Page content unavailable after CF wait: {page_err}"
                    )

                if self._is_error_html(html, url):
                    logger.warning(f"Rejected error/interstitial page for {url}")
                    raise RuntimeError(
                        "Received Cloudflare / 504 / interstitial HTML instead of real page"
                    )
                if html and len(html) > 1000:
                    return html
                logger.warning(f"Short response ({len(html) if html else 0}) for {url}")
                raise RuntimeError(f"Short/invalid HTML for {url}")

            except Exception as e:
                if isinstance(e, RendererUnavailableError):
                    logger.error(f"Renderer unavailable for {url}: {e}")
                    raise
                logger.warning(f"Dolphin fetch error ({attempt + 1}): {e} for {url}")
                time.sleep(3 * (attempt + 1))
                # Reconnect on every failure, not just after the first attempt
                try:
                    self._disconnect(keep_ws=True, keep_pw_runtime=True)
                    time.sleep(2)
                    self._connect()
                except Exception as e2:
                    if isinstance(e2, RendererUnavailableError):
                        logger.error(f"Reconnect failed permanently: {e2}")
                        raise
                    if self._is_sync_api_in_async_loop_error(e2):
                        logger.warning(
                            "Reconnect failed due to Playwright sync-in-async-loop guard; "
                            "forcing full runtime reset"
                        )
                        try:
                            self._disconnect(keep_ws=False, keep_pw_runtime=False)
                            time.sleep(2)
                            self._connect()
                            continue
                        except Exception as e3:
                            logger.error(f"Reconnect after full reset failed: {e3}")
                    logger.error(f"Reconnect failed: {e2}")

        logger.error(f"All retries failed for {url}")
        return None

    def setup_poland(self):
        """Visit site to set Poland context."""
        self._ensure_connected_page()
        try:
            self._page.goto("https://2407.pl/ru/", wait_until="domcontentloaded", timeout=30000)
            time.sleep(3)
            logger.info("Poland context ready")
        except Exception as e:
            logger.warning(f"Poland setup error: {e}")

    def close(self):
        self._disconnect(keep_ws=False)
        self._stop_profile()


class AdaptiveRenderer:
    """Dolphin-based renderer with the same interface as before."""

    def __init__(
        self,
        profile_id: str = DOLPHIN_PROFILE_ID,
        delay_min: float = 2.0,
        delay_max: float = 5.0,
        max_retries: int = 3,
        **kwargs,
    ):
        self.dolphin = DolphinRenderer(
            profile_id=profile_id,
            delay_min=delay_min,
            delay_max=delay_max,
            max_retries=max_retries,
        )

    def fetch(self, url: str, force_playwright: bool = False):
        """Returns (html, mode)."""
        html = self.dolphin.fetch_html(url)
        return html, "dolphin"

    def fetch_html(self, url: str) -> Optional[str]:
        return self.dolphin.fetch_html(url)

    def setup_poland(self):
        self.dolphin.setup_poland()

    def close(self):
        self.dolphin.close()
