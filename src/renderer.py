"""
Renderer module: uses Dolphin Anty browser via local CDP API.
Features:
  - Cookie persistence (save/load cookies.json)
  - 2Captcha solver for reCAPTCHA v2 / Turnstile
  - Residential proxy rotation
  - Stealth via Dolphin Anty antidetect browser
"""
import json
import logging
import os
import random
import re
import time
from typing import Optional, List
from urllib.parse import urlparse
import requests

logger = logging.getLogger(__name__)

DOLPHIN_API_URL = "http://localhost:3001/v1.0"
DOLPHIN_PROFILE_ID = os.environ.get("DOLPHIN_PROFILE_ID", "759890630")
MAX_SHOW_MORE_CLICKS = int(os.environ.get("MAX_SHOW_MORE_CLICKS", "25"))

# 2Captcha config
CAPTCHA_API_KEY = os.environ.get("CAPTCHA_API_KEY", "")

# Cookie persistence file
COOKIES_FILE = os.environ.get("COOKIES_FILE", "cookies.json")

# Residential proxies list — set via env as comma-separated URLs
# e.g. PROXIES=http://user:pass@host1:port,http://user:pass@host2:port
_PROXIES_RAW = os.environ.get("PROXIES", "")
PROXY_LIST: List[str] = [p.strip() for p in _PROXIES_RAW.split(",") if p.strip()]

# Fallback User-Agent pool (Dolphin handles UA, but used as backup)
_USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.4 Safari/605.1.15",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
]

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


# ---------------------------------------------------------------------------
# Cookie persistence helpers
# ---------------------------------------------------------------------------

def load_cookies(path: str = COOKIES_FILE) -> Optional[list]:
    """Load cookies from JSON file. Returns None if file missing or invalid."""
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            cookies = json.load(f)
        logger.info(f"Loaded {len(cookies)} cookies from {path}")
        return cookies
    except Exception as e:
        logger.warning(f"Failed to load cookies from {path}: {e}")
        return None


def save_cookies(cookies: list, path: str = COOKIES_FILE):
    """Persist cookies list to JSON file."""
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(cookies, f, indent=2, ensure_ascii=False)
        logger.info(f"Saved {len(cookies)} cookies to {path}")
    except Exception as e:
        logger.warning(f"Failed to save cookies to {path}: {e}")


# ---------------------------------------------------------------------------
# Proxy rotation helper
# ---------------------------------------------------------------------------

def pick_proxy() -> Optional[str]:
    """Return a random proxy from PROXY_LIST, or None if list is empty."""
    if not PROXY_LIST:
        return None
    proxy = random.choice(PROXY_LIST)
    logger.debug(f"Selected proxy: {proxy}")
    return proxy


# ---------------------------------------------------------------------------
# 2Captcha solver
# ---------------------------------------------------------------------------

