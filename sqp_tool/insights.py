"""Seller-facing insight layers: query taxonomy (inferred), quadrant segmentation,
funnel-leak locator, visibility gaps, price sensitivity, token roll-ups and a
ranked action list."""
from __future__ import annotations

import re
from collections import Counter

import numpy as np
import pandas as pd

from .metrics import Thresholds, safe_div

# ---------------------------------------------------------------------------
# Token helpers
# ---------------------------------------------------------------------------
STOPWORDS = {
    "for", "with", "and", "the", "of", "to", "in", "on", "a", "an", "or", "by", "up",
    "no", "not", "&", "-", "+", "x", "w", "w/", "per", "from", "inch", "inches", "in.",
    "cm", "mm", "kg", "lb", "lbs", "pack", "set", "size", "large", "small", "medium",
    "new", "best", "cheap", "top",
}
ATTRIBUTE_DICT = {
    "colour": {"white", "black", "silver", "grey", "gray", "red", "blue", "green", "pink", "gold", "rose"},
    "quantity": {"single", "dual", "double", "twin", "triple", "quad", "2", "3", "4", "two", "three", "four"},
    "form": {"curved", "flat", "ultrawide", "vertical", "horizontal", "portable", "wireless", "wired",
             "foldable", "adjustable", "heavy", "duty", "slim", "mini", "compact", "tall", "wide"},
    "mount": {"clamp", "grommet", "wall", "desk", "stand", "mount", "arm", "bracket", "riser", "pole", "rail"},
    "mechanism": {"gas", "spring", "hydraulic", "motorised", "motorized", "electric", "manual"},
}
_SIZE_RE = re.compile(r'^\d+(\.\d+)?(["”]|in|inch|mm|cm|oz|ml|l|kg|g|lb|lbs)?$|^\d+-\d+$')


def tokens(q: str) -> list[str]:
    q = q.lower().replace("’", "'")
    return [t for t in re.split(r"[\s/,]+", q) if t]


def attribute_tags(q: str) -> str:
    tags = []
    for tok in tokens(q):
        if _SIZE_RE.match(tok):
            tags.append(f"size:{tok}")
            continue
        for cat, words in ATTRIBUTE_DICT.items():
            if tok in words:
                tags.append(f"{cat}:{tok}")
    return "|".join(dict.fromkeys(tags))


# ---------------------------------------------------------------------------
# Brand inference
# ---------------------------------------------------------------------------
def _is_generic(tok: str) -> bool:
    if tok in STOPWORDS or _SIZE_RE.match(tok):
        return True
    return any(tok in words for words in ATTRIBUTE_DICT.values())


