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

    def _is_cloudflare_challenge(self) -> bool:
        """Check if current page shows a Cloudflare challenge."""
        title = self._page.title().lower()
        cf_titles = ["even geduld", "момент", "moment", "checking", "just a moment",
                     "attention required", "beveiliging wordt geverifieerd"]
        if any(t in title for t in cf_titles):
            return True
        # Check for CF challenge iframe
        for frame in self._page.frames:
            if "challenges.cloudflare.com" in frame.url:
                return True
        # Check page content for CF markers (silent block without iframe)
        try:
            body = self._page.inner_text("body")
            cf_body = ["verifieer dat je mens bent", "verify you are human",
                       "подтвердите, что вы человек", "cf-browser-verification",
                       "ray id", "checking your browser"]
            if any(m in body.lower() for m in cf_body):
                return True
        except Exception:
            pass
        return False

    def _handle_cloudflare_challenge(self) -> bool:
        """Click Cloudflare Turnstile checkbox. Returns True if click was attempted."""
        try:
            # Method 1: click the CF iframe element directly via mouse coordinates
            # This is the most reliable way - bypasses all shadow DOM complexity
            for iframe_sel in [
                "iframe[src*='challenges.cloudflare.com']",
                "iframe[src*='challenge-platform']",
                "iframe[title*='cloudflare' i]",
                "iframe[title*='challenge' i]",
            ]:
                try:
                    iframe_el = self._page.locator(iframe_sel).first
                    if iframe_el.count() > 0:
                        box = iframe_el.bounding_box()
                        if box and box["width"] > 0 and box["height"] > 0:
                            # Click center of the CF widget (where the checkbox is)
                            cx = box["x"] + box["width"] / 2
                            cy = box["y"] + box["height"] / 2
                            self._page.mouse.click(cx, cy)
                            logger.info(f"Clicked CF iframe via mouse at ({cx:.0f}, {cy:.0f})")
                            time.sleep(5)
                            return True
                except Exception:
                    continue

            # Method 2: click inside the CF frame using frame locator
            cf_frame = None
            for frame in self._page.frames:
                if "challenges.cloudflare.com" in frame.url:
                    cf_frame = frame
                    break

            if cf_frame:
                logger.info("CF frame found, attempting selector click...")
                for selector in [
                    'input[type="checkbox"]',
                    ".ctp-checkbox-label",
                    'label[for="cf-stage"]',
                    "#cf-stage",
                    "body",
                ]:
                    try:
                        loc = cf_frame.locator(selector)
                        if loc.count() > 0:
                            loc.first.click(timeout=5000, force=True)
                            logger.info(f"Clicked CF element: {selector}")
                            time.sleep(5)
                            return True
                    except Exception:
                        continue

        except Exception as e:
            logger.debug(f"CF challenge handler error: {e}")
        return False

    def fetch_html(self, url: str) -> Optional[str]:
        if self._page is None:
            self._connect()

        for attempt in range(self.max_retries):
            try:
                time.sleep(random.uniform(self.delay_min, self.delay_max))
                self._page.goto(url, wait_until="domcontentloaded", timeout=60000)

                # Wait for Cloudflare challenge and handle it
                # Try clicking once every 3 seconds, up to 90s total
                cf_click_attempted = False
                for tick in range(30):  # 30 * 3s = 90s max
                    if not self._is_cloudflare_challenge():
                        break
                    # Try to click on tick 0, 5, 10 (every 15s after first)
                    if tick == 0 or tick % 5 == 0:
                        clicked = self._handle_cloudflare_challenge()
                        if clicked and not cf_click_attempted:
                            cf_click_attempted = True
                            logger.info("CF click attempted, waiting for challenge to resolve...")
                    else:
                        logger.debug(f"Waiting for CF to clear (tick {tick}/30)...")
                    time.sleep(3)
                else:
                    # Loop exhausted - CF did not clear in 90s
                    logger.warning(
                        f"Cloudflare challenge was not cleared within 90s for {url}. "
                        "Please click the checkbox manually in the Dolphin browser window."
                    )
                    # Extra 60s grace period for manual solve
                    for _ in range(20):
                        if not self._is_cloudflare_challenge():
                            break
                        time.sleep(3)

                time.sleep(2)
                html = self._page.content()
                if html and len(html) > 1000:
                    return html
                logger.warning(f"Short response ({len(html) if html else 0}) for {url}, retrying...")
                time.sleep(3)

            except Exception as e:
                logger.warning(f"Dolphin fetch error ({attempt + 1}): {e} for {url}")
                time.sleep(3 * (attempt + 1))
                if attempt >= 1:
                    # Reconnect to avoid E_BROWSER_RUN_DUPLICATE
                    try:
                        self._disconnect()
                        self._stop_profile()
                        time.sleep(2)
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
