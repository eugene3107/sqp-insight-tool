"""Read raw Amazon SQP exports (xlsx / csv, ASIN-level or brand-level) into one canonical long table.

Handles the layout drift seen across exports:
  * header row on row 2 or 3 (row 1 holds `KEY=["value"]` metadata)
  * `ASIN=` vs `ASIN or Product=` vs `Brand=` metadata keys
  * `Impressions: ASIN Count` vs `Impressions: Brand Count` column families
  * `Same-Day` vs `Same Day` shipping labels
"""
from __future__ import annotations

import csv
import re
from pathlib import Path

import openpyxl
import pandas as pd

# Canonical column names. Headers are normalised (lowercase, ASIN/Brand -> "own",
# hyphens -> spaces, collapsed whitespace) before lookup.
HEADER_MAP = {
    "search query": "query",
    "search query score": "sqp_score",
    "search query volume": "sqp_volume",
    "impressions: total count": "impr_total",
    "impressions: own count": "impr_own",
    "impressions: own share %": "impr_own_share_amz",
    "clicks: total count": "clicks_total",
    "clicks: click rate %": "clicks_rate_amz",
    "clicks: own count": "clicks_own",
    "clicks: own share %": "clicks_own_share_amz",
    "clicks: price (median)": "price_click_mkt",
    "clicks: own price (median)": "price_click_own",
    "clicks: same day shipping speed": "ship_sd_clicks",
    "clicks: 1d shipping speed": "ship_1d_clicks",
    "clicks: 2d shipping speed": "ship_2d_clicks",
    "cart adds: total count": "carts_total",
    "cart adds: cart add rate %": "carts_rate_amz",
    "cart adds: own count": "carts_own",
    "cart adds: own share %": "carts_own_share_amz",
    "cart adds: price (median)": "price_cart_mkt",
    "cart adds: own price (median)": "price_cart_own",
    "cart adds: same day shipping speed": "ship_sd_carts",
    "cart adds: 1d shipping speed": "ship_1d_carts",
    "cart adds: 2d shipping speed": "ship_2d_carts",
    "purchases: total count": "purch_total",
    "purchases: purchase rate %": "purch_rate_amz",
    "purchases: own count": "purch_own",
    "purchases: own share %": "purch_own_share_amz",
    "purchases: price (median)": "price_purch_mkt",
    "purchases: own price (median)": "price_purch_own",
    "purchases: same day shipping speed": "ship_sd_purch",
    "purchases: 1d shipping speed": "ship_1d_purch",
    "purchases: 2d shipping speed": "ship_2d_purch",
    "reporting date": "report_date",
}

# Manually-added helper columns that we recompute; dropped if present in the source.
DERIVED_HEADERS = {"market ctr", "brand ctr", "market cvr", "brand cvr"}

COUNT_COLS = [
    "sqp_score", "sqp_volume",
    "impr_total", "impr_own", "clicks_total", "clicks_own",
    "carts_total", "carts_own", "purch_total", "purch_own",
    "ship_sd_clicks", "ship_1d_clicks", "ship_2d_clicks",
    "ship_sd_carts", "ship_1d_carts", "ship_2d_carts",
    "ship_sd_purch", "ship_1d_purch", "ship_2d_purch",
]
PRICE_COLS = [
    "price_click_mkt", "price_click_own", "price_cart_mkt",
    "price_cart_own", "price_purch_mkt", "price_purch_own",
]
META_COLS = ["source", "scope", "entity_id", "period_type", "period_label", "period_end"]

_META_RE = re.compile(r'^\s*(?P<key>[^=]+?)\s*=\s*\[?"?(?P<val>[^"\]]*)"?\]?\s*$')


def _norm_header(h: object) -> str:
    s = str(h).strip().lower()
    s = s.replace("-", " ")
    s = re.sub(r"\basin\b", "own", s)
    s = re.sub(r"\bbrand\b", "own", s)
    return re.sub(r"\s+", " ", s)


def _parse_meta(cells: list[object]) -> dict[str, str]:
    meta: dict[str, str] = {}
    for c in cells:
        if c is None:
            continue
        m = _META_RE.match(str(c))
        if m:
            meta[m.group("key").strip().lower()] = m.group("val").strip()
    return meta


def _find_header_row(rows: list[list[object]]) -> int | None:
    for i, row in enumerate(rows[:10]):
        if row and str(row[0]).strip().lower() == "search query":
            return i
    return None


