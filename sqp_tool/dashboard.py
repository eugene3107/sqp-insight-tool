"""Streamlit dashboard for the SQP Insight Tool.  Run: `sqp dashboard`."""
from __future__ import annotations

import io
import os
import tempfile
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from sqp_tool.insights import action_list, enrich, price_sensitivity, token_rollup, visibility_gaps
from sqp_tool.metrics import Thresholds, compute_metrics, entity_summary
from sqp_tool.parse import load_sqp
from sqp_tool.report import write_report

BLUE, BLUE_LIGHT, BLUE_DARK, BLUE_PALE = "#2B54AB", "#4A73C9", "#1E3D7D", "#E8EEF8"
GRAY, GRAY_700, BLACK = "#8C8C8C", "#333333", "#0A0A0A"
LOGO = Path(os.environ.get("SQP_LOGO", Path(__file__).with_name("assets") / "logo.png"))
# Streamlit Community Cloud mounts the repo under /mount/src; a local-path field is meaningless there.
IS_CLOUD = str(Path(__file__).resolve()).startswith("/mount/src") or os.environ.get("SQP_HIDE_LOCAL_PATH") == "1"

st.set_page_config(page_title="SQP Insight", page_icon="🔎", layout="wide")
st.markdown(
    """
<style>
@import url('https://fonts.googleapis.com/css2?family=Poppins:wght@300;400;500;600;700&display=swap');
html, body, [class*="css"], .stMarkdown, .stDataFrame { font-family: 'Poppins', -apple-system, sans-serif; color: #333333; }
h1, h2, h3 { font-family: 'Poppins', sans-serif; color: #0A0A0A; font-weight: 700; }
.ec-hero { background:#2B54AB; color:#fff; padding:24px 32px; border-radius:8px; margin-bottom:24px; }
.ec-hero h1 { color:#fff; margin:0; font-size:28px; }
.ec-hero p { color:#E8EEF8; margin:4px 0 0; font-weight:300; }
.ec-label { color:#2B54AB; text-transform:uppercase; letter-spacing:2px; font-size:11px; font-weight:500; }
.ec-tile { background:#E8EEF8; border-left:4px solid #2B54AB; border-radius:8px; padding:16px; }
.ec-tile .v { font-size:28px; font-weight:700; color:#0A0A0A; line-height:1.1; }
.ec-tile .s { font-size:13px; color:#333333; font-weight:300; }
</style>
""",
    unsafe_allow_html=True,
)

PLOT_LAYOUT = dict(
    font=dict(family="Poppins, sans-serif", color=GRAY_700, size=13),
    paper_bgcolor="white", plot_bgcolor="white", margin=dict(l=40, r=20, t=56, b=40),
    xaxis=dict(gridcolor="#F0F0F0", zerolinecolor="#E0E0E0"),
    yaxis=dict(gridcolor="#F0F0F0", zerolinecolor="#E0E0E0"),
    legend=dict(orientation="h", yanchor="bottom", y=-0.42, x=0),
)


def _layout(title: str, **kw) -> dict:
    return {**PLOT_LAYOUT, "title": dict(text=title, x=0, xanchor="left", font=dict(size=15, color=BLACK)), **kw}


# --------------------------------------------------------------------------- data
@st.cache_data(show_spinner="Parsing SQP exports…")
def _load(file_bytes: list[tuple[str, bytes]]) -> pd.DataFrame:
    frames = []
    for name, data in file_bytes:
        with tempfile.NamedTemporaryFile(suffix=Path(name).suffix, delete=False) as tmp:
            tmp.write(data)
            path = tmp.name
        f = load_sqp(path)
        f["source"] = f["source"].str.replace(Path(path).name, name, regex=False)
        frames.append(f)
    return pd.concat(frames, ignore_index=True)


@st.cache_data(show_spinner="Computing metrics…")
def _analyse(raw: pd.DataFrame, min_impr: int, min_clicks: int, price_band: float,
             own: tuple[str, ...], comp: tuple[str, ...]):
    t = Thresholds(min_impr=min_impr, min_clicks=min_clicks, price_band=price_band)
    d = compute_metrics(raw, t)
    return enrich(d, t, list(own) or None, list(comp) or None)


def _pct(x: float) -> str:
    return "–" if pd.isna(x) else f"{x:.2%}"


def _tile(col, label: str, value: str, sub: str) -> None:
    col.markdown(
        f'<div class="ec-tile"><div class="ec-label">{label}</div><div class="v">{value}</div>'
        f'<div class="s">{sub}</div></div>', unsafe_allow_html=True)