def infer_brand_tokens(d: pd.DataFrame, own_brand: list[str] | None = None,
                       competitors: list[str] | None = None) -> tuple[set[str], set[str], pd.DataFrame]:
    """Guess own-brand and competitor-brand tokens from the SQP numbers themselves.

    own-brand token  : own impression share on queries containing it is >=5x the entity average,
                       it appears in >=3 queries (or as a standalone query) and carries real own
                       impressions. Shoppers searching your name see you.
    competitor token : leads its queries (brand names come first / stand alone), has meaningful
                       market impressions, near-zero own share, and is rare across the query set
                       (category words like "monitor" appear everywhere and are excluded).
    Explicit lists override inference. Results are surfaced so they can be corrected.
    """
    stats: dict[str, dict[str, float]] = {}
    for q, ct, co, it, io_ in zip(d["query"], d.clicks_total, d.clicks_own, d.impr_total, d.impr_own):
        toks = tokens(q)
        for pos, tok in enumerate(dict.fromkeys(toks)):
            s = stats.setdefault(tok, {"n": 0, "ct": 0.0, "co": 0.0, "it": 0.0, "io": 0.0,
                                       "single": 0, "leading": 0})
            s["n"] += 1
            s["ct"] += ct
            s["co"] += co
            s["it"] += it
            s["io"] += io_
            if len(toks) == 1:
                s["single"] += 1
            if pos == 0:
                s["leading"] += 1
    tf = pd.DataFrame.from_dict(stats, orient="index")
    tf.index.name = "token"
    n_queries = max(len(d), 1)
    base_click_share = d.clicks_own.sum() / max(d.clicks_total.sum(), 1)
    base_impr_share = d.impr_own.sum() / max(d.impr_total.sum(), 1)
    tf["click_share"] = tf.co / tf.ct.replace(0, np.nan)
    tf["impr_share"] = tf.io / tf.it.replace(0, np.nan)
    tf["click_lift"] = tf.click_share / (base_click_share or np.nan)
    tf["impr_lift"] = tf.impr_share / (base_impr_share or np.nan)
    tf["query_frac"] = tf.n / n_queries
    tf["generic"] = [_is_generic(t) for t in tf.index]

    own: set[str] = set(x.lower() for x in (own_brand or []))
    comp: set[str] = set(x.lower() for x in (competitors or []))
    if not own:
        cand = tf[(~tf.generic) & (tf.io >= 50) & (tf.impr_lift >= 5)
                  & ((tf.n >= 3) | (tf.single > 0))]
        # anchor on a token that leads its queries (brand names come first), then admit
        # companions of comparable lift and weight (e.g. "choco" -> "nose")
        anchors = cand[(cand.single > 0) | (cand.leading >= cand.n / 2)]
        if len(anchors):
            top = anchors.loc[(anchors.impr_lift * np.sqrt(anchors.io)).idxmax()]
            keep = cand[(cand.impr_lift >= 0.6 * top.impr_lift) & (cand.io >= 0.35 * top.io)]
            own = set(keep.sort_values("impr_lift", ascending=False).head(3).index) | {top.name}
    if not comp:
        cand = tf[(~tf.generic) & (~tf.index.isin(own)) & (tf.it >= 1000) & (tf.query_frac <= 0.05)]
        near_zero = cand.impr_share.fillna(0) <= max(base_impr_share * 0.25, 0.002)
        leads = (cand.single > 0) | (cand.leading == cand.n)
        comp = set(cand[near_zero & leads].index)
    tf["role"] = np.where(tf.index.isin(own), "own_brand",
                          np.where(tf.index.isin(comp), "competitor_brand",
                                   np.where(tf.generic, "attribute", "generic")))
    return own, comp, tf.reset_index()


def classify_queries(d: pd.DataFrame, own: set[str], comp: set[str]) -> pd.DataFrame:
    d = d.copy()
    def cls(q: str) -> str:
        toks = set(tokens(q))
        if toks & own:
            return "own_brand"
        if toks & comp:
            return "competitor_brand"
        return "generic"
    d["query_type"] = d["query"].map(cls)
    d["attributes"] = d["query"].map(attribute_tags)
    d["competitor"] = d["query"].map(lambda q: "|".join(sorted(set(tokens(q)) & comp)))
    return d


# ---------------------------------------------------------------------------
# Quadrants, leaks, opportunities
# ---------------------------------------------------------------------------
QUADRANT_ACTION = {
    "Winner": "Defend: protect rank & price, scale Sponsored spend, keep stock deep.",
    "Leaky page": "SERP works but PDP doesn't: audit price vs market, reviews/rating, images, A+, variations, delivery promise.",
    "Hidden gem": "Page converts but the tile loses the click: main image, title, price badge, rating count; push ranking/ads.",
    "Mismatch": "Under-performs on both — likely low relevance: check intent, consider PPC negative or a different ASIN.",
    "Not shown": "You get impressions but no clicks yet — visibility only; treat as SERP-tile / relevance test.",
    "Insufficient data": "Too few own impressions/clicks to judge; watch across periods or aggregate to brand level.",
}


