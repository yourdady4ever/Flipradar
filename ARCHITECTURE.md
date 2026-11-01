# FLIP RADAR architecture

## Overview

```
                ┌──────────────────────────── backend (Python, single process) ───────────────────────────┐
                │                                                                                          │
 Mercado Libre ─┤ collectors/        services/collection.py        services/ingestion.py                   │
 eBay Browse   ─┤  (one class per  ─► run_collection() ──────────► normalize/ ─► matching/ ─► SQLite       │
 RSS feeds     ─┤   source, async)                                  (attributes)  (product clusters)       │
 JSON-LD URL   ─┤                                                                                          │
 CSV import    ─┤ services/imports.py ─────────────────────────────┘                                       │
 Banxico/FX    ─┤ collectors/banxico.py, fx_sources.py ─► services/fx.py (USD→MXN, stored per day)         │
                │                                                                                          │
                │ services/analysis.py ─► engine/ (pure, deterministic)                                    │
                │      stats → risk → demand → finance/calculator → scoring → explain                      │
                │      └─► Opportunity rows ─► services/alerts.py ─► Notification rows ─► notify.py        │
                │                                                     (in-app, ntfy, Telegram, webhook)    │
                │ services/inventory.py, analytics.py, settings_store.py, fees.py, demo.py                 │
                │ services/scheduler.py (asyncio loop: tracked searches, FX refresh)                       │
                │ ai/narrative.py (optional local LLM; output validated against deterministic facts)       │
                │                                                                                          │
                │ api/routers/* (FastAPI, /api/v1, bearer tokens) + web/ (static dashboard at /)           │
                └──────────────────────────────────────────────────────────────────────────────────────────┘
                                   ▲                                   ▲
                       Android app (Kotlin, framework-only)     Browser dashboard
                       JobScheduler polling → notifications
```

## Design principles

1. **Financial results are deterministic.** All money is stored as integer centavos and all rates as
   integer basis points. Intermediate math uses `Decimal`, and each line item is rounded half-up, the
   way an invoice is. The engine modules (`engine/*`, `finance/*`) are pure functions: they take
   explicit inputs, including `now`, with no I/O and no randomness.
2. **No invented data.** Expected resale prices come only from observed comparable listings, and only
   when at least `min_comparables` exist. Demand is reported as `unknown` unless there is real sales
   evidence. AI is optional, may only rephrase computed facts, and is rejected if its text contains
   a number that is not in the facts.
3. **Every recommendation is explainable.** Each opportunity stores its facts (`explanation_facts_json`),
   the price statistics, the cost breakdown, the score components and the risk flags. The explanation
   text is generated from those stored facts only.
4. **Zero mandatory cost.** SQLite and local execution. Every integration is optional and free, using
   the user's own keys. A source without keys reports "not configured"; nothing fails.
5. **Legitimate data access only.** FLIP RADAR uses official APIs, user-chosen feeds, user-initiated
   single-page checks (which honor robots.txt and have an SSRF guard), and manual or CSV import. It
   does no scraping behind anti-bot protection. See `docs/DATA_SOURCES.md`.

## Data model (backend/flipradar/models.py)

| Table | Purpose |
|---|---|
| `sources` | Known data sources: enabled flag, last run status/error, default resale fee profile |
| `tracked_searches` | Queries the scheduler runs periodically against chosen sources |
| `collection_runs` | Audit log of each collector run (fetched/new/updated counts, errors) |
| `listings` | One offer on one source (unique `source_id + external_id`), normalized attributes, risk flags, product link |
| `price_observations` | Time series of price, status and quantities per listing (the basis for history and demand evidence) |
| `products` | Product clusters, keyed by `canonical_key` (e.g. `apple\|iphone 15 pro\|256gb`) or GTIN |
| `fx_rates` | Exchange rates per day and source (stored as exact decimal strings) |
| `fee_profiles` | Selling cost models: commission, fixed-fee tiers, payment fees, IVA on fees, shipping, tax withholdings, plus source URL and as-of date |
| `opportunities` | An analysed buy candidate: profit, ROI, score, confidence, facts, explanation and user status |
| `alert_rules`, `notifications` | Watch rules and the in-app notification inbox, including push delivery status |
| `inventory_items`, `sales` | Purchased stock and realized sales, with average-cost basis and calculator breakdown |
| `api_tokens` | SHA-256 hashes of bearer tokens (the plain token is shown once) |
| `app_settings` | Runtime-editable analysis settings |

