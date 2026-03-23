"""
Excel exporter: writes results.xlsx with sheets:
  - results  (main offers data)
  - errors   (blocked/failed tasks)
  - runs     (run summary)
"""
from __future__ import annotations
import os
from datetime import datetime, timezone
from typing import List

import openpyxl
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment
from openpyxl.utils import get_column_letter

from .adapters.base import OfferParsed, TaskStatus
from .queue import ScrapeTask
from .logger import Metrics


_HEADER_FILL = PatternFill("solid", fgColor="1F497D")
_HEADER_FONT = Font(bold=True, color="FFFFFF")
_ERROR_FILL = PatternFill("solid", fgColor="FFCCCC")


def _write_header(ws, cols: List[str]) -> None:
    for ci, col in enumerate(cols, 1):
        cell = ws.cell(row=1, column=ci, value=col)
        cell.font = _HEADER_FONT
        cell.fill = _HEADER_FILL
        cell.alignment = Alignment(horizontal="center")


def _autofit(ws) -> None:
    for col in ws.columns:
        max_len = 0
        col_letter = get_column_letter(col[0].column)
        for cell in col:
            if cell.value:
                max_len = max(max_len, len(str(cell.value)))
        ws.column_dimensions[col_letter].width = min(max(max_len + 2, 10), 60)


def export_results(
    tasks: List[ScrapeTask],
    run_id: str,
    metrics: Metrics,
    output_path: str,
) -> None:
    wb = Workbook()
    wb.remove(wb.active)  # remove default sheet

    # ---- results sheet ----
    ws_res = wb.create_sheet("results")
    results_cols = [
        "site", "header-brand", "name", "SKU", "price", "availability",
        "sku_queried", "match_type", "source_url", "page_no", "scraped_at",
    ]
    _write_header(ws_res, results_cols)

    row_idx = 2
    for task in tasks:
        if not task.result or not task.result.offers:
            continue
        for offer in task.result.offers:
            ws_res.cell(row=row_idx, column=1, value=offer.site)
            ws_res.cell(row=row_idx, column=2, value=offer.header_brand)
            ws_res.cell(row=row_idx, column=3, value=offer.name)
            ws_res.cell(row=row_idx, column=4, value=offer.sku)
            ws_res.cell(row=row_idx, column=5, value=offer.price)
            ws_res.cell(row=row_idx, column=6, value=offer.availability)
            ws_res.cell(row=row_idx, column=7, value=offer.sku_queried)
            ws_res.cell(row=row_idx, column=8, value=str(offer.match_type.value) if offer.match_type else "")
            ws_res.cell(row=row_idx, column=9, value=offer.source_url)
            ws_res.cell(row=row_idx, column=10, value=offer.page_no)
            ws_res.cell(row=row_idx, column=11, value=offer.scraped_at)
            row_idx += 1
    _autofit(ws_res)
    ws_res.freeze_panes = "A2"

    # ---- errors sheet ----
    ws_err = wb.create_sheet("errors")
    error_cols = [
        "run_id", "site", "sku", "attempt", "status",
        "error_message", "http_status", "screenshot_path", "html_path", "duration_ms",
    ]
    _write_header(ws_err, error_cols)

    err_idx = 2
    for task in tasks:
        if task.status in (TaskStatus.COMPLETED,):
            continue
        r = task.result
        ws_err.cell(row=err_idx, column=1, value=task.run_id)
        ws_err.cell(row=err_idx, column=2, value=task.site_id)
        ws_err.cell(row=err_idx, column=3, value=task.sku_norm)
        ws_err.cell(row=err_idx, column=4, value=task.attempt)
        ws_err.cell(row=err_idx, column=5, value=str(task.status.value))
        ws_err.cell(row=err_idx, column=6, value=(r.error_message[:500] if r and r.error_message else ""))
        ws_err.cell(row=err_idx, column=7, value=(r.http_status if r else None))
        ws_err.cell(row=err_idx, column=8, value=(r.screenshot_path if r else ""))
        ws_err.cell(row=err_idx, column=9, value=(r.html_path if r else ""))
        ws_err.cell(row=err_idx, column=10, value=round(task.duration_ms, 1))

        # Colour error rows
        for ci in range(1, len(error_cols) + 1):
            ws_err.cell(row=err_idx, column=ci).fill = _ERROR_FILL
        err_idx += 1
    _autofit(ws_err)

    # ---- runs/summary sheet ----
    ws_run = wb.create_sheet("runs")
    run_cols = ["metric", "value"]
    _write_header(ws_run, run_cols)
    summary = {
        "run_id": run_id,
        "generated_at": datetime.now(tz=timezone.utc).isoformat(),
        **metrics.as_dict(),
    }
    for ri, (k, v) in enumerate(summary.items(), start=2):
        ws_run.cell(row=ri, column=1, value=k)
        ws_run.cell(row=ri, column=2, value=str(v))
    _autofit(ws_run)

    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    wb.save(output_path)


def load_completed_keys(output_path: str) -> set[tuple[str, str]]:
    """
    Resume mode: read existing output Excel and return set of (sku_norm, site_id)
    pairs that already have COMPLETED results (non-empty price or offers).
    """
    keys: set[tuple[str, str]] = set()
    if not os.path.exists(output_path):
        return keys
    try:
        wb = openpyxl.load_workbook(output_path, read_only=True, data_only=True)
        if "results" not in wb.sheetnames:
            return keys
        ws = wb["results"]
        # Header row: site=col1, SKU=col4, sku_queried=col7
        for row in ws.iter_rows(min_row=2, values_only=True):
            if row and len(row) >= 7:
                site_url = str(row[0] or "").strip()
                sku_norm = str(row[3] or "").strip()
                if site_url and sku_norm:
                    # Map site_url to site_id (best-effort)
                    site_id = site_url.rstrip("/").split("//")[-1].split("/")[0].replace("www.", "")
                    keys.add((sku_norm, site_id))
        wb.close()
    except Exception:
        pass
    return keys