def assign_quadrant(d: pd.DataFrame, t: Thresholds = Thresholds()) -> pd.DataFrame:
    d = d.copy()
    enough_ctr = d.impr_own >= t.min_impr
    enough_cvr = d.clicks_own >= t.min_clicks
    ctr_up = d.ctr_index >= 1
    cvr_up = d.cvr_index >= 1
    quad = np.select(
        [
            ~enough_ctr,
            enough_ctr & (d.clicks_own == 0),
            ~enough_cvr,
            ctr_up & cvr_up,
            ctr_up & ~cvr_up,
            ~ctr_up & cvr_up,
        ],
        ["Insufficient data", "Not shown", "Insufficient data", "Winner", "Leaky page", "Hidden gem"],
        default="Mismatch",
    )
    d["quadrant"] = quad
    d["quadrant_strength"] = np.where(
        (d.ctr_signif != "ns") & (d.cvr_signif != "ns") & (d.quadrant.isin(list(QUADRANT_ACTION)[:4])),
        "strong", "weak",
    )
    d["recommended_action"] = d.quadrant.map(QUADRANT_ACTION)
    return d


def funnel_leak(d: pd.DataFrame) -> pd.DataFrame:
    """Which stage loses most vs market for each query (lowest stage index)."""
    d = d.copy()
    idx = d[["ctr_index", "cart_index", "checkout_index"]].rename(
        columns={"ctr_index": "impression→click", "cart_index": "click→cart", "checkout_index": "cart→purchase"}
    )
    has = idx.notna().any(axis=1)
    d["biggest_leak"] = "n/a"
    d.loc[has, "biggest_leak"] = idx[has].idxmin(axis=1)
    d["biggest_leak_index"] = idx.min(axis=1)
    return d


def visibility_gaps(d: pd.DataFrame, top_n: int = 25) -> pd.DataFrame:
    """Relevant, high-volume queries where own impression share is low."""
    rel = d[(d.query_type != "competitor_brand") & (d.quadrant != "Mismatch")]
    rel = rel[rel.impr_share < 0.05]
    cols = ["entity_id", "period_label", "query", "query_type", "sqp_volume", "impr_total", "impr_share",
            "mkt_ctr", "mkt_cvr", "visibility_value", "headroom_purchases", "price_gap_click"]
    return rel.sort_values("visibility_value", ascending=False).head(top_n)[cols]


def price_sensitivity(d: pd.DataFrame, t: Thresholds = Thresholds()) -> pd.DataFrame:
    """Queries where a price premium coincides with under-conversion (price is the likely blocker)."""
    m = (d.price_position == "premium") & (d.cvr_index < 1) & (d.clicks_own >= t.min_clicks)
    cols = ["entity_id", "period_label", "query", "clicks_own", "price_click_own", "price_click_mkt",
            "price_gap_click", "own_cvr", "mkt_cvr", "cvr_index", "lost_purchases"]
    return d[m].sort_values("lost_purchases", ascending=False)[cols]


def token_rollup(d: pd.DataFrame, min_queries: int = 2) -> pd.DataFrame:
    """Aggregate performance by query token (attribute-level view)."""
    rows = []
    for _, r in d.iterrows():
        for tok in set(tokens(r["query"])):
            if tok in STOPWORDS:
                continue
            rows.append((tok, r.sqp_volume, r.impr_total, r.impr_own, r.clicks_total, r.clicks_own,
                         r.purch_total, r.purch_own))
    tf = pd.DataFrame(rows, columns=["token", "sqp_volume", "impr_total", "impr_own", "clicks_total",
                                     "clicks_own", "purch_total", "purch_own"])
    g = tf.groupby("token").agg(queries=("sqp_volume", "size"), sqp_volume=("sqp_volume", "sum"),
                                impr_total=("impr_total", "sum"), impr_own=("impr_own", "sum"),
                                clicks_total=("clicks_total", "sum"), clicks_own=("clicks_own", "sum"),
                                purch_total=("purch_total", "sum"), purch_own=("purch_own", "sum")).reset_index()
    g = g[g.queries >= min_queries]
    g["impr_share"] = safe_div(g.impr_own, g.impr_total)
    g["mkt_ctr"] = safe_div(g.clicks_total, g.impr_total)
    g["own_ctr"] = safe_div(g.clicks_own, g.impr_own)
    g["mkt_cvr"] = safe_div(g.purch_total, g.clicks_total)
    g["own_cvr"] = safe_div(g.purch_own, g.clicks_own)
    g["ctr_index"] = safe_div(g.own_ctr, g.mkt_ctr)
    g["cvr_index"] = safe_div(g.own_cvr, g.mkt_cvr)
    g["purch_share"] = safe_div(g.purch_own, g.purch_total)
    return g.sort_values("sqp_volume", ascending=False)


