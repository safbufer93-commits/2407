"""
Configuration for auto parts price scraper.
All target sites and tuning parameters are defined here.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Dict, List, Optional
import os

# ---------------------------------------------------------------------------
# Target sites (source-of-truth from TZ)
# ---------------------------------------------------------------------------
SITES: List[Dict] = [
    {
        "id": "autopiter",
        "base_url": "https://autopiter.ru/",
        "per_domain_concurrency": 2,
        "enabled": True,
    },
    {
        "id": "armtek",
        "base_url": "http://armtek.ru",
        "per_domain_concurrency": 2,
        "enabled": True,
    },
    {
        "id": "tatparts",
        "base_url": "https://www.tatparts.ru/",
        "per_domain_concurrency": 1,
        "enabled": True,
    },
    {
        "id": "truckdrive",
        "base_url": "https://truckdrive.ru/",
        "per_domain_concurrency": 1,
        "enabled": True,
    },
    {
        "id": "saleparts",
        "base_url": "https://sale-parts.ru/",
        "per_domain_concurrency": 1,
        "enabled": True,
    },
    {
        "id": "market_tmtr",
        "base_url": "https://market.tmtr.ru/",
        "per_domain_concurrency": 1,
        "enabled": True,
    },
    {
        "id": "tsmavto",
        "base_url": "https://tsmavto.ru/",
        "per_domain_concurrency": 1,
        "enabled": True,
    },
]

# ---------------------------------------------------------------------------
# Browser / Playwright settings
# ---------------------------------------------------------------------------
HEADLESS: bool = os.environ.get("HEADLESS", "true").lower() != "false"
USER_AGENT: str = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)
LOCALE: str = "ru-RU"
NAVIGATION_TIMEOUT_MS: int = int(os.environ.get("NAVIGATION_TIMEOUT_MS", "45000"))
SELECTOR_TIMEOUT_MS: int = int(os.environ.get("SELECTOR_TIMEOUT_MS", "15000"))
PAGE_LOAD_STATE: str = "domcontentloaded"

# ---------------------------------------------------------------------------
# Concurrency
# ---------------------------------------------------------------------------
GLOBAL_CONCURRENCY: int = int(os.environ.get("GLOBAL_CONCURRENCY", "4"))

# ---------------------------------------------------------------------------
# Retry / rate limiting
# ---------------------------------------------------------------------------
MAX_ATTEMPTS: int = int(os.environ.get("MAX_ATTEMPTS", "3"))
BACKOFF_BASE_SEC: float = 2.0
BACKOFF_MAX_SEC: float = 30.0
BACKOFF_JITTER: float = 0.5  # fraction of backoff interval added randomly

# ---------------------------------------------------------------------------
# Pagination
# ---------------------------------------------------------------------------
MAX_PAGES: int = int(os.environ.get("MAX_PAGES", "5"))  # 0 = unlimited
MAX_MANUFACTURERS: int = int(os.environ.get("MAX_MANUFACTURERS", "3"))

# ---------------------------------------------------------------------------
# SKU normalisation
# ---------------------------------------------------------------------------
# If True, strip hyphens/dots when submitting to search; raw value is kept
SKU_STRIP_SEPARATORS_FOR_SEARCH: bool = True

# ---------------------------------------------------------------------------
# Brand synonym map  (canonical -> list of aliases, all upper-cased)
# ---------------------------------------------------------------------------
BRAND_SYNONYMS: Dict[str, List[str]] = {
    "MERCEDES": ["MERCEDES-BENZ", "MERCEDES BENZ", "MB"],
    "VAG": ["VOLKSWAGEN", "VW", "AUDI", "SEAT", "SKODA"],
    "BMW": ["BMW AG"],
    "TOYOTA": ["ТОЙОТА"],
}

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
BASE_DIR: str = os.path.dirname(os.path.abspath(__file__))
INPUT_DIR: str = os.path.join(BASE_DIR, "input")
OUTPUT_DIR: str = os.path.join(BASE_DIR, "output")
ARTIFACTS_DIR: str = os.path.join(BASE_DIR, "artifacts")
LOG_DIR: str = os.path.join(BASE_DIR, "logs")

# ---------------------------------------------------------------------------
# Credentials (populated via env-vars; never hardcode)
# ---------------------------------------------------------------------------
MARKET_TMTR_LOGIN: Optional[str] = os.environ.get("MARKET_TMTR_LOGIN")
MARKET_TMTR_PASSWORD: Optional[str] = os.environ.get("MARKET_TMTR_PASSWORD")

# ---------------------------------------------------------------------------
# Truckdrive phone mode (disabled by default — legal compliance)
# ---------------------------------------------------------------------------
TRUCKDRIVE_PHONE_MODE: bool = os.environ.get("TRUCKDRIVE_PHONE_MODE", "false").lower() == "true"
TRUCKDRIVE_PHONE: Optional[str] = os.environ.get("TRUCKDRIVE_PHONE")

# ---------------------------------------------------------------------------
# Resume mode: skip (sku, site) pairs already in output
# ---------------------------------------------------------------------------
RESUME_MODE: bool = os.environ.get("RESUME_MODE", "true").lower() != "false"
