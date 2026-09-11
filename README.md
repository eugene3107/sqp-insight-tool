# SQP Insight Tool

Turns Amazon **Search Query Performance** exports (ASIN-level or brand-level, any period) into
Market-vs-Brand funnel metrics and seller-facing insights. Replaces the four manual columns
(Market CTR / Brand CTR / Market CVR / Brand CVR) and adds indices, confidence, quadrants,
opportunity sizing, price positioning and a ranked action list.

Design notes: [SQP_TOOL_PLAN.md](SQP_TOOL_PLAN.md).

## Setup

```bash
uv sync
```

## Excel report

```bash
uv run sqp analyse ~/Desktop/SQP.xlsx -o output/sqp_report.xlsx
```

Options: `--brand avlt` / `--competitor ergotron` (override inference, repeatable),
`--min-impr 20 --min-clicks 10` (sample-size thresholds), `--price-band 0.15`, `--top-n 25`.
Multiple files can be passed at once (e.g. several periods of the same ASIN).

Tabs: **Summary** (per entity/period, volume-weighted) · **Actions** (ranked, plain-language) ·
**Quadrants** · **Query Types** (own / competitor / generic) · **Visibility Gaps** ·
**Price Sensitivity** · **Tokens** (attribute roll-up) · **Data** (every query, every metric) ·
**Brand Tokens** (what was inferred) · **Definitions**.

## Dashboard

```bash
uv run sqp dashboard
```

Upload exports (or paste a local path) in the sidebar. Filters: entity, period, query type,
confidence. Tabs mirror the Excel report; the enriched workbook can be downloaded from the page.

## Deploy (Streamlit Community Cloud)

The repo is deploy-ready: `streamlit_app.py` is the entry point, `requirements.txt` pins
dependencies, `.streamlit/config.toml` carries the theme. At share.streamlit.io choose this repo,
branch `main`, main file `streamlit_app.py`. Restrict viewers under *Settings → Sharing* if the
app will hold client data.

## Key definitions

| Metric | Formula |
|---|---|
| Market CTR / Own CTR | clicks ÷ impressions (total / ASIN-or-brand) |
| Market CVR / Own CVR | purchases ÷ clicks |
| ctr_index, cvr_index | own ÷ market — `>1` beats the market |
| share_drift | purchase share − impression share |
| lost_clicks / lost_purchases | (market rate − own rate) × own denominator, floored at 0 |
| quadrant | Winner / Leaky page / Hidden gem / Mismatch from the two indices; `Insufficient data` below thresholds |

Amazon's own `Click Rate %`, `Cart Add Rate %`, `Purchase Rate %` divide by **Search Query
Volume**, not impressions — they are kept as `*_rate_amz` for reference only.

## Layout

```
sqp_tool/
  parse.py      raw export -> canonical long table (handles header/metadata drift, ASIN vs Brand)
  metrics.py    funnel rates, indices, shares, price gaps, opportunity, Wilson CIs
  insights.py   brand inference, query taxonomy, quadrants, leak locator, action list
  report.py     enriched Excel workbook
  dashboard.py  Streamlit app
  cli.py        `sqp analyse`, `sqp dashboard`
tests/          run with `uv run pytest`
```