def _frame_from_rows(rows: list[list[object]], source: str, fallback_entity: str) -> pd.DataFrame | None:
    hdr_idx = _find_header_row(rows)
    if hdr_idx is None:
        return None
    meta = _parse_meta(rows[0]) if hdr_idx > 0 else {}
    raw_headers = rows[hdr_idx]

    hdr_l = [str(h).lower() for h in raw_headers if h is not None]
    scope = "asin" if any("asin count" in h for h in hdr_l) else (
        "brand" if any("brand count" in h for h in hdr_l) else "unknown")
    keep: dict[int, str] = {}
    for i, h in enumerate(raw_headers):
        if h is None:
            continue
        n = _norm_header(h)
        if n in DERIVED_HEADERS:
            continue
        if n in HEADER_MAP:
            keep[i] = HEADER_MAP[n]

    data = []
    for row in rows[hdr_idx + 1:]:
        if not row or row[0] is None or str(row[0]).strip() == "":
            continue
        data.append({name: (row[i] if i < len(row) else None) for i, name in keep.items()})
    if not data:
        return None
    df = pd.DataFrame(data)

    # --- metadata ---
    entity = (
        meta.get("asin") or meta.get("asin or product") or meta.get("brand")
        or meta.get("brands") or fallback_entity
    )
    period_type = meta.get("reporting range", "").lower() or "unknown"
    year = meta.get("select year", "")
    sub = meta.get("select month") or meta.get("select quarter") or meta.get("select week") or ""
    if period_type == "quarterly" and sub:
        label = f"{year} Q{sub}"
    elif sub:
        label = f"{year} {sub}".strip()
    else:
        label = year or "unknown"

    df["source"] = source
    df["scope"] = scope
    df["entity_id"] = entity
    df["period_type"] = period_type
    df["period_label"] = label
    if "report_date" in df:
        df["period_end"] = pd.to_datetime(df["report_date"], errors="coerce")
    else:
        df["period_end"] = pd.NaT
    return df


def _coerce(df: pd.DataFrame) -> pd.DataFrame:
    def _num(col: pd.Series) -> pd.Series:
        # exports may quote numbers with thousands separators / currency symbols
        cleaned = col.astype(str).str.replace(r"[,\s$£€]", "", regex=True)
        return pd.to_numeric(cleaned, errors="coerce")

    for c in COUNT_COLS:
        if c not in df:
            df[c] = 0.0
        df[c] = _num(df[c]).fillna(0.0)
    for c in PRICE_COLS:
        if c not in df:
            df[c] = float("nan")
        df[c] = _num(df[c])
    df["query"] = df["query"].astype(str).str.strip().str.lower()
    ordered = ["query"] + META_COLS + COUNT_COLS + PRICE_COLS
    rest = [c for c in df.columns if c not in ordered]
    return df[ordered + rest]


def load_sqp(path: str | Path, sheets: list[str] | None = None) -> pd.DataFrame:
    """Load one SQP export (xlsx with any number of sheets, or a csv) into the canonical long table."""
    path = Path(path)
    frames: list[pd.DataFrame] = []
    if path.suffix.lower() in {".xlsx", ".xlsm"}:
        wb = openpyxl.load_workbook(path, data_only=True, read_only=True)
        for ws in wb.worksheets:
            if sheets and ws.title not in sheets:
                continue
            rows = [list(r) for r in ws.iter_rows(values_only=True)]
            f = _frame_from_rows(rows, source=f"{path.name}:{ws.title}", fallback_entity=ws.title)
            if f is not None:
                frames.append(f)
    elif path.suffix.lower() in {".csv", ".tsv"}:
        # Amazon's CSV has a short metadata line above the header, so rows are ragged;
        # read with the csv module rather than pandas. utf-8-sig drops the BOM.
        sep = "\t" if path.suffix.lower() == ".tsv" else ","
        with open(path, newline="", encoding="utf-8-sig") as fh:
            rows = [[None if v == "" else v for v in r] for r in csv.reader(fh, delimiter=sep)]
        f = _frame_from_rows(rows, source=path.name, fallback_entity=path.stem)
        if f is not None:
            frames.append(f)
    else:
        raise ValueError(f"Unsupported file type: {path.suffix}")
    if not frames:
        raise ValueError(f"No SQP tables found in {path}")
    return _coerce(pd.concat(frames, ignore_index=True))


def load_many(paths: list[str | Path]) -> pd.DataFrame:
    return pd.concat([load_sqp(p) for p in paths], ignore_index=True)
