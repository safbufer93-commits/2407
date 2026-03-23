"""
Renderer module: uses Dolphin Anty browser via local CDP API.
Falls back to direct Playwright if Dolphin not available.
"""
import logging
import os
import time
import random
import requests
from typing import Optional
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

DOLPHIN_API_URL = "http://localhost:3001/v1.0"
DOLPHIN_PROFILE_ID = os.environ.get("DOLPHIN_PROFILE_ID", "759890630")

# Markers in page title that indicate Cloudflare challenge
CLOUDFLARE_TITLE_MARKERS = [
    "момент", "moment", "checking", "just a moment",
    "attention required", "cloudflare", "403 forbidden",
    "access denied", "ddos", "verify you are human",
]

# Markers in HTML body that indicate Cloudflare interstitial/error page
CLOUDFLARE_BODY_MARKERS = [
    "challenge-error-text",
    "cf-error-details",
    "cf_chl_prog",
    "__cf_chl_",
    "Cloudflare Ray ID",
    "DDoS protection by Cloudflare",
    "cf-spinner",
    "jschl-answer",
    "challenge-form",
]


class DolphinRenderer:
    """Renders pages using Dolphin Anty antidect browser."""

    def __init__(self, profile_id: str = DOLPHIN_PROFILE_ID,
                 delay_min: float = 2.0, delay_max: float = 5.0,
                 max_retries: int = 3):
        self.profile_id = profile_id
        self.delay_min = delay_min
        self.delay_max = delay_max
        self.max_retries = max_retries
        self._browser = None
        self._context = None
        self._page = None
        self._pw = None
        self._ws_endpoint = None

    def _start_profile(self) -> str:
        """Start Dolphin profile, return ws endpoint."""
        url = f"{DOLPHIN_API_URL}/browser_profiles/{self.profile_id}/start?automation=1"
        logger.info(f"Starting Dolphin profile {self.profile_id}...")
        r = requests.get(url, timeout=30)
        data = r.json()
        if not data.get("success"):
            raise Exception(f"Dolphin start failed: {data}")
        port = data["automation"]["port"]
        ws_path = data["automation"]["wsEndpoint"]
        ws_endpoint = f"ws://localhost:{port}{ws_path}"
        logger.info(f"Dolphin started: {ws_endpoint}")
        return ws_endpoint

    def _stop_profile(self):
        try:
            url = f"{DOLPHIN_API_URL}/browser_profiles/{self.profile_id}/stop"
            requests.get(url, timeout=10)
            logger.info("Dolphin profile stopped")
        except Exception:
            pass

    def _connect(self):
        from playwright.sync_api import sync_playwright
        self._ws_endpoint = self._start_profile()
        time.sleep(3)
        self._pw = sync_playwright().__enter__()
        self._browser = self._pw.chromium.connect_over_cdp(self._ws_endpoint)
        contexts = self._browser.contexts
        if contexts:
            self._context = contexts[0]
            pages = self._context.pages
            self._page = pages[0] if pages else self._context.new_page()
        else:
            self._context = self._browser.new_context()
            self._page = self._context.new_page()
        logger.info("Connected to Dolphin browser")

    def _disconnect(self):
        try:
            if self._pw:
                self._pw.__exit__(None, None, None)
        except Exception:
            pass
        self._browser = None
        self._context = None
        self._page = None
        self._pw = None

    def _detect_cloudflare(self, html: str, title: str) -> Optional[str]:
        """Return the detected marker string if the page is a Cloudflare
        interstitial/error, or None if the page looks real."""
        title_lower = title.lower()
        for marker in CLOUDFLARE_TITLE_MARKERS:
            if marker in title_lower:
                return f"title:{marker}"
        for marker in CLOUDFLARE_BODY_MARKERS:
            if marker in html:
                return marker
        return None

    def fetch_html(self, url: str) -> Optional[str]:
        if self._page is None:
            self._connect()

        for attempt in range(self.max_retries):
            try:
                time.sleep(random.uniform(self.delay_min, self.delay_max))
                # Use "load" instead of "domcontentloaded" so JS (Turnstile) has time to start
                self._page.goto(url, wait_until="load", timeout=60000)

                # Wait before first check — give Turnstile JS time to execute
                time.sleep(12)

                # Wait for Cloudflare challenge to resolve (checks both title and body)
                resolved = False
                for wait_i in range(25):
                    try:
                        title = self._page.title()
                        html_snap = self._page.content()
                    except Exception as page_err:
                        logger.warning(f"Page read error during CF wait ({wait_i}): {page_err}")
                        break
                    marker = self._detect_cloudflare(html_snap, title)
                    if marker is None:
                        resolved = True
                        break
                    logger.warning(
                        f"Detected interstitial/error HTML for {url}: marker={marker}"
                    )
                    time.sleep(6)

                time.sleep(2)
                try:
                    html = self._page.content()
                except Exception as page_err:
                    logger.warning(f"Page content read failed after CF wait: {page_err}")
                    raise Exception(f"Page content unavailable after CF wait: {page_err}")

                # Final check after waiting
                if not resolved:
                    try:
                        marker = self._detect_cloudflare(html, self._page.title())
                    except Exception:
                        marker = self._detect_cloudflare(html, "")
                    if marker:
                        logger.warning(f"Rejected error/interstitial page for {url}")
                        raise Exception(
                            f"Received Cloudflare / 504 / interstitial HTML instead of real page"
                        )

                if html and len(html) > 1000:
                    return html
                logger.warning(f"Short response ({len(html) if html else 0}) for {url}")
                return html

            except Exception as e:
                logger.warning(f"Dolphin fetch error ({attempt+1}): {e} for {url}")
                time.sleep(3 * (attempt + 1))
                # Reconnect on every failure, not just after first attempt
                try:
                    self._disconnect()
                    self._connect()
                except Exception as e2:
                    logger.error(f"Reconnect failed: {e2}")

        logger.error(f"All retries failed for {url}")
        return None

    def setup_poland(self):
        """Visit site to set Poland context."""
        if self._page is None:
            self._connect()
        try:
            self._page.goto("https://2407.pl/ru/", wait_until="domcontentloaded", timeout=30000)
            time.sleep(3)
            logger.info("Poland context ready")
        except Exception as e:
            logger.warning(f"Poland setup error: {e}")

    def close(self):
        self._disconnect()
        self._stop_profile()


class AdaptiveRenderer:
    """Dolphin-based renderer with the same interface as before."""

    def __init__(self, profile_id: str = DOLPHIN_PROFILE_ID,
                 delay_min: float = 2.0, delay_max: float = 5.0, **kwargs):
        self.dolphin = DolphinRenderer(
            profile_id=profile_id,
            delay_min=delay_min,
            delay_max=delay_max,
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