# --------------------------------------------------------------------------- sidebar
with st.sidebar:
    if LOGO.exists():
        st.image(str(LOGO), width=56)
    st.markdown('<div class="ec-label">Inputs</div>', unsafe_allow_html=True)
    uploads = st.file_uploader("SQP exports (.xlsx / .csv)", accept_multiple_files=True,
                               type=["xlsx", "xlsm", "csv", "tsv"])
    default_path = "" if IS_CLOUD else st.text_input("…or a local path", value="")
    st.markdown('<div class="ec-label">Thresholds</div>', unsafe_allow_html=True)
    min_impr = st.number_input("Own impressions to trust CTR", 1, 1000, 20)
    min_clicks = st.number_input("Own clicks to trust CVR", 1, 1000, 10)
    price_band = st.slider("Price parity band ±", 0.0, 0.5, 0.15, 0.05)
    st.markdown('<div class="ec-label">Brand overrides</div>', unsafe_allow_html=True)
    own_in = st.text_input("Own-brand tokens (comma-sep, blank = infer)", "")
    comp_in = st.text_input("Competitor tokens (comma-sep, blank = infer)", "")

file_bytes: list[tuple[str, bytes]] = [(u.name, u.getvalue()) for u in uploads] if uploads else []
if default_path and Path(default_path).exists():
    file_bytes.append((Path(default_path).name, Path(default_path).read_bytes()))

st.markdown('<div class="ec-hero"><h1>SQP Insight</h1><p>Market vs Brand funnel performance from Amazon Search Query Performance</p></div>',
            unsafe_allow_html=True)

if not file_bytes:
    st.info("Upload one or more SQP exports to begin." if IS_CLOUD
            else "Upload one or more SQP exports (or enter a local path) to begin.")
    st.stop()

raw = _load(file_bytes)
own_t = tuple(x.strip().lower() for x in own_in.split(",") if x.strip())
comp_t = tuple(x.strip().lower() for x in comp_in.split(",") if x.strip())
d_all, ctx = _analyse(raw, int(min_impr), int(min_clicks), float(price_band), own_t, comp_t)

# --------------------------------------------------------------------------- filters (one row)
f1, f2, f3, f4 = st.columns([2, 2, 2, 2])
entities = sorted(d_all.entity_id.unique())
entity = f1.selectbox("Entity (ASIN / brand)", entities)
periods = sorted(d_all[d_all.entity_id == entity].period_label.unique())
period = f2.selectbox("Period", periods, index=len(periods) - 1)
qtypes = f3.multiselect("Query type", ["own_brand", "competitor_brand", "generic"],
                        default=["own_brand", "competitor_brand", "generic"])
conf = f4.multiselect("CTR confidence", ["high", "medium", "low"], default=["high", "medium", "low"])

d = d_all[(d_all.entity_id == entity) & (d_all.period_label == period)]
d = d[d.query_type.isin(qtypes) & d.ctr_confidence.isin(conf)]
if d.empty:
    st.warning("No rows match the current filters.")
    st.stop()

# --------------------------------------------------------------------------- KPI tiles
s = entity_summary(d).iloc[0]
c1, c2, c3, c4, c5 = st.columns(5)
_tile(c1, "Own CTR vs market", _pct(s.own_ctr), f"market {_pct(s.mkt_ctr)} · index {s.ctr_index:.2f}×")
_tile(c2, "Own CVR vs market", _pct(s.own_cvr), f"market {_pct(s.mkt_cvr)} · index {s.cvr_index:.2f}×")
_tile(c3, "Impression share", _pct(s.impr_share), f"click share {_pct(s.click_share)}")
_tile(c4, "Purchase share", _pct(s.purch_share), f"share drift {s.share_drift:+.2%}")
_tile(c5, "Purchases at stake", f"{s.lost_purchases:,.0f}", f"lost clicks {s.lost_clicks:,.0f} · {int(s.queries)} queries")
ts = ctx["token_stats"]
ts = ts[ts.entity_id == entity]
own_tok = sorted(ts[ts.role == "own_brand"].token)
comp_tok = sorted(ts[ts.role == "competitor_brand"].token)
st.caption(f"Inferred own-brand tokens: **{', '.join(own_tok) or '—'}** · "
           f"competitor tokens: **{', '.join(comp_tok) or '—'}** (override in sidebar)")

# --------------------------------------------------------------------------- tabs
tab_over, tab_act, tab_q, tab_vis, tab_price, tab_tok = st.tabs(
    ["Overview", "Actions", "Queries", "Visibility gaps", "Price", "Tokens"])

