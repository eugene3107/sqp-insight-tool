# SQP Insight Tool — Structure & Plan

Turns raw Amazon Search Query Performance (SQP) exports into Market vs Brand
funnel metrics and seller-facing insights. Built around the sample workbook
`~/Desktop/SQP.xlsx` (6 ASIN-level sheets + 1 brand-level sheet).

---

## 1. Observations from the sample file (drive the design)

| Finding | Implication |
|---|---|
| Header row is row 2 or 3; metadata lives in A1 (`ASIN=[...]` / `ASIN or Product=[...]`), F1 (range), G1 (year), H1 (month/quarter) | Parser must locate the `Search Query` header dynamically and read metadata from row 1 |
| Brand-level export renames `ASIN Count/Share/Price` → `Brand Count/Share/Price`, and `Same-Day` → `Same Day` | Normalise all "own" columns to a single `own_*` name; scope flag = `asin` or `brand` |
| Amazon's `Click Rate %`, `Cart Add Rate %`, `Purchase Rate %` = count ÷ **Search Query Volume**, not ÷ impressions (13/34 = 38.24%) | Values > 100% are possible (2600% seen). Compute per-impression rates ourselves; show Amazon's as "rate per search" |
| Many rows have 0–3 own clicks / 0 own impressions | Need `min_sample` thresholds, Wilson confidence intervals, and an explicit "low data" flag; avoid div-by-zero |
| Irrelevant queries appear (e.g. "p plate holder", "samsung odyssey g9" for a monitor arm) | Add relevance / intent classification so they can be excluded or used as PPC negatives |
| Shipping-speed columns exist only at market level (no own-ASIN split) | Shipping insights are market-level only |

Your 4 manual columns (kept as the core):

```
Market CTR = Clicks Total     / Impressions Total
Brand  CTR = Clicks Own       / Impressions Own
Market CVR = Purchases Total  / Clicks Total
Brand  CVR = Purchases Own    / Clicks Own
```

---

## 2. Architecture

```
sqp_tool/
├── ingest/
│   ├── parse.py        # xlsx/csv → normalised long DataFrame (handles header drift, metadata row, ASIN vs Brand)
│   └── schema.py       # canonical column names + validation
├── metrics/
│   ├── funnel.py       # CTR / cart-add / CVR for market & own, indices, share metrics
│   ├── stats.py        # Wilson CI, sample-size flags, significance of own vs market
│   └── opportunity.py  # lost clicks / lost purchases / headroom
├── insights/
│   ├── classify.py     # branded / competitor / generic; attribute tags (dual, triple, white, 34", curved…)
│   ├── quadrant.py     # CTR-index × CVR-index segmentation + recommended action
│   ├── pricing.py      # own vs market median price gap by stage
│   ├── trends.py       # period-over-period (same ASIN/brand, multiple exports)
│   └── portfolio.py    # ASIN vs ASIN on the same query (cannibalisation, best ASIN per query)
├── report/
│   ├── excel.py        # enriched workbook (all metrics, flags, colour scales, summary tab)
│   ├── dashboard.py    # Streamlit (or static HTML) dashboard
│   └── summary.py      # plain-language "top 10 actions" text
├── cli.py              # sqp analyse <files...> --brand "AVLT" --out report.xlsx
└── tests/              # fixtures from SQP.xlsx
```

**Canonical data model** (one row per query × entity × period):

```
marketplace, scope (asin|brand), entity_id, brand_name,
period_type (monthly|quarterly|weekly), period_end,
query, sqp_score, sqp_volume,
impr_total, impr_own, clicks_total, clicks_own,
carts_total, carts_own, purch_total, purch_own,
price_click_mkt, price_click_own, price_cart_mkt, price_cart_own, price_purch_mkt, price_purch_own,
ship_sameday_clicks, ship_1d_clicks, ship_2d_clicks, (same for carts, purchases)
```

Everything downstream is computed from this table, so ASIN-level and brand-level
exports, and any number of periods, flow through the same code.

---

## 3. Metrics layer

### Core (your 4)
- `mkt_ctr`, `own_ctr`, `mkt_cvr`, `own_cvr`