class CaptchaSolver:
    """
    Solves reCAPTCHA v2 and Cloudflare Turnstile via 2Captcha API.
    Requires CAPTCHA_API_KEY env variable.
    """

    BASE_URL = "http://2captcha.com"
    POLL_INTERVAL = 5       # seconds between status checks
    MAX_POLL_ATTEMPTS = 60  # ~5 minutes max wait

    def __init__(self, api_key: str = CAPTCHA_API_KEY):
        self.api_key = api_key

    def _is_available(self) -> bool:
        return bool(self.api_key)

    def solve_recaptcha_v2(self, site_key: str, page_url: str) -> Optional[str]:
        """Submit reCAPTCHA v2 to 2Captcha, wait for solution, return token."""
        if not self._is_available():
            logger.warning("2Captcha API key not set — skipping reCAPTCHA solve")
            return None
        try:
            resp = requests.get(
                f"{self.BASE_URL}/in.php",
                params={
                    "key": self.api_key,
                    "method": "userrecaptcha",
                    "googlekey": site_key,
                    "pageurl": page_url,
                    "json": 1,
                },
                timeout=15,
            )
            data = resp.json()
            if data.get("status") != 1:
                logger.warning(f"2Captcha submit failed: {data}")
                return None
            request_id = data["request"]
            logger.info(f"2Captcha reCAPTCHA submitted, id={request_id}")
            return self._poll_result(request_id)
        except Exception as e:
            logger.warning(f"2Captcha reCAPTCHA solve error: {e}")
            return None

    def solve_turnstile(self, site_key: str, page_url: str) -> Optional[str]:
        """Submit Cloudflare Turnstile to 2Captcha, wait for solution."""
        if not self._is_available():
            logger.warning("2Captcha API key not set — skipping Turnstile solve")
            return None
        try:
            resp = requests.get(
                f"{self.BASE_URL}/in.php",
                params={
                    "key": self.api_key,
                    "method": "turnstile",
                    "sitekey": site_key,
                    "pageurl": page_url,
                    "json": 1,
                },
                timeout=15,
            )
            data = resp.json()
            if data.get("status") != 1:
                logger.warning(f"2Captcha Turnstile submit failed: {data}")
                return None
            request_id = data["request"]
            logger.info(f"2Captcha Turnstile submitted, id={request_id}")
            return self._poll_result(request_id)
        except Exception as e:
            logger.warning(f"2Captcha Turnstile solve error: {e}")
            return None

    def _poll_result(self, request_id: str) -> Optional[str]:
        """Poll 2Captcha until solution is ready."""
        for attempt in range(self.MAX_POLL_ATTEMPTS):
            time.sleep(self.POLL_INTERVAL)
            try:
                resp = requests.get(
                    f"{self.BASE_URL}/res.php",
                    params={
                        "key": self.api_key,
                        "action": "get",
                        "id": request_id,
                        "json": 1,
                    },
                    timeout=10,
                )
                data = resp.json()
                if data.get("status") == 1:
                    token = data["request"]
                    logger.info(f"2Captcha solved after {(attempt + 1) * self.POLL_INTERVAL}s")
                    return token
                if data.get("request") != "CAPCHA_NOT_READY":
                    logger.warning(f"2Captcha unexpected response: {data}")
                    return None
            except Exception as e:
                logger.warning(f"2Captcha poll error: {e}")
        logger.warning("2Captcha: timed out waiting for solution")
        return None


