"""End-to-end checks against the sample workbook (skipped if it is not present)."""
from pathlib import Path

import numpy as np
import openpyxl
import pandas as pd
import pytest

from sqp_tool.insights import enrich
from sqp_tool.metrics import compute_metrics, wilson
from sqp_tool.parse import load_sqp

SAMPLE = Path.home() / "Desktop" / "SQP.xlsx"
needs_sample = pytest.mark.skipif(not SAMPLE.exists(), reason="sample workbook not present")


@pytest.fixture(scope="module")
def enriched():
    if not SAMPLE.exists():
        pytest.skip("sample workbook not present")
    d = compute_metrics(load_sqp(SAMPLE))
    return enrich(d)


@needs_sample
def test_parses_every_sheet_with_metadata():
    raw = load_sqp(SAMPLE)
    assert raw.entity_id.nunique() == 7
    assert set(raw.scope) == {"asin", "brand"}
    assert set(raw.period_label) >= {"2025 January", "2024 Q4", "2025 Q1", "2025 Q2"}
    assert raw["query"].str.len().min() > 0


@needs_sample
def test_core_rates_match_manual_formulas():
    """Market CTR = K/H, Brand CTR = M/I, Market CVR = AC/K, Brand CVR = AE/M in the source sheet."""
    d = compute_metrics(load_sqp(SAMPLE, sheets=["AU_dm43"]))
    ws = openpyxl.load_workbook(SAMPLE, data_only=False)["AU_dm43"]
    hdr = {ws.cell(2, c).value: c for c in range(1, ws.max_column + 1)}
    for r in range(3, 13):
        q = str(ws.cell(r, 1).value).strip().lower()
        row = d[d["query"] == q].iloc[0]
        H, I, K, M, AC, AE = (ws.cell(r, hdr[h]).value or 0 for h in (
            "Impressions: Total Count", "Impressions: ASIN Count", "Clicks: Total Count",
            "Clicks: ASIN Count", "Purchases: Total Count", "Purchases: ASIN Count"))
        assert row.mkt_ctr == pytest.approx(K / H)
        assert (np.isnan(row.own_ctr) if I == 0 else row.own_ctr == pytest.approx(M / I))
        assert (np.isnan(row.mkt_cvr) if K == 0 else row.mkt_cvr == pytest.approx(AC / K))
        assert (np.isnan(row.own_cvr) if M == 0 else row.own_cvr == pytest.approx(AE / M))


@needs_sample
def test_amazon_rates_are_per_search_volume():
    d = compute_metrics(load_sqp(SAMPLE, sheets=["AU_dm41"]))
    row = d[d["query"] == "avlt dual monitor arm"].iloc[0]
    assert row.clicks_rate_amz == pytest.approx(100 * row.clicks_total / row.sqp_volume, rel=1e-3)
    assert row.mkt_ctr < 0.1  # per-impression CTR is a real funnel rate


def test_wilson_bounds():
    lo, hi = wilson(pd.Series([0, 5, 10]), pd.Series([0, 10, 10]))
    assert np.isnan(lo[0]) and np.isnan(hi[0])
    assert 0 < lo[1] < 0.5 < hi[1] < 1
    assert hi[2] == pytest.approx(1.0)


@needs_sample
def test_quadrants_and_brand_inference(enriched):
    d, ctx = enriched
    assert set(d.quadrant) <= {"Winner", "Leaky page", "Hidden gem", "Mismatch", "Not shown", "Insufficient data"}
    assert "avlt" in ctx["own_brand_tokens"]
    assert {"choco", "nose"} <= set(ctx["own_brand_tokens"])
    # a brand query is classified as own_brand
    assert (d[d["query"] == "avlt"].query_type == "own_brand").all()
    # low-sample rows never land in a judgement quadrant
    low = d[d.impr_own < 20]
    assert (low.quadrant == "Insufficient data").all()


@needs_sample
def test_opportunity_never_negative(enriched):
    d, _ = enriched
    assert (d.lost_clicks >= 0).all() and (d.lost_purchases >= 0).all()


def test_parses_amazon_csv_with_ragged_metadata_row(tmp_path):
    """Amazon CSV: BOM, 4-field metadata line, then a 34-field quoted header and rows."""
    header = ['Search Query', 'Search Query Score', 'Search Query Volume', 'Impressions: Total Count',
              'Impressions: ASIN Count', 'Impressions: ASIN Share %', 'Clicks: Total Count', 'Clicks: Click Rate %',
              'Clicks: ASIN Count', 'Clicks: ASIN Share %', 'Clicks: Price (Median)', 'Clicks: ASIN Price (Median)',
              'Clicks: Same Day Shipping Speed', 'Clicks: 1D Shipping Speed', 'Clicks: 2D Shipping Speed',
              'Cart Adds: Total Count', 'Cart Adds: Cart Add Rate %', 'Cart Adds: ASIN Count', 'Cart Adds: ASIN Share %',
              'Cart Adds: Price (Median)', 'Cart Adds: ASIN Price (Median)', 'Cart Adds: Same Day Shipping Speed',
              'Cart Adds: 1D Shipping Speed', 'Cart Adds: 2D Shipping Speed', 'Purchases: Total Count',
              'Purchases: Purchase Rate %', 'Purchases: ASIN Count', 'Purchases: ASIN Share %',
              'Purchases: Price (Median)', 'Purchases: ASIN Price (Median)', 'Purchases: Same Day Shipping Speed',
              'Purchases: 1D Shipping Speed', 'Purchases: 2D Shipping Speed', 'Reporting Date']
    row = ['fitness weighted ball', '84', '58', '1,841', '3', '0.16', '47', '81.03', '1', '2.13', '19.79', '26.99',
           '8', '7', '12', '2', '3.45', '0', '0.0', '19.79', '', '0', '0', '0', '0', '0.0', '0', '', '', '', '0',
           '0', '0', '2026-06-30']
    q = lambda xs: ",".join(f'"{x}"' if x != "" else "" for x in xs)
    p = tmp_path / "US_SQP.csv"
    p.write_text("﻿" + 'ASIN=["B0GSDRK2JK"],Reporting Range=["Quarterly"],Select year=["2026"],Select quarter=["2"]\n'
                 + q(header) + "\n" + q(row) + "\n", encoding="utf-8")
    d = load_sqp(p)
    assert len(d) == 1 and d.scope[0] == "asin" and d.entity_id[0] == "B0GSDRK2JK"
    assert d.period_label[0] == "2026 Q2" and d.impr_total[0] == 1841 and d.impr_own[0] == 3
    assert d.price_click_mkt[0] == pytest.approx(19.79) and np.isnan(d.price_cart_own[0])