with tab_over:
    left, right = st.columns([1, 1])
    # funnel comparison
    stages = ["Impression→Click", "Click→Cart", "Cart→Purchase", "Click→Purchase"]
    mkt = [s.mkt_ctr, d.carts_total.sum() / max(d.clicks_total.sum(), 1),
           d.purch_total.sum() / max(d.carts_total.sum(), 1), s.mkt_cvr]
    own = [s.own_ctr, d.carts_own.sum() / max(d.clicks_own.sum(), 1),
           d.purch_own.sum() / max(d.carts_own.sum(), 1), s.own_cvr]
    fig = go.Figure()
    fig.add_bar(name="Market", x=stages, y=mkt, marker_color=GRAY, marker_line_width=0,
                hovertemplate="%{x}<br>Market %{y:.2%}<extra></extra>")
    fig.add_bar(name="Own", x=stages, y=own, marker_color=BLUE, marker_line_width=0,
                hovertemplate="%{x}<br>Own %{y:.2%}<extra></extra>")
    fig.update_layout(**_layout("Funnel: own vs market (volume-weighted)", barmode="group", bargap=0.35,
                                bargroupgap=0.08, yaxis_tickformat=".1%"))
    left.plotly_chart(fig, width="stretch")

    # quadrant scatter: position = quadrant, colour = confidence, size = volume
    q = d[(d.ctr_index.notna()) & (d.cvr_index.notna()) & (d.quadrant != "Insufficient data")]
    fig2 = go.Figure()
    for lvl, colr in (("high", BLUE_DARK), ("medium", BLUE_LIGHT), ("low", "#B7C6E8")):
        g = q[q.cvr_confidence == lvl]
        if g.empty:
            continue
        fig2.add_scatter(
            name=f"{lvl} confidence", mode="markers", x=g.ctr_index, y=g.cvr_index,
            marker=dict(color=colr, opacity=0.75, size=(g.sqp_volume.clip(lower=1) ** 0.5 / 2).clip(6, 22),
                        line=dict(color="white", width=1)),
            text=g["query"],
            customdata=g[["quadrant", "impr_share", "own_ctr", "mkt_ctr", "own_cvr", "mkt_cvr"]].values,
            hovertemplate="<b>%{text}</b><br>%{customdata[0]}<br>CTR %{customdata[2]:.2%} vs %{customdata[3]:.2%}"
                          "<br>CVR %{customdata[4]:.1%} vs %{customdata[5]:.1%}<br>impr share %{customdata[1]:.1%}<extra></extra>",
        )
    xmax = float(max(2.5, min(q.ctr_index.quantile(0.98) * 1.1, 6))) if len(q) else 2.5
    ymax = float(max(2.5, min(q.cvr_index.quantile(0.98) * 1.1, 6))) if len(q) else 2.5
    fig2.add_vline(x=1, line=dict(color="#C8C8C8", dash="dot"))
    fig2.add_hline(y=1, line=dict(color="#C8C8C8", dash="dot"))
    for txt, x, y in (("Hidden gem", 0.05, ymax * 0.95), ("Winner", xmax * 0.95, ymax * 0.95),
                      ("Mismatch", 0.05, 0.05), ("Leaky page", xmax * 0.95, 0.05)):
        fig2.add_annotation(x=x, y=y, text=txt, showarrow=False, font=dict(color=GRAY, size=12),
                            xanchor="left" if x < 1 else "right", yanchor="top" if y > 1 else "bottom")
    fig2.update_layout(**_layout("Query quadrants (bubble = search volume)",
                                 xaxis_title="CTR index (own ÷ market)", yaxis_title="CVR index (own ÷ market)",
                                 xaxis_range=[0, xmax], yaxis_range=[0, ymax]))
    right.plotly_chart(fig2, width="stretch")

    qc = d.quadrant.value_counts().rename_axis("quadrant").reset_index(name="queries")
    qs = d.groupby("quadrant").agg(sqp_volume=("sqp_volume", "sum"), impr_own=("impr_own", "sum"),
                                   purch_own=("purch_own", "sum"), lost_purchases=("lost_purchases", "sum")).reset_index()
    st.dataframe(qc.merge(qs, on="quadrant"), width="stretch", hide_index=True)

    leak = d[d.biggest_leak != "n/a"].groupby("biggest_leak").agg(
        queries=("query", "size"), lost_purchases=("lost_purchases", "sum")).reset_index()
    if not leak.empty:
        st.markdown('<div class="ec-label">Biggest funnel leak (queries by stage with lowest own/market index)</div>',
                    unsafe_allow_html=True)
        st.dataframe(leak.sort_values("queries", ascending=False), width="stretch", hide_index=True)

