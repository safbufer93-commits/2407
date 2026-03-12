"""
Configuration for 2407.pl fitment crawler.
All scalar values can be overridden via environment variables.
"""
import os

SEED_URLS = [
    # Автозапчасти
    {"section": "Автозапчасти", "url": "https://2407.pl/ru/avtozapchasti/"},
    {"section": "Автозапчасти", "url": "https://2407.pl/ru/uzly-detali-dvigatelja/"},
    {"section": "Автозапчасти", "url": "https://2407.pl/ru/detali-transmissii/"},
    {"section": "Автозапчасти", "url": "https://2407.pl/ru/detali-tormoznoj-sistemy/"},
    {"section": "Автозапчасти", "url": "https://2407.pl/ru/detali-podveski/"},
    {"section": "Автозапчасти", "url": "https://2407.pl/ru/filtry/"},
    {"section": "Автозапчасти", "url": "https://2407.pl/ru/ohlazhdenie-dvigatelja/"},
    {"section": "Автозапчасти", "url": "https://2407.pl/ru/rulevoe-upravlenie/"},
    {"section": "Автозапчасти", "url": "https://2407.pl/ru/sistema-zazhiganija/"},
    {"section": "Автозапчасти", "url": "https://2407.pl/ru/datchiki/"},
    {"section": "Автозапчасти", "url": "https://2407.pl/ru/elektrika/"},
    {"section": "Автозапчасти", "url": "https://2407.pl/ru/sistema-otoplenija-kondicionirovanija/"},
    {"section": "Автозапчасти", "url": "https://2407.pl/ru/toplivnaja-sistema/"},
    {"section": "Автозапчасти", "url": "https://2407.pl/ru/vyhlopnaja-sistema/"},
    {"section": "Автозапчасти", "url": "https://2407.pl/ru/sistema-podgotovki-podachi-vozduha/"},
    {"section": "Автозапчасти", "url": "https://2407.pl/ru/generatory-startery-ih-komponenty/"},
    {"section": "Автозапчасти", "url": "https://2407.pl/ru/gazoballonnoe-oborudovanie/"},
    # Кузовные запчасти
    {"section": "Кузовные запчасти", "url": "https://2407.pl/ru/kuzovnye-zapchasti/"},
    # Автосвет
    {"section": "Автосвет", "url": "https://2407.pl/ru/fary-osnovnogo-sveta/"},
    {"section": "Автосвет", "url": "https://2407.pl/ru/fonari-zadnie-zadnie-gabarity/"},
    {"section": "Автосвет", "url": "https://2407.pl/ru/korrektory-far/"},
    # Экстерьер
    {"section": "Экстерьер", "url": "https://2407.pl/ru/eksterer/"},
]

BASE_URL = "https://2407.pl"
SITEMAP_URL = "https://2407.pl/sitemap.xml"

# Output settings
OUTPUT_DIR = os.environ.get("OUTPUT_DIR", "./output")
OUTPUT_BASE_NAME = "2407_fitment_PLN"
ROW_LIMIT = int(os.environ.get("ROW_LIMIT", "500000"))

# Request settings
REQUEST_DELAY_MIN = float(os.environ.get("REQUEST_DELAY_MIN", "1.0"))
REQUEST_DELAY_MAX = float(os.environ.get("REQUEST_DELAY_MAX", "3.0"))
MAX_RETRIES = int(os.environ.get("MAX_RETRIES", "5"))
RETRY_BACKOFF_BASE = float(os.environ.get("RETRY_BACKOFF_BASE", "2.0"))
RETRY_BACKOFF_MAX = float(os.environ.get("RETRY_BACKOFF_MAX", "60.0"))

# Playwright settings
PLAYWRIGHT_HEADLESS = os.environ.get("PLAYWRIGHT_HEADLESS", "true").lower() == "true"
PLAYWRIGHT_TIMEOUT = int(os.environ.get("PLAYWRIGHT_TIMEOUT", "30000"))
STORAGE_STATE_PATH = os.environ.get("STORAGE_STATE_PATH", "./config/storage_state_poland.json")

# Logging
LOG_DIR = os.environ.get("LOG_DIR", "./logs")
LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO")

# Forbidden paths (from robots.txt)
FORBIDDEN_PREFIXES = ["/api/v1/", "/search/"]

# Items per page (try to set max)
ITEMS_PER_PAGE = 50
# Dolphin Anty
DOLPHIN_PROFILE_ID = os.environ.get('DOLPHIN_PROFILE_ID', '759890630')