### Full funnel (both sides)
- Cart-add rate: `carts / clicks`
- Cart→purchase rate: `purch / carts`
- Search→purchase: `purch / sqp_volume` (Amazon's definition, kept for reference)

### Relative performance indices  (the headline numbers)
- `ctr_index = own_ctr / mkt_ctr`   (>1 = your listing wins the click)
- `cvr_index = own_cvr / mkt_cvr`   (>1 = your page converts better)
- `cart_index`, `checkout_index` likewise

### Share & share drift
- Impression / click / cart / purchase share (given by Amazon)
- `share_drift = purchase_share − impression_share` → +ve means you convert above your share of voice; –ve means you're shown but not chosen

### Price positioning
- `price_gap_% = (own_price − mkt_price) / mkt_price` at click, cart and purchase stage
- Flag: premium (>+15%), parity, discount (<−15%)

### Opportunity sizing (turns rates into units)
- `lost_clicks = max(0, mkt_ctr − own_ctr) × impr_own`
- `lost_purchases = max(0, mkt_cvr − own_cvr) × clicks_own`
- `headroom_purchases = purch_total − purch_own` (what the rest of the market sells on this query)
- `impression_headroom = impr_total × (target_share − impr_share)`

### Statistical guard-rails
- Wilson 95% CI on own CTR/CVR; `confidence = high|medium|low` based on own denominator
- `significant = own rate CI does not overlap market rate`
- Default `min_sample`: 20 own impressions for CTR, 10 own clicks for CVR (configurable)

---

## 4. Insight modules (what the seller actually reads)

### 4.1 Query quadrant (CTR index × CVR index)
| | CVR ≥ market | CVR < market |
|---|---|---|
| **CTR ≥ market** | **Winners** — defend rank, scale ads, protect price | **Leaky page** — SERP works, PDP doesn't: reviews, price, images, A+, variations |
| **CTR < market** | **Hidden gems** — page converts, SERP tile loses: main image, title, price badge, rating; push ranking/ads | **Mismatch** — probably irrelevant or wrong product; negate in PPC / ignore |

Each query gets a quadrant + a templated recommendation.

### 4.2 Visibility gap
High `sqp_volume`, relevant, low `impr_share` → ranking/SEO/Sponsored targets. Ranked by `impression_headroom × mkt_cvr` (expected purchases if you showed up).

### 4.3 Price sensitivity
Queries where `price_gap_% > +15%` **and** `cvr_index < 1` → price is likely the blocker. Queries where you're cheaper and still under-convert → not a price problem, look at listing.

### 4.4 Funnel leak locator
For each query, which stage has the biggest own-vs-market drop: impression→click, click→cart, cart→purchase. Aggregated across queries → "your #1 leak is cart→purchase" type summary.

### 4.5 Query taxonomy
- **Own-brand** (e.g. "avlt") — brand-defence health
- **Competitor-brand** (ergotron, humanmotion, dell, samsung…) — conquesting performance
- **Generic** — split by attributes (dual / triple / single, white, curved, 34", ultrawide, gas spring…)
  → roll-ups per attribute: "white" queries convert at 2.1× market, "triple" at 0.4×

### 4.6 Trends (needs ≥2 periods of the same entity)
- Δ share, Δ ctr_index, Δ cvr_index per query; new queries, lost queries
- Rising-volume queries where you have no share yet

### 4.7 Portfolio view (multiple ASINs of one brand)
- Best ASIN per query; queries where two ASINs split impressions (cannibalisation)
- Brand-level report vs sum of ASIN reports → coverage gaps

### 4.8 Shipping-speed context (market level)
- % of market clicks/purchases on same-day / 1D / 2D → whether Prime speed matters on this query

---

## 5. Outputs

1. **Enriched Excel** — original data + all metrics/flags, colour scales, quadrant column, plus tabs: `Summary`, `Actions`, `Visibility Gaps`, `Price`, `Taxonomy`.
2. **Dashboard** (Streamlit first; static HTML/React later) — KPI header (weighted own vs market CTR/CVR, share), quadrant scatter (bubble = volume), funnel comparison, price-gap chart, sortable query table with filters (quadrant, taxonomy, confidence), trend lines when multiple periods loaded.
3. **Plain-language action list** — top 10 actions ranked by opportunity size, e.g. *"'dual monitor arm white' — CTR 0.6× market on 579 impressions; price +250% vs market median. Review price or main image."*

---

## 6. Recommended stack

- Python 3.11, `pandas`, `openpyxl` (read/write, keep formulas optional), `scipy` (Wilson CI)
- `streamlit` + `plotly` for the dashboard (fast to ship, runs locally, no hosting needed)
- `typer` for the CLI
- `pytest` with `SQP.xlsx` sheets as fixtures

Alternative if you want it shareable without Python: same engine, export to a single self-contained HTML report.

---

## 7. Phased build

| Phase | Deliverable |
|---|---|
| 1 | Parser + canonical model + core 4 metrics + indices + confidence flags → enriched Excel. Replaces the manual columns. |
| 2 | Quadrant, opportunity sizing, price gap, funnel leak, action list. |
| 3 | Query taxonomy (brand list + attribute dictionary config), attribute roll-ups. |
| 4 | Streamlit dashboard. |
| 5 | Multi-period trends + multi-ASIN portfolio view. |

---

## 8. Open decisions

1. Primary output: Excel-first, dashboard-first, or both from day one?
2. Brand/competitor names and attribute dictionary — supply a config list, or let the tool infer from the queries?
3. Weekly SQP exports too, or only monthly/quarterly?
4. Thresholds: min sample sizes and the ±15% price band — sensible defaults, but should be configurable per category.
