"""
Logging and metrics module.
"""
import json
import logging
import os
import time
from collections import defaultdict
from datetime import datetime
from typing import Optional


def setup_logging(log_dir: str = "./logs", level: str = "INFO") -> logging.Logger:
    """Setup structured JSON logging."""
    os.makedirs(log_dir, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_file = os.path.join(log_dir, f"crawler_{timestamp}.log")

    # Root logger
    root = logging.getLogger()
    root.setLevel(getattr(logging, level.upper(), logging.INFO))

    # File handler (JSON)
    fh = logging.FileHandler(log_file, encoding="utf-8")
    fh.setFormatter(JsonFormatter())
    root.addHandler(fh)

    # Console handler
    ch = logging.StreamHandler()
    ch.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s"))
    ch.setLevel(logging.INFO)
    root.addHandler(ch)

    return logging.getLogger("crawler")


class JsonFormatter(logging.Formatter):
    def format(self, record):
        data = {
            "event_time": datetime.utcnow().isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if hasattr(record, "extra"):
            data.update(record.extra)
        if record.exc_info:
            data["exception"] = self.formatException(record.exc_info)
        return json.dumps(data, ensure_ascii=False)


class Metrics:
    """Runtime metrics collection."""

    def __init__(self):
        self.start_time = time.time()
        self.products_processed = 0
        self.products_with_fitment = 0
        self.rows_written = 0
        self.errors = defaultdict(int)
        self.pages_html = 0
        self.pages_playwright = 0
        self.page_times_html = []
        self.page_times_playwright = []
        self.fields_ok = 0  # products with all required fields

    def record_page(self, mode: str, elapsed_ms: float):
        if mode == "requests":
            self.pages_html += 1
            self.page_times_html.append(elapsed_ms)
        else:
            self.pages_playwright += 1
            self.page_times_playwright.append(elapsed_ms)

    def record_product(self, has_fitment: bool, has_required_fields: bool):
        self.products_processed += 1
        if has_fitment:
            self.products_with_fitment += 1
        if has_required_fields:
            self.fields_ok += 1

    def record_error(self, error_type: str):
        self.errors[error_type] += 1

    def record_rows(self, count: int):
        self.rows_written += count

    def summary(self) -> dict:
        elapsed = time.time() - self.start_time
        n_html = len(self.page_times_html) or 1
        n_pw = len(self.page_times_playwright) or 1
        avg_html_ms = sum(self.page_times_html) / n_html
        avg_pw_ms = sum(self.page_times_playwright) / n_pw
        total_pages = self.pages_html + self.pages_playwright

        pct_complete = (self.fields_ok / max(self.products_processed, 1)) * 100
        pct_fitment = (self.products_with_fitment / max(self.products_processed, 1)) * 100

        return {
            "elapsed_seconds": round(elapsed, 1),
            "products_processed": self.products_processed,
            "products_with_fitment": self.products_with_fitment,
            "rows_written": self.rows_written,
            "pct_completeness": round(pct_complete, 1),
            "pct_fitment_found": round(pct_fitment, 1),
            "pages_html": self.pages_html,
            "pages_playwright": self.pages_playwright,
            "pages_per_min_html": round(self.pages_html / max(elapsed / 60, 0.01), 1),
            "pages_per_min_playwright": round(self.pages_playwright / max(elapsed / 60, 0.01), 1),
            "avg_html_ms": round(avg_html_ms, 1),
            "avg_pw_ms": round(avg_pw_ms, 1),
            "errors": dict(self.errors),
        }

    def print_summary(self):
        s = self.summary()
        print("\n" + "=" * 60)
        print("CRAWL SUMMARY")
        print("=" * 60)
        for k, v in s.items():
            print(f"  {k}: {v}")
        print("=" * 60 + "\n")

    def save_report(self, path: str):
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.summary(), f, ensure_ascii=False, indent=2)
