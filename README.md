# 2407.pl Fitment Crawler

A crawler/parser for extracting vehicle compatibility (fitment) data from 2407.pl/ru into Excel files.

---

## Project Structure

```
2407_crawler/
+-- main.py                  # Entry point
+-- requirements.txt         # Python dependencies
+-- Dockerfile               # Docker image
+-- docker-compose.yml       # Docker Compose config
+-- config/
|   +-- settings.py          # Seed URLs, request parameters
+-- src/
|   +-- crawler.py           # Category/listing/pagination crawler
|   +-- renderer.py          # HTTP requests + Playwright fallback
|   +-- extractor.py         # Product card and fitment parser
|   +-- exporter.py          # XLSX writer with 500k row rotation
|   +-- logger.py            # Logging and metrics
+-- output/                  # Output XLSX files (auto-created)
+-- logs/                    # Logs and reports (auto-created)
```

---

## Quick Start (Docker - recommended)

### 1. Install Docker and Docker Compose

- Docker Desktop: https://www.docker.com/products/docker-desktop/ (Windows/Mac)
- Or Docker Engine + Docker Compose (Linux)

### 2. Unzip and enter the folder

```bash
cd 2407_crawler
```

### 3. Build the Docker image

```bash
docker compose build
```

NOTE: First build takes 5-10 minutes (downloads Playwright + Chromium).

### 4. Run the crawler

```bash
docker compose run --rm crawler
```

Output files appear in ./output/:
  2407_fitment_PLN_0001.xlsx
  2407_fitment_PLN_0002.xlsx
  ... (new file every 500,000 rows)

### 5. Optional flags

```bash
# Also write CSV:
docker compose run --rm crawler --csv

# Skip sitemap, seed crawl only:
docker compose run --rm crawler --no-sitemap

# Test - first 100 products only:
docker compose run --rm crawler --limit 100

# Force Playwright for all requests:
docker compose run --rm crawler --playwright-only

# Initialize Poland/PLN context and exit:
docker compose run --rm crawler --setup-poland
```

---

## Quick Start (Python directly, no Docker)

Requirements: Python 3.10+

```bash
# Create virtual environment (recommended)
python -m venv venv

# Windows:
venv\Scripts\activate
# Linux/Mac:
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt
playwright install chromium
playwright install-deps chromium   # Linux only

# Run
python main.py
```

---

## Excel Output Schema (15 columns)

Column                  | Description
------------------------|-------------------------------------------------------------
source_section          | Root section (Avtozapchasti / Kuzovnye / Avtosvet / Ekster)
source_subsection       | Subsection from seed URL
source_url              | Listing URL where product was found
breadcrumb_path         | Breadcrumb path from product card
product_url             | Canonical product card URL
product_id              | Numeric product ID (from URL)
name                    | Product name (H1)
brand                   | Brand name
part_number_display     | Part number as shown on site
part_number_normalized  | UPPERCASE, spaces/dashes/dots removed
price_pln               | Price in PLN
vat_included            | True if price includes VAT
fitment_make            | Vehicle make (NULL if no fitment data)
fitment_model           | Vehicle model - one row per model (NULL if no fitment)
fitment_raw_line        | Original fitment line for audit

Each row = one product x one vehicle model.
Products with no fitment are written as one row with empty fitment_* fields.

---

## Configuration (config/settings.py + env vars)

Variable               | Default   | Description
-----------------------|-----------|----------------------------------
REQUEST_DELAY_MIN      | 1.0       | Min delay between requests (sec)
REQUEST_DELAY_MAX      | 3.0       | Max delay between requests (sec)
MAX_RETRIES            | 5         | Max retries on HTTP errors
ROW_LIMIT              | 500000    | Rows per Excel file
PLAYWRIGHT_HEADLESS    | true      | Headless browser mode
LOG_LEVEL              | INFO      | DEBUG / INFO / WARNING / ERROR

---

## Logs and Metrics

Logs:   ./logs/crawler_YYYYMMDD_HHMMSS.log  (JSON format)
Report: ./logs/report_YYYYMMDD_HHMMSS.json

Metrics:
  products_processed   - total products fetched
  pct_completeness     - % products with all required fields
  pct_fitment_found    - % products with vehicle compatibility data
  pages_per_min        - crawl speed (requests vs Playwright)
  errors               - error counts by type

---

## robots.txt Compliance

- /api/v1/* and /search/* are blocked in code (forbidden by robots.txt)
- Sitemap https://2407.pl/sitemap.xml used optionally for speed
- Configurable polite delay between all requests

---

## Troubleshooting

Problem: playwright command not found
Fix:     playwright install chromium

Problem: Empty output / no products found
Fix:     Run with --limit 5 and check ./logs/ for errors.
         Try --playwright-only to force full browser rendering.

Problem: Prices not in PLN
Fix:     Run --setup-poland first to re-initialize country context.

Problem: Very slow crawl
Fix:     Lower REQUEST_DELAY_MIN/MAX in config/settings.py.