The schema is created with `Base.metadata.create_all()` at startup. Before changing a column on an
existing deployment, see `CONTINUATION.md` § "Schema changes".

## Opportunity pipeline (per product cluster)

1. **Comparable selection** (`engine/stats.py`). For a buy candidate, comparables are listings of the
   same product in the same condition class (new / refurbished / used, where unknown counts as used),
   seen within `comparable_max_age_days`. The following are excluded: the candidate itself, the same
   seller, accessories, bundles, demo data, ended listings, and listings with critical risk flags.
   Each seller counts once. Outliers with robust z > 3.5 are removed. Every exclusion is counted and
   reported.
2. **Resale price estimate.** If at least `min_comparables` sold observations exist, their median
   is used. Otherwise the `competitive` strategy uses the 25th percentile of competing asking prices
   (the price at which you would need to list to sell), and the `market` strategy uses the median.
3. **Profit** (`finance/calculator.py`). The selected fee profile supplies commission, fixed or
   shipping tiers, payment fees, IVA on fees, seller-paid shipping above the free-shipping threshold,
   and ISR/IVA withholdings. The calculator also returns the break-even sale price and the maximum
   buy price for the target ROI.
4. **Risk** (`engine/risk.py`). Deterministic rules flag: replica wording, box or parts only, locked
   or reported devices, prices far below market, off-platform payment or contact, new or
   poorly-rated sellers, unknown shipping, and low match confidence.
5. **Demand** (`engine/demand.py`). Uses `sold_quantity` deltas over time and listings observed as
   sold. Supply counts are informational only. With no evidence, demand is `unknown`.
6. **Score** (`engine/scoring.py`). A score from 0 to 100 is built from ROI (30), absolute profit (20),
   price-evidence quality (20), match confidence (10) and demand (10), minus risk penalties. A
   confidence level (high / medium / low) comes with it.
7. **Explanation** (`engine/explain.py`). A facts dictionary and a sectioned plain-text explanation.

## Product equivalence (normalize/, matching/)

Titles are normalized: accents stripped, units unified (`128 GB` → `128gb`), and Spanish and English
vocabularies used to detect brand, model family, storage, RAM, color, identity variants (digital/disc,
OLED/Lite, Wi-Fi/cellular), condition, accessories and bundles. Clustering tries each method in order:

1. **GTIN**, with a validated check digit. Confidence 0.99.
2. **Canonical key**, `brand|model|storage|variants`. Confidence 0.95.
3. **Guarded fuzzy match** (rapidfuzz). Used only when brand and model already match and storage and
   variants do not conflict.

Accessories never share a cluster with devices.

## Interfaces

* **REST API.** FastAPI under `/api/v1` with bearer tokens. The contract lives in
  `backend/flipradar/schemas.py`; see `API_DOCUMENTATION.md` and `docs/API_CONTRACT.md`. Interactive
  OpenAPI docs are served at `/docs`.
* **Web dashboard.** Static HTML, JS and CSS in `backend/flipradar/web/`. No CDN, strict CSP, served at `/`.
* **Android app.** Kotlin using only Android framework APIs (no AndroidX), so it can be built without
  Google Maven. There are two build paths: Gradle/AGP (Android Studio, CI) and
  `android/scripts/build-offline.sh`, which calls the SDK tools directly.
* **CLI.** `python -m flipradar …` (init, serve, tokens, collect, recompute, import-csv, demo, FX).

## Concurrency model

There is one process. FastAPI routes and the scheduler run on one asyncio loop. Collectors are async
(httpx). Database access goes through synchronous SQLAlchemy sessions with SQLite in WAL mode. This is
appropriate for a single user or a small team. To scale beyond that, see `CONTINUATION.md`, which
covers moving to PostgreSQL and a separate worker.