# ---------------------------------------------------------------------------
# Renderer
# ---------------------------------------------------------------------------

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
        self._captcha_solver = CaptchaSolver()
        self._cookies_loaded = False

    # ------------------------------------------------------------------
    # Dolphin profile management
    # ------------------------------------------------------------------

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
                    raise Exception(
                        f"Dolphin start response missing automation data: {data}"
                    )
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
        """Connect to Dolphin. Reuse existing ws endpoint when possible."""
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

    # ------------------------------------------------------------------
    # Cookie helpers
    # ------------------------------------------------------------------

    def _restore_cookies(self):
        """Load cookies from disk and inject into current context."""
        if self._cookies_loaded or self._context is None:
            return
        cookies = load_cookies()
        if cookies:
            try:
                self._context.add_cookies(cookies)
                logger.info("Cookies restored into browser context")
            except Exception as e:
                logger.warning(f"Failed to inject cookies: {e}")
        self._cookies_loaded = True

    def _persist_cookies(self):
        """Save current context cookies to disk."""
        if self._context is None:
            return
        try:
            cookies = self._context.cookies()
            if cookies:
                save_cookies(cookies)
        except Exception as e:
            logger.warning(f"Failed to persist cookies: {e}")

    # ------------------------------------------------------------------
    # Page interaction helpers
    # ------------------------------------------------------------------

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
        """Prime lazy-loaded product tabs before snapshotting HTML."""
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
        """Click 'Показать еще' repeatedly to expand lazy-loaded listings."""
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
        """Detect Cloudflare / gateway / Dolphin error pages."""
        if not html:
            return True
        low = html.lower()
        for marker in _CF_BODY_MARKERS:
            if marker in low:
                if not silent:
                    logger.warning(
                        f"Detected interstitial/error HTML for {url}: marker={marker}"
                    )
                return True
        return False

    # ------------------------------------------------------------------
    # Captcha detection & solving
    # ------------------------------------------------------------------

    def _try_solve_captcha(self, url: str) -> bool:
        """
        Detect reCAPTCHA v2 or Cloudflare Turnstile on current page
        and solve via 2Captcha. Returns True if a solution was injected.
        """
        if self._page is None or not self._captcha_solver._is_available():
            return False

        # --- Cloudflare Turnstile ---
        try:
            turnstile_el = self._page.query_selector(
                "div[class*='cf-turnstile'], iframe[src*='challenges.cloudflare.com']"
            )
            if turnstile_el:
                site_key = turnstile_el.get_attribute("data-sitekey")
                if site_key:
                    logger.info(f"Turnstile detected sitekey={site_key}, solving via 2Captcha...")
                    token = self._captcha_solver.solve_turnstile(site_key, url)
                    if token:
                        # Inject token into hidden input and submit
                        self._page.evaluate(
                            """(token) => {
                                const inp = document.querySelector(
                                    'input[name="cf-turnstile-response"]'
                                );
                                if (inp) inp.value = token;
                                const form = document.querySelector('form');
                                if (form) form.submit();
                            }""",
                            token,
                        )
                        try:
                            self._page.wait_for_load_state("networkidle", timeout=15000)
                        except Exception:
                            pass
                        logger.info("Turnstile token injected and form submitted")
                        return True
        except Exception as e:
            logger.debug(f"Turnstile detection error: {e}")

        # --- reCAPTCHA v2 ---
        try:
            recaptcha_el = self._page.query_selector(".g-recaptcha")
            if recaptcha_el:
                site_key = recaptcha_el.get_attribute("data-sitekey")
                if site_key:
                    logger.info(
                        f"reCAPTCHA v2 detected sitekey={site_key}, solving via 2Captcha..."
                    )
                    token = self._captcha_solver.solve_recaptcha_v2(site_key, url)
                    if token:
                        self._page.evaluate(
                            """(solution) => {
                                const el = document.querySelector('#g-recaptcha-response');
                                if (el) el.innerHTML = solution;
                                const btn = document.querySelector('#submit-button')
                                    || document.querySelector('button[type=submit]')
                                    || document.querySelector('input[type=submit]');
                                if (btn) btn.click();
                            }""",
                            token,
                        )
                        try:
                            self._page.wait_for_navigation(
                                wait_until="networkidle", timeout=15000
                            )
                        except Exception:
                            pass
                        logger.info("reCAPTCHA token injected and form submitted")
                        return True
        except Exception as e:
            logger.debug(f"reCAPTCHA detection error: {e}")

        return False

    # ------------------------------------------------------------------
    # Core fetch
    # ------------------------------------------------------------------

    def _ensure_connected_page(self):
        if self._page is not None:
            return
        self._connect()
        if self._page is None:
            raise RuntimeError("Dolphin page is not initialized after connect")
        # Restore saved cookies once after (re-)connecting
        self._restore_cookies()

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

                    if cf_in_title or cf_in_body:
                        # Attempt captcha solve on first CF encounter
                        if wait_i == 0:
                            solved = self._try_solve_captcha(url)
                            if solved:
                                time.sleep(5)
                                continue
                        logger.debug(
                            f"Waiting for Cloudflare ({wait_i + 1}/25): title={title!r}"
                        )
                        time.sleep(6)
                    else:
                        break

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
                    # Persist cookies after each successful fetch
                    self._persist_cookies()
                    return html

                logger.warning(f"Short response ({len(html) if html else 0}) for {url}")
                raise RuntimeError(f"Short/invalid HTML for {url}")

            except Exception as e:
                if isinstance(e, RendererUnavailableError):
                    logger.error(f"Renderer unavailable for {url}: {e}")
                    raise
                logger.warning(f"Dolphin fetch error ({attempt + 1}): {e} for {url}")
                time.sleep(3 * (attempt + 1))
                # Reconnect on every failure
                try:
                    self._disconnect(keep_ws=True, keep_pw_runtime=True)
                    time.sleep(2)
                    self._connect()
                    self._restore_cookies()
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
                            self._restore_cookies()
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
            self._page.goto(
                "https://2407.pl/ru/", wait_until="domcontentloaded", timeout=30000
            )
            time.sleep(3)
            self._persist_cookies()
            logger.info("Poland context ready")
        except Exception as e:
            logger.warning(f"Poland setup error: {e}")

    def close(self):
        self._persist_cookies()
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