def action_list(d: pd.DataFrame, top_n: int = 10) -> pd.DataFrame:
    """Ranked, plain-language actions. Priority = purchases at stake."""
    d = d.copy()
    d["purchases_at_stake"] = d.lost_purchases + d.lost_clicks * d.mkt_cvr.fillna(0)
    # visibility plays: relevant queries where we barely show
    vis = (d.impr_share < 0.05) & (d.query_type != "competitor_brand") & (d.quadrant != "Mismatch")
    d.loc[vis, "purchases_at_stake"] = d.loc[vis, "purchases_at_stake"] + d.loc[vis, "visibility_value"] * 5

    def text(r) -> str:
        bits = []
        if r.quadrant in ("Leaky page", "Mismatch") and r.clicks_own > 0:
            bits.append(f"CVR {r.own_cvr:.1%} vs market {r.mkt_cvr:.1%} on {int(r.clicks_own)} clicks")
        if r.quadrant in ("Hidden gem", "Mismatch", "Not shown") and r.impr_own > 0:
            bits.append(f"CTR {r.own_ctr:.2%} vs market {r.mkt_ctr:.2%} on {int(r.impr_own)} impressions")
        if r.impr_share < 0.05:
            bits.append(f"only {r.impr_share:.1%} impression share on {int(r.impr_total):,} impressions")
        if pd.notna(r.price_gap_click) and abs(r.price_gap_click) > 0.15:
            bits.append(f"price {r.price_gap_click:+.0%} vs market median")
        return "; ".join(bits) + ". " + (r.recommended_action or "")

    d["why"] = d.apply(text, axis=1)
    cols = ["entity_id", "period_label", "query", "query_type", "quadrant", "quadrant_strength",
            "sqp_volume", "impr_share", "ctr_index", "cvr_index", "price_gap_click",
            "purchases_at_stake", "why"]
    return d[d.quadrant != "Insufficient data"].sort_values("purchases_at_stake", ascending=False).head(top_n)[cols]


# ---------------------------------------------------------------------------
# One-shot enrichment
# ---------------------------------------------------------------------------
def enrich(d: pd.DataFrame, t: Thresholds = Thresholds(), own_brand: list[str] | None = None,
           competitors: list[str] | None = None) -> tuple[pd.DataFrame, dict]:
    """Metrics must already be computed. Returns (enriched rows, context dict with brand tokens)."""
    own_all: set[str] = set()
    comp_all: set[str] = set()
    parts = []
    token_frames = []
    # infer brand tokens per entity (an ASIN report and a brand report may differ)
    for ent, grp in d.groupby("entity_id", sort=False):
        own, comp, tf = infer_brand_tokens(grp, own_brand, competitors)
        own_all |= own
        comp_all |= comp
        tf.insert(0, "entity_id", ent)
        token_frames.append(tf)
        parts.append(classify_queries(grp, own, comp))
    out = pd.concat(parts).sort_index()
    out = assign_quadrant(out, t)
    out = funnel_leak(out)
    ctx = {"own_brand_tokens": sorted(own_all), "competitor_tokens": sorted(comp_all),
           "token_stats": pd.concat(token_frames, ignore_index=True)}
    return out, ctx
