#!/usr/bin/env python3
"""
Auto Parts Price Scraper
========================

Reads Excel files with SKU numbers, searches each SKU on configured Russian
auto parts websites, and writes a unified results.xlsx.

Usage:
    python main.py [options]

Options:
    --input-dir DIR        Folder with input *.xlsx / *.xls files (default: ./input)
    --output-dir DIR       Output folder (default: ./output)
    --sites SITE [SITE ..]  Run only these site IDs (e.g. autopiter armtek)
    --limit N              Process only first N SKUs (for testing)
    --no-resume            Disable resume mode (re-scrape everything)
    --headless false       Show browser window (for debugging / captcha solving)
    --log-level LEVEL      DEBUG, INFO, WARNING (default: INFO)
    --max-pages N          Max pages per offer table (default: 5, 0=unlimited)
    --max-manufacturers N  Max manufacturers per SKU on multi-brand results (default: 3)
"""
from __future__ import annotations
import argparse
import asyncio
import os
import sys
import uuid
from datetime import datetime, timezone
from typing import List, Optional

# Ensure project root on path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import config as cfg
from src.logger import setup_logging, CorrelatedLogger, Metrics
from src.sku_extractor import load_skus_from_folder
from src.queue import ScrapeTask, TaskRunner
from src.exporter import export_results, load_completed_keys
from src.adapters.autopiter import AutopiterAdapter
from src.adapters.armtek import ArmtekAdapter
from src.adapters.tatparts import TatpartsAdapter
from src.adapters.truckdrive import TruckdriveAdapter
from src.adapters.saleparts import SalepartsAdapter
from src.adapters.market_tmtr import MarketTmtrAdapter
from src.adapters.tsmavto import TsmavtoAdapter


ADAPTER_REGISTRY = {
    "autopiter": AutopiterAdapter(),
    "armtek": ArmtekAdapter(),
    "tatparts": TatpartsAdapter(),
    "truckdrive": TruckdriveAdapter(),
    "saleparts": SalepartsAdapter(),
    "market_tmtr": MarketTmtrAdapter(),
    "tsmavto": TsmavtoAdapter(),
}


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Auto Parts Price Scraper")
    p.add_argument("--input-dir", default=cfg.INPUT_DIR)
    p.add_argument("--output-dir", default=cfg.OUTPUT_DIR)
    p.add_argument("--sites", nargs="*", default=None, help="Limit to specific site IDs")
    p.add_argument("--limit", type=int, default=0, help="Max SKUs to process (0=all)")
    p.add_argument("--no-resume", action="store_true", help="Disable resume mode")
    p.add_argument("--headless", default="true", help="true/false")
    p.add_argument("--log-level", default="INFO")
    p.add_argument("--max-pages", type=int, default=cfg.MAX_PAGES)
    p.add_argument("--max-manufacturers", type=int, default=cfg.MAX_MANUFACTURERS)
    return p.parse_args()


def build_tasks(
    skus,
    sites: List[dict],
    run_id: str,
    completed_keys: set,
    resume: bool,
) -> List[ScrapeTask]:
    tasks = []
    for sku in skus:
        for site in sites:
            if not site.get("enabled", True):
                continue
            key = (sku.sku_norm, site["id"])
            if resume and key in completed_keys:
                continue
            tasks.append(ScrapeTask(
                run_id=run_id,
                site_id=site["id"],
                site_base_url=site["base_url"],
                sku_raw=sku.sku_raw,
                sku_norm=sku.sku_norm,
                brand_hint=sku.brand_hint,
            ))
    return tasks


async def main_async(args: argparse.Namespace) -> None:
    run_id = str(uuid.uuid4())[:8]
    os.makedirs(cfg.LOG_DIR, exist_ok=True)
    os.makedirs(args.output_dir, exist_ok=True)
    os.makedirs(cfg.ARTIFACTS_DIR, exist_ok=True)

    setup_logging(cfg.LOG_DIR, run_id, level=args.log_level)
    log = CorrelatedLogger("runner", run_id=run_id)
    metrics = Metrics()

    log.info(f"Run started: run_id={run_id}")
    log.info(f"Input dir: {args.input_dir}")

    # Override config from CLI
    cfg.HEADLESS = args.headless.lower() != "false"
    cfg.MAX_PAGES = args.max_pages
    cfg.MAX_MANUFACTURERS = args.max_manufacturers

    # Load SKUs
    skus = load_skus_from_folder(args.input_dir)
    if not skus:
        log.warning("No SKUs found. Place Excel files in the input directory.")
        return

    if args.limit:
        skus = skus[:args.limit]

    log.info(f"Loaded {len(skus)} unique SKUs")

    # Filter sites
    sites = cfg.SITES
    if args.sites:
        sites = [s for s in sites if s["id"] in args.sites]
    log.info(f"Active sites: {[s['id'] for s in sites]}")

    # Resume mode
    output_path = os.path.join(args.output_dir, f"results_{run_id}.xlsx")
    resume_output = os.path.join(args.output_dir, "results_latest.xlsx")
    completed_keys: set = set()
    resume = cfg.RESUME_MODE and not args.no_resume
    if resume and os.path.exists(resume_output):
        completed_keys = load_completed_keys(resume_output)
        log.info(f"Resume mode: {len(completed_keys)} (sku, site) pairs already done")

    # Build task list
    tasks = build_tasks(skus, sites, run_id, completed_keys, resume)
    log.info(f"Total tasks to run: {len(tasks)}")

    if not tasks:
        log.info("Nothing to do (all tasks already completed in resume mode).")
        return

    # Run
    runner = TaskRunner(
        adapters=ADAPTER_REGISTRY,
        config=cfg,
        metrics=metrics,
        log=log,
        artifacts_dir=cfg.ARTIFACTS_DIR,
    )

    completed_tasks = await runner.run_all(tasks)

    # Export
    export_results(completed_tasks, run_id, metrics, output_path)

    # Also write/overwrite "latest" symlink-style copy
    import shutil
    shutil.copy2(output_path, resume_output)

    log.info(f"Results written to: {output_path}")
    log.info(f"Run summary: {metrics.as_dict()}")

    # Print summary to stdout
    print("\n" + "=" * 60)
    print(f"Run ID     : {run_id}")
    print(f"Output     : {output_path}")
    m = metrics.as_dict()
    print(f"Tasks      : {m['tasks_total']} total")
    print(f"  Success  : {m['tasks_success']}")
    print(f"  Not found: {m['tasks_not_found']}")
    print(f"  Blocked  : {m['tasks_blocked']}")
    print(f"  Timeout  : {m['tasks_timeout']}")
    print(f"  Errors   : {m['tasks_error']}")
    print(f"Offers     : {m['offers_parsed_total']}")
    print(f"Avg time   : {m['avg_task_duration_ms']} ms/task")
    print("=" * 60)


def main() -> None:
    args = parse_args()
    asyncio.run(main_async(args))


if __name__ == "__main__":
    main()
