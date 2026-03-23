# Auto Parts Price Scraper

Automated batch web scraper for Russian auto parts websites.
Reads SKU numbers from Excel files, searches each site, and outputs a unified `results.xlsx`.

## Quick Start

```bash
# Install dependencies
pip install -r requirements.txt
playwright install chromium

# Create sample input files
python create_sample_input.py

# Run (all sites, default settings)
python main.py

# Run specific sites only
python main.py --sites autopiter armtek

# Test with 3 SKUs, visible browser
python main.py --limit 3 --headless false

# Resume interrupted run (default: on)
python main.py --no-resume   # disable resume
```

## Output

`output/results_<run_id>.xlsx` with sheets:

| Sheet | Description |
|-------|-------------|
| `results` | All parsed offers: site, header-brand, name, SKU, price, availability |
| `errors` | Blocked/failed tasks with screenshot and HTML snapshot paths |
| `runs` | Run summary metrics |

### Results columns

| Column | Description |
|--------|-------------|
| `site` | Base URL of the source site |
| `header-brand` | Brand/manufacturer |
| `name` | Product name |
| `SKU` | Article number |
| `price` | Price in RUB (numeric) |
| `availability` | `QTY=64`, `IN_STOCK`, `REQUIRES_PHONE`, etc. |

## Input Excel Format

Place `*.xlsx` or `*.xls` files in `./input/`. Column names are auto-detected.

Supported header names (case-insensitive):
- SKU column: `SKU`, `Артикул`, `Article`, `PartNumber`, `Номер детали`
- Brand hint: `brand`, `Бренд`, `Производитель`
- Quantity needed: `qty`, `Количество`
- Comment: `comment`, `Примечание`

Minimum: one column with part numbers.

## Target Sites

| Site | ID | Status |
|------|----|--------|
| autopiter.ru | `autopiter` | Full support (card + pagination) |
| armtek.ru | `armtek` | Full support (search + card + pagination) |
| tatparts.ru | `tatparts` | Full support (2-step: SKU → manufacturer → offers) |
| truckdrive.ru | `truckdrive` | Supported; phone-gated prices → `REQUIRES_PHONE` |
| sale-parts.ru | `saleparts` | Captcha protected → `BLOCKED_CAPTCHA` |
| market.tmtr.ru | `market_tmtr` | SPA + login required → `LOGIN_REQUIRED` |
| tsmavto.ru | `tsmavto` | Supported via `/search?pcode=SKU` |

## Environment Variables

```bash
# Browser
HEADLESS=false                     # Show browser window

# Concurrency / timeouts
GLOBAL_CONCURRENCY=4
NAVIGATION_TIMEOUT_MS=45000
SELECTOR_TIMEOUT_MS=15000
MAX_PAGES=5                        # 0 = unlimited
MAX_ATTEMPTS=3

# market.tmtr.ru credentials (optional)
MARKET_TMTR_LOGIN=user@example.com
MARKET_TMTR_PASSWORD=secret

# truckdrive.ru phone mode (disabled by default — legal compliance)
# Only enable with explicit corporate authorisation
TRUCKDRIVE_PHONE_MODE=false
TRUCKDRIVE_PHONE=+79001234567
```

## Availability Status Glossary

| Value | Meaning |
|-------|---------|
| `QTY=N` | N units in stock |
| `QTY=N;ETA=...` | N units, estimated ship date |
| `IN_STOCK` | Available, quantity unknown |
| `OUT_OF_STOCK` | Not available |
| `PREORDER` | Available on order |
| `REQUIRES_PHONE` | Price visible only after phone input (truckdrive) |
| `REQUIRES_CAPTCHA` | Site blocked by captcha (saleparts) |
| `REQUIRES_LOGIN` | Login required (market.tmtr) |
| `UNKNOWN` | Could not determine status |

## Architecture

```
main.py
├── src/sku_extractor.py     — Read Excel folder, extract & deduplicate SKUs
├── src/normalizer.py        — Normalise SKU / price / availability / brand
├── src/queue.py             — Async task runner, global + per-domain semaphores, retry backoff
├── src/exporter.py          — Write results.xlsx (results / errors / runs sheets)
├── src/logger.py            — Structured JSON logger with correlation fields
└── src/adapters/
    ├── base.py              — BaseSiteAdapter, OfferParsed, ScrapeResult, TaskStatus
    ├── autopiter.py
    ├── armtek.py
    ├── tatparts.py
    ├── truckdrive.py
    ├── saleparts.py
    ├── market_tmtr.py
    └── tsmavto.py
```

## Legal & Compliance Notes

- The scraper **never** automatically enters personal data (phone numbers, emails).
- Truckdrive phone mode is disabled by default. Enable only with corporate authorisation and documented legal basis (152-ФЗ).
- Sale-parts captcha is not bypassed; task is marked `BLOCKED_CAPTCHA`.
- Market.tmtr login uses only credentials you provide via environment variables.
- Robots.txt is respected ethically; per-domain concurrency is limited to reduce server load.

## Docker

```bash
docker build -t autoparts-scraper .
docker run --rm \
  -v $(pwd)/input:/app/input \
  -v $(pwd)/output:/app/output \
  autoparts-scraper python main.py
```
