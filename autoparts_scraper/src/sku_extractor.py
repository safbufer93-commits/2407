"""
Read *.xlsx / *.xls files from a folder and extract SKU records.
Supports heuristic column detection: SKU, Артикул, Article, PartNumber, Код.
"""
from __future__ import annotations
import glob
import os
import logging
from dataclasses import dataclass, field
from typing import List, Optional, Iterator

import openpyxl
from openpyxl import load_workbook

from .normalizer import normalize_sku, is_valid_sku

log = logging.getLogger(__name__)

# Header aliases that identify the SKU column
_SKU_HEADERS = {
    "sku", "sku_raw", "артикул", "article", "partnumber", "part_number", "part number",
    "код товара", "код детали", "номер детали", "номер", "number", "oem",
}
# Header aliases for brand hint column
_BRAND_HEADERS = {
    "brand", "бренд", "производитель", "brand_hint",
}
# Header aliases for comment column
_COMMENT_HEADERS = {
    "comment", "комментарий", "примечание", "notes",
}
# Header aliases for required quantity
_QTY_HEADERS = {
    "qty", "qty_needed", "количество", "кол-во",
}


@dataclass
class SkuRecord:
    sku_raw: str
    sku_norm: str
    brand_hint: str = ""
    qty_needed: Optional[int] = None
    comment: str = ""
    source_file: str = ""
    source_row: int = 0


def _header_key(value) -> str:
    if value is None:
        return ""
    return str(value).strip().lower().replace("-", "_").replace(" ", "_")


def _find_col(headers: List[str], aliases: set) -> Optional[int]:
    for i, h in enumerate(headers):
        if _header_key(h) in aliases:
            return i
    return None


def _extract_from_sheet(ws, filepath: str) -> List[SkuRecord]:
    rows = list(ws.iter_rows(values_only=True))
    if not rows:
        return []

    # Try to detect header row (first row with recognisable column name)
    header_row_idx = 0
    col_sku: Optional[int] = None

    for ridx, row in enumerate(rows[:10]):
        headers = [str(c) if c is not None else "" for c in row]
        col = _find_col(headers, _SKU_HEADERS)
        if col is not None:
            header_row_idx = ridx
            col_sku = col
            break

    if col_sku is None:
        # Fallback: treat first column as SKU
        headers = [str(c) if c is not None else "" for c in rows[0]]
        col_sku = 0
        header_row_idx = 0
        log.warning("No recognised SKU column in %s; using column 0", filepath)

    headers = [str(c) if c is not None else "" for c in rows[header_row_idx]]
    col_brand = _find_col(headers, _BRAND_HEADERS)
    col_comment = _find_col(headers, _COMMENT_HEADERS)
    col_qty = _find_col(headers, _QTY_HEADERS)

    records: List[SkuRecord] = []
    for ridx, row in enumerate(rows[header_row_idx + 1:], start=header_row_idx + 2):
        if col_sku >= len(row):
            continue
        raw_val = row[col_sku]
        if raw_val is None:
            continue
        sku_raw = str(raw_val).strip()
        if not sku_raw:
            continue
        sku_norm = normalize_sku(sku_raw)
        if not is_valid_sku(sku_norm):
            log.debug("Skipping invalid SKU '%s' at row %d in %s", sku_raw, ridx, filepath)
            continue

        brand = ""
        if col_brand is not None and col_brand < len(row) and row[col_brand]:
            brand = str(row[col_brand]).strip()

        comment = ""
        if col_comment is not None and col_comment < len(row) and row[col_comment]:
            comment = str(row[col_comment]).strip()

        qty = None
        if col_qty is not None and col_qty < len(row) and row[col_qty]:
            try:
                qty = int(row[col_qty])
            except (ValueError, TypeError):
                pass

        records.append(
            SkuRecord(
                sku_raw=sku_raw,
                sku_norm=sku_norm,
                brand_hint=brand,
                qty_needed=qty,
                comment=comment,
                source_file=os.path.basename(filepath),
                source_row=ridx,
            )
        )
    return records


def load_skus_from_folder(folder: str, recursive: bool = False) -> List[SkuRecord]:
    """
    Scan folder for *.xlsx and *.xls files, extract all SKU records.
    Deduplicates by sku_norm (keeps first occurrence).
    """
    pattern_xlsx = os.path.join(folder, "**/*.xlsx") if recursive else os.path.join(folder, "*.xlsx")
    pattern_xls = os.path.join(folder, "**/*.xls") if recursive else os.path.join(folder, "*.xls")

    files = sorted(
        glob.glob(pattern_xlsx, recursive=recursive) +
        glob.glob(pattern_xls, recursive=recursive)
    )

    if not files:
        log.warning("No Excel files found in %s", folder)
        return []

    seen: set[str] = set()
    all_records: List[SkuRecord] = []

    for filepath in files:
        log.info("Loading SKUs from %s", filepath)
        try:
            wb = load_workbook(filepath, read_only=True, data_only=True)
            for ws in wb.worksheets:
                records = _extract_from_sheet(ws, filepath)
                for rec in records:
                    if rec.sku_norm not in seen:
                        seen.add(rec.sku_norm)
                        all_records.append(rec)
                    else:
                        log.debug("Duplicate SKU '%s' skipped (from %s)", rec.sku_norm, filepath)
            wb.close()
        except Exception as exc:
            log.error("Failed to load %s: %s", filepath, exc)

    log.info("Total unique SKUs loaded: %d", len(all_records))
    return all_records
