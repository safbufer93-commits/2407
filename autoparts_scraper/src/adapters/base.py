"""
Base adapter interface and shared data models.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import List, Optional
from enum import Enum


class TaskStatus(str, Enum):
    CREATED = "CREATED"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"
    NOT_FOUND = "NOT_FOUND"
    BLOCKED_CAPTCHA = "BLOCKED_CAPTCHA"
    LOGIN_REQUIRED = "LOGIN_REQUIRED"
    TIMEOUT = "TIMEOUT"
    REQUIRES_PHONE = "REQUIRES_PHONE"
    PARSE_ERROR = "PARSE_ERROR"
    INVALID_SKU = "INVALID_SKU"
    REGION_REQUIRED = "REGION_REQUIRED"


class MatchType(str, Enum):
    EXACT = "EXACT"
    ANALOG = "ANALOG"
    REPLACEMENT = "REPLACEMENT"
    UNKNOWN = "UNKNOWN"


@dataclass
class OfferParsed:
    site: str
    header_brand: str
    name: str
    sku: str
    price: Optional[float]
    availability: str
    # Optional extended fields
    source_url: str = ""
    match_type: MatchType = MatchType.UNKNOWN
    page_no: int = 1
    row_no: int = 0
    currency: str = "RUB"
    scraped_at: str = field(
        default_factory=lambda: datetime.now(tz=timezone.utc).isoformat()
    )
    sku_queried: str = ""   # original SKU that triggered this search


@dataclass
class ScrapeResult:
    status: TaskStatus
    offers: List[OfferParsed] = field(default_factory=list)
    error_message: str = ""
    screenshot_path: str = ""
    trace_path: str = ""
    html_path: str = ""
    http_status: Optional[int] = None
    retry_after: Optional[int] = None


class BaseSiteAdapter:
    """
    Each site adapter must implement `scrape()`.
    The Playwright `page` object is provided by the runner.
    """
    site_id: str = ""
    base_url: str = ""

    async def scrape(
        self,
        page,   # playwright.async_api.Page
        sku_norm: str,
        sku_raw: str,
        config: object,
        artifacts_dir: str,
        log,
    ) -> ScrapeResult:
        raise NotImplementedError

    # ------------------------------------------------------------------
    # Shared helpers available to all adapters
    # ------------------------------------------------------------------

    async def _save_artifacts(
        self,
        page,
        artifacts_dir: str,
        prefix: str,
        save_trace: bool = False,
    ) -> tuple[str, str]:
        """Save screenshot + optional HTML snapshot. Returns (screenshot_path, html_path)."""
        import os
        os.makedirs(artifacts_dir, exist_ok=True)
        ts = datetime.now(tz=timezone.utc).strftime("%Y%m%dT%H%M%S")
        scr_path = os.path.join(artifacts_dir, f"{prefix}_{ts}.png")
        html_path = os.path.join(artifacts_dir, f"{prefix}_{ts}.html")
        try:
            await page.screenshot(path=scr_path, full_page=False)
        except Exception:
            scr_path = ""
        try:
            html = await page.content()
            with open(html_path, "w", encoding="utf-8") as f:
                f.write(html)
        except Exception:
            html_path = ""
        return scr_path, html_path

    async def _detect_blocked(self, page) -> Optional[TaskStatus]:
        """
        Detect common block patterns: captcha, login wall.
        Returns TaskStatus if blocked, else None.
        """
        try:
            content = await page.content()
        except Exception:
            return None
        cl = content.lower()
        if any(kw in cl for kw in ("security check", "enter the code", "captcha", "recaptcha")):
            return TaskStatus.BLOCKED_CAPTCHA
        if any(kw in cl for kw in ("авторизация", "войти", "login required", "sign in", "log in")):
            # Only treat as LOGIN_REQUIRED if there's no meaningful content
            # (heuristic: check if page has a search/results section too)
            return None  # subclasses can override for stricter detection
        return None
