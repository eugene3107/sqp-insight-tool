"""Funnel metrics: market vs own CTR / cart-add / CVR, relative indices, shares,
price gaps, opportunity sizing and Wilson confidence intervals.

Rates are computed per impression / per click (true funnel rates). Amazon's own
`Click Rate %` etc. are per *search query volume* and are kept as `*_rate_amz`
for reference only.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

Z95 = 1.959963984540054


@dataclass(frozen=True)
class Thresholds:
    min_impr: int = 20        # own impressions needed for a trustworthy own CTR
    min_clicks: int = 10      # own clicks needed for a trustworthy own CVR
    price_band: float = 0.15  # ±15% = price parity band
    high_multiple: int = 5    # n >= high_multiple * min => "high" confidence


def safe_div(num: pd.Series, den: pd.Series) -> pd.Series:
    num = num.astype(float)
    den = den.astype(float)
    return pd.Series(np.where(den > 0, num / den.where(den > 0, np.nan), np.nan), index=num.index)


def wilson(k: pd.Series, n: pd.Series, z: float = Z95) -> tuple[pd.Series, pd.Series]:
    """Wilson score interval for a binomial proportion; NaN where n == 0."""
    k = k.astype(float)
    n = n.astype(float)
    with np.errstate(divide="ignore", invalid="ignore"):
        p = k / n
        denom = 1 + z**2 / n
        centre = (p + z**2 / (2 * n)) / denom
        half = z * np.sqrt(p * (1 - p) / n + z**2 / (4 * n**2)) / denom
        lo = np.where(n > 0, centre - half, np.nan)
        hi = np.where(n > 0, centre + half, np.nan)
    return pd.Series(lo, index=k.index).clip(0, 1), pd.Series(hi, index=k.index).clip(0, 1)


def _confidence(n: pd.Series, minimum: int, high_mult: int) -> pd.Series:
    return pd.Series(
        np.select([n >= minimum * high_mult, n >= minimum], ["high", "medium"], default="low"),
        index=n.index,
    )


def _signif(lo: pd.Series, hi: pd.Series, benchmark: pd.Series) -> pd.Series:
    out = np.where(lo > benchmark, "above", np.where(hi < benchmark, "below", "ns"))
    out = np.where(lo.isna() | benchmark.isna(), "n/a", out)
    return pd.Series(out, index=lo.index)


def compute_metrics(df: pd.DataFrame, t: Thresholds = Thresholds()) -> pd.DataFrame:
    d = df.copy()

    # ---- funnel rates (per impression / per click / per cart) ----
    d["mkt_ctr"] = safe_div(d.clicks_total, d.impr_total)
    d["own_ctr"] = safe_div(d.clicks_own, d.impr_own)
    d["mkt_cart_rate"] = safe_div(d.carts_total, d.clicks_total)
    d["own_cart_rate"] = safe_div(d.carts_own, d.clicks_own)
    d["mkt_checkout_rate"] = safe_div(d.purch_total, d.carts_total)
    d["own_checkout_rate"] = safe_div(d.purch_own, d.carts_own)
    d["mkt_cvr"] = safe_div(d.purch_total, d.clicks_total)
    d["own_cvr"] = safe_div(d.purch_own, d.clicks_own)
    # Amazon-style per-search rates, for reference
    d["mkt_search_to_purchase"] = safe_div(d.purch_total, d.sqp_volume)
    d["own_search_to_purchase"] = safe_div(d.purch_own, d.sqp_volume)

    # ---- relative indices (own / market); >1 = beating the market ----
    d["ctr_index"] = safe_div(d.own_ctr, d.mkt_ctr)
    d["cart_index"] = safe_div(d.own_cart_rate, d.mkt_cart_rate)
    d["checkout_index"] = safe_div(d.own_checkout_rate, d.mkt_checkout_rate)
    d["cvr_index"] = safe_div(d.own_cvr, d.mkt_cvr)

    # ---- shares (fractions, recomputed from counts) ----
    d["impr_share"] = safe_div(d.impr_own, d.impr_total).fillna(0.0)
    d["click_share"] = safe_div(d.clicks_own, d.clicks_total).fillna(0.0)
    d["cart_share"] = safe_div(d.carts_own, d.carts_total).fillna(0.0)
    d["purch_share"] = safe_div(d.purch_own, d.purch_total).fillna(0.0)
    d["share_drift"] = d.purch_share - d.impr_share

    # ---- price positioning ----
    for stage in ("click", "cart", "purch"):
        d[f"price_gap_{stage}"] = safe_div(
            d[f"price_{stage}_own"] - d[f"price_{stage}_mkt"], d[f"price_{stage}_mkt"]
        )
    gap = d.price_gap_click
    d["price_position"] = np.select(
        [gap.isna(), gap > t.price_band, gap < -t.price_band],
        ["unknown", "premium", "discount"], default="parity",
    )

    # ---- opportunity sizing (units) ----
    d["lost_clicks"] = ((d.mkt_ctr - d.own_ctr).clip(lower=0) * d.impr_own).fillna(0.0)
    d["lost_purchases"] = ((d.mkt_cvr - d.own_cvr).clip(lower=0) * d.clicks_own).fillna(0.0)
    d["headroom_purchases"] = (d.purch_total - d.purch_own).clip(lower=0)
    # expected extra purchases if own share of impressions rose by 1pt at market rates
    d["visibility_value"] = (d.impr_total * 0.01 * d.mkt_ctr * d.mkt_cvr).fillna(0.0)

    # ---- confidence ----
    d["own_ctr_lo"], d["own_ctr_hi"] = wilson(d.clicks_own, d.impr_own)
    d["own_cvr_lo"], d["own_cvr_hi"] = wilson(d.purch_own, d.clicks_own)
    d["ctr_confidence"] = _confidence(d.impr_own, t.min_impr, t.high_multiple)
    d["cvr_confidence"] = _confidence(d.clicks_own, t.min_clicks, t.high_multiple)
    d["ctr_signif"] = _signif(d.own_ctr_lo, d.own_ctr_hi, d.mkt_ctr)
    d["cvr_signif"] = _signif(d.own_cvr_lo, d.own_cvr_hi, d.mkt_cvr)

    # ---- market shipping-speed mix (share of clicks with fast delivery options) ----
    fast_clicks = d.ship_sd_clicks + d.ship_1d_clicks + d.ship_2d_clicks
    d["mkt_fast_ship_click_share"] = safe_div(fast_clicks, d.clicks_total)
    fast_purch = d.ship_sd_purch + d.ship_1d_purch + d.ship_2d_purch
    d["mkt_fast_ship_purch_share"] = safe_div(fast_purch, d.purch_total)
    return d


def entity_summary(d: pd.DataFrame, by: list[str] | None = None) -> pd.DataFrame:
    """Volume-weighted roll-up (sums of counts, then rates) per entity/period."""
    by = by or ["entity_id", "scope", "period_label"]
    agg = d.groupby(by, dropna=False).agg(
        queries=("query", "nunique"),
        sqp_volume=("sqp_volume", "sum"),
        impr_total=("impr_total", "sum"), impr_own=("impr_own", "sum"),
        clicks_total=("clicks_total", "sum"), clicks_own=("clicks_own", "sum"),
        carts_total=("carts_total", "sum"), carts_own=("carts_own", "sum"),
        purch_total=("purch_total", "sum"), purch_own=("purch_own", "sum"),
        lost_clicks=("lost_clicks", "sum"), lost_purchases=("lost_purchases", "sum"),
    ).reset_index()
    agg["mkt_ctr"] = safe_div(agg.clicks_total, agg.impr_total)
    agg["own_ctr"] = safe_div(agg.clicks_own, agg.impr_own)
    agg["mkt_cvr"] = safe_div(agg.purch_total, agg.clicks_total)
    agg["own_cvr"] = safe_div(agg.purch_own, agg.clicks_own)
    agg["ctr_index"] = safe_div(agg.own_ctr, agg.mkt_ctr)
    agg["cvr_index"] = safe_div(agg.own_cvr, agg.mkt_cvr)
    agg["impr_share"] = safe_div(agg.impr_own, agg.impr_total)
    agg["click_share"] = safe_div(agg.clicks_own, agg.clicks_total)
    agg["purch_share"] = safe_div(agg.purch_own, agg.purch_total)
    agg["share_drift"] = agg.purch_share - agg.impr_share
    return agg