with tab_act:
    a = action_list(d, top_n=25)
    st.dataframe(a, width="stretch", hide_index=True,
                 column_config={"impr_share": st.column_config.NumberColumn(format="%.2%"),
                                "ctr_index": st.column_config.NumberColumn(format="%.2f×"),
                                "cvr_index": st.column_config.NumberColumn(format="%.2f×"),
                                "price_gap_click": st.column_config.NumberColumn(format="%+.0%"),
                                "purchases_at_stake": st.column_config.NumberColumn(format="%.1f"),
                                "why": st.column_config.TextColumn(width="large")})

with tab_q:
    quads = st.multiselect("Quadrant", sorted(d.quadrant.unique()), default=sorted(d.quadrant.unique()))
    cols = ["query", "query_type", "quadrant", "quadrant_strength", "sqp_volume", "impr_total", "impr_own",
            "impr_share", "mkt_ctr", "own_ctr", "ctr_index", "ctr_signif", "clicks_own", "mkt_cvr", "own_cvr",
            "cvr_index", "cvr_signif", "purch_share", "share_drift", "price_gap_click", "lost_clicks",
            "lost_purchases", "biggest_leak", "attributes"]
    st.dataframe(d[d.quadrant.isin(quads)][cols].sort_values("sqp_volume", ascending=False),
                 width="stretch", hide_index=True, height=560,
                 column_config={c: st.column_config.NumberColumn(format="%.2%") for c in
                                ("impr_share", "mkt_ctr", "own_ctr", "mkt_cvr", "own_cvr", "purch_share",
                                 "share_drift", "price_gap_click")}
                 | {c: st.column_config.NumberColumn(format="%.2f×") for c in ("ctr_index", "cvr_index")})

with tab_vis:
    st.markdown("Relevant, high-volume queries where you barely appear — ranked by expected purchases per +1pt share.")
    st.dataframe(visibility_gaps(d, 40), width="stretch", hide_index=True,
                 column_config={c: st.column_config.NumberColumn(format="%.2%") for c in
                                ("impr_share", "mkt_ctr", "mkt_cvr", "price_gap_click")})

with tab_price:
    ps = price_sensitivity(d)
    st.markdown("Queries where a price premium (>{:.0%}) coincides with under-conversion.".format(price_band))
    if ps.empty:
        st.success("No price-sensitivity flags at current thresholds.")
    else:
        st.dataframe(ps, width="stretch", hide_index=True,
                     column_config={c: st.column_config.NumberColumn(format="%.2%") for c in
                                    ("price_gap_click", "own_cvr", "mkt_cvr")})
    pp = d[d.price_gap_click.notna()]
    if not pp.empty:
        fig3 = go.Figure()
        fig3.add_scatter(mode="markers", x=pp.price_gap_click, y=pp.cvr_index, text=pp["query"],
                         marker=dict(color=BLUE, size=(pp.clicks_own.clip(lower=1) ** 0.5).clip(6, 36),
                                     line=dict(color="white", width=1)),
                         hovertemplate="<b>%{text}</b><br>price gap %{x:+.0%}<br>CVR index %{y:.2f}×<extra></extra>")
        fig3.add_vline(x=0, line=dict(color="#C8C8C8", dash="dot"))
        fig3.add_hline(y=1, line=dict(color="#C8C8C8", dash="dot"))
        fig3.update_layout(**_layout("Price gap vs CVR index (bubble = own clicks)",
                                     xaxis_title="Own price vs market median", xaxis_tickformat="+.0%",
                                     yaxis_title="CVR index"))
        st.plotly_chart(fig3, width="stretch")

with tab_tok:
    tk = token_rollup(d).head(60)
    st.markdown("Performance rolled up by query word — spot attributes (colour, size, form) where you over/under-index.")
    st.dataframe(tk, width="stretch", hide_index=True, height=560,
                 column_config={c: st.column_config.NumberColumn(format="%.2%") for c in
                                ("impr_share", "mkt_ctr", "own_ctr", "mkt_cvr", "own_cvr", "purch_share")}
                 | {c: st.column_config.NumberColumn(format="%.2f×") for c in ("ctr_index", "cvr_index")})

# --------------------------------------------------------------------------- export
st.divider()
buf = io.BytesIO()
with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as tmp:
    write_report(d_all, ctx, tmp.name)
    buf.write(Path(tmp.name).read_bytes())
st.download_button("⬇ Download enriched Excel report (all entities)", buf.getvalue(), "sqp_report.xlsx",
                   mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
