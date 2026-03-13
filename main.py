#!/usr/bin/env python3
"""
Main entry point for 2407.pl fitment crawler.
Uses Dolphin Anty antidect browser to bypass Cloudflare.

Usage:
    python main.py [options]

Options:
    --no-sitemap        Skip sitemap discovery
    --sections S [S ..] Only process listed sections (e.g. Фильтры Автосвет)
    --limit N           Stop after N products total (for testing)
    --limit-per-seed N  Stop after N products per seed URL (for testing)
    --output-dir DIR    Output directory (default: ./output)
    --csv               Also write CSV output
    --log-level LEVEL   DEBUG, INFO, WARNING (default: INFO)
"""
import argparse
import json
import logging
import os
import sys
import time
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config.settings import (
    SEED_URLS, BASE_URL, SITEMAP_URL, OUTPUT_DIR, OUTPUT_BASE_NAME,
    ROW_LIMIT, LOG_DIR, LOG_LEVEL,
    DOLPHIN_PROFILE_ID, REQUEST_DELAY_MIN, REQUEST_DELAY_MAX
)
from src.logger import setup_logging, Metrics
from src.crawler import CategoryCrawler, SitemapParser
from src.extractor import extract_product, looks_complete
from src.exporter import RotatingXlsxWriter, CsvWriter

logger = logging.getLogger(__name__)


def parse_args():
    parser = argparse.ArgumentParser(description="2407.pl fitment crawler (Dolphin Anty)")
    parser.add_argument("--no-sitemap", action="store_true")
    parser.add_argument("--sections", nargs="+", default=[],
                        help="Разделы для парсинга, например: --sections Фильтры Автосвет")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--limit-per-seed", type=int, default=0,
                        help="Stop after N products per seed URL (for testing)")
    parser.add_argument("--output-dir", default=OUTPUT_DIR)
    parser.add_argument("--csv", action="store_true")
    parser.add_argument("--log-level", default=LOG_LEVEL)
    return parser.parse_args()


def build_renderer():
    from src.renderer import AdaptiveRenderer
    return AdaptiveRenderer(
        profile_id=DOLPHIN_PROFILE_ID,
        delay_min=REQUEST_DELAY_MIN,
        delay_max=REQUEST_DELAY_MAX,
    )


def process_product(product_info: dict, renderer, metrics: Metrics) -> list:
    url = product_info["product_url"]
    source_ctx = {
        "source_section": product_info["source_section"],
        "source_subsection": product_info["source_subsection"],
        "source_url": product_info["source_url"],
    }

    t0 = time.time()
    html = renderer.fetch_html(url)
    elapsed = (time.time() - t0) * 1000

    if not html:
        metrics.record_error("fetch_failed")
        logger.warning(f"Failed to fetch: {url}")
        return []

    metrics.record_page("dolphin", elapsed)

    try:
        product_data = extract_product(url, html)
    except Exception as e:
        metrics.record_error("parse_error")
        logger.error(f"Parse error for {url}: {e}")
        return []

    has_required = all([
        product_data.product_id is not None,
        product_data.name,
        product_data.brand,
        product_data.part_number_display,
        product_data.price_pln is not None,
    ])
    has_fitment = any(r.make is not None for r in product_data.fitment_rows)
    metrics.record_product(has_fitment, has_required)

    rows = []
    for fitment in product_data.fitment_rows:
        row = {
            "source_section": source_ctx["source_section"],
            "source_subsection": source_ctx["source_subsection"],
            "source_url": source_ctx["source_url"],
            "breadcrumb_path": product_data.breadcrumb_path,
            "product_url": url,
            "product_id": product_data.product_id,
            "name": product_data.name,
            "brand": product_data.brand,
            "part_number_display": product_data.part_number_display,
            "part_number_normalized": product_data.part_number_normalized,
            "price_pln": product_data.price_pln,
            "vat_included": product_data.vat_included,
            "characteristics": product_data.characteristics,
            "fitment_make": fitment.make,
            "fitment_model": fitment.model,
            "fitment_raw_line": fitment.raw_line,
        }
        rows.append(row)

    metrics.record_rows(len(rows))
    logger.debug(f"Product {url}: {len(rows)} rows, fitment={has_fitment}")
    return rows


def main():
    args = parse_args()
    setup_logging(LOG_DIR, args.log_level)
    logger.info("2407.pl fitment crawler starting (Dolphin Anty mode)")

    renderer = build_renderer()

    # Setup Poland context
    logger.info("Setting up Poland/PLN context...")
    renderer.setup_poland()

    output_dir = args.output_dir
    xlsx_writer = RotatingXlsxWriter(output_dir, OUTPUT_BASE_NAME, ROW_LIMIT)
    csv_writer = CsvWriter(output_dir, OUTPUT_BASE_NAME, ROW_LIMIT) if args.csv else None
    metrics = Metrics()

    # Sitemap
    sitemap_urls = set()
    if not args.no_sitemap:
        logger.info("Trying sitemap...")
        try:
            parser = SitemapParser(renderer)
            prefixes = list(set(s["url"].replace("https://2407.pl", "") for s in SEED_URLS))
            sitemap_urls = parser.get_urls_for_sections(SITEMAP_URL, prefixes)
            logger.info(f"Sitemap: {len(sitemap_urls)} URLs")
        except Exception as e:
            logger.warning(f"Sitemap failed: {e}")

    crawler = CategoryCrawler(renderer, base_url=BASE_URL)

    # Deduplicate within source_url
    seen_per_source = {}  # source_url -> set of product_ids

    products_count = 0
    try:
        active_seeds = [s for s in SEED_URLS if not args.sections or s["section"] in args.sections]
        for seed in active_seeds:
            logger.info(f"Processing: {seed['section']} — {seed['url']}")
            seed_count = 0

            for product_info in crawler.crawl_seed(seed):
                src_url = product_info["source_url"]
                prod_url = product_info["product_url"]

                # Dedup within same source_url
                if src_url not in seen_per_source:
                    seen_per_source[src_url] = set()
                if prod_url in seen_per_source[src_url]:
                    continue
                seen_per_source[src_url].add(prod_url)

                rows = process_product(product_info, renderer, metrics)
                for row in rows:
                    xlsx_writer.write_row(row)
                    if csv_writer:
                        csv_writer.write_row(row)

                products_count += 1
                seed_count += 1

                if args.limit_per_seed and seed_count >= args.limit_per_seed:
                    logger.info(f"Reached per-seed limit ({args.limit_per_seed}) for: {seed['url']}")
                    break

                if args.limit and products_count >= args.limit:
                    logger.info(f"Reached limit: {args.limit}")
                    break

                if products_count % 50 == 0:
                    s = metrics.summary()
                    logger.info(f"Progress: {products_count} products, "
                                f"{s['rows_written']} rows, "
                                f"{s['pct_fitment_found']:.1f}% fitment")

            if args.limit and products_count >= args.limit:
                break

    except KeyboardInterrupt:
        logger.info("Interrupted")
    except Exception as e:
        logger.error(f"Fatal: {e}", exc_info=True)
    finally:
        xlsx_writer.finalize()
        if csv_writer:
            csv_writer.finalize()
        renderer.close()

        os.makedirs(LOG_DIR, exist_ok=True)
        report = os.path.join(LOG_DIR, f"report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json")
        metrics.save_report(report)
        metrics.print_summary()
        logger.info(f"Done. Output: {output_dir}")


if __name__ == "__main__":
    main()
