# Continuing FLIP RADAR development

This guide is for whoever picks the project up next, whether a person or an AI assistant. It explains
how to get a working environment, where everything lives, how to verify a change, and the decisions
that should not be undone by accident.

## 1. Environment setup (about 5 minutes)

```bash
git clone https://github.com/yourdady4ever/Flipradar.git && cd Flipradar
python3 -m venv .venv && .venv/bin/pip install -e "backend[dev]"
cd backend
../.venv/bin/python -m pytest -q          # backend tests (hermetic: no network)
../.venv/bin/ruff check flipradar tests scripts
```

Android, with either option:
* **Android Studio or Gradle with internet access:** `cd android && ./gradlew assembleDebug testDebugUnitTest`
* **Without Google Maven** (restricted networks): install the Android SDK command-line tools, then
  `platforms;android-36` and `build-tools;36.0.0`, plus the standalone Kotlin compiler 2.2.x. Then run
  `bash android/scripts/run-unit-tests.sh` and `bash android/scripts/build-offline.sh`. The scripts
  read `ANDROID_HOME`, `KOTLINC` and `M2` (a directory containing `junit-4.13.2.jar`,
  `hamcrest-core-1.3.jar` and `json-20250517.jar` from Maven Central).

## 2. Verify everything before and after a change

```bash
# 1. unit + integration tests
cd backend && ../.venv/bin/python -m pytest -q
# 2. live end-to-end (throwaway DB)
export FLIPRADAR_DATABASE_URL=sqlite:////tmp/fr-e2e.db FLIPRADAR_SCHEDULER_ENABLED=false
../.venv/bin/python -m flipradar init && ../.venv/bin/python -m flipradar seed-demo
TOKEN=$(../.venv/bin/python -m flipradar create-token --name e2e | tail -1)
../.venv/bin/python -m flipradar serve --port 8765 &
../.venv/bin/python scripts/e2e_check.py --url http://127.0.0.1:8765 --token "$TOKEN" --mutate
FLIPRADAR_URL=http://127.0.0.1:8765 FLIPRADAR_TOKEN=$TOKEN FLIPRADAR_SMOKE_MUTATE=1 node tests/web_smoke/smoke.mjs
bash ../android/scripts/run-contract-check.sh http://127.0.0.1:8765 "$TOKEN"
kill %1
# dashboard pure-function tests and mock-server smoke (no backend needed)
node --test tests/web_smoke/*.test.mjs && NODE_PATH=$(npm root -g) node tests/web_smoke/run_mock.mjs
# (the live checks above make about 200 requests: run the server with FLIPRADAR_RATE_LIMIT_PER_MINUTE=5000)
# 3. Android
bash ../android/scripts/run-unit-tests.sh && bash ../android/scripts/build-offline.sh
# 4. regenerate API docs if the API changed
../.venv/bin/python scripts/gen_api_docs.py > ../API_DOCUMENTATION.md
```

CI (`.github/workflows/ci.yml`) runs the backend tests, the offline APK build and the Gradle build
on every push.

## 3. Code map

| Path | What it is | Notes |
|---|---|---|
| `backend/flipradar/config.py` | All settings (`FLIPRADAR_*` env vars) | Every integration is optional |
| `backend/flipradar/models.py` | SQLAlchemy models | Money is `*_cents` (int), rates are `*_bp` (int) |
| `backend/flipradar/schemas.py` | **The HTTP contract** | When you change it, also update `android/.../data/ApiModels.kt` and `web/static/app.js`, then regenerate the API docs |
| `backend/flipradar/finance/calculator.py` | Deterministic profit math | Pure; heavily tested; keep it pure |
| `backend/flipradar/finance/import_costs.py` | Courier import duties (MX) | Dated rules with sources |
| `backend/flipradar/normalize/`, `matching/` | Title → attributes → product clusters | Precision first: when unsure, do not cluster |
| `backend/flipradar/engine/` | Comparables, stats, risk, demand, scoring, explanations | Pure functions; `now` is passed in |
| `backend/flipradar/collectors/` | One module per data source + `netguard` (SSRF guard) | Register new ones in `registry.BUILTIN_MODULES` |
| `backend/flipradar/services/` | Database ↔ engine glue, inventory, analytics, alerts, scheduler, FX, CSV import, demo data | |
| `backend/flipradar/api/routers/` | FastAPI routers (`/api/v1`) | |
| `backend/flipradar/web/` | Static dashboard (vanilla JS, strict CSP) | Never use `innerHTML` with server data |
| `backend/flipradar/ai/narrative.py` | Optional LLM summary + number validator | AI may only rephrase facts |
| `android/` | Kotlin app using framework APIs only (no AndroidX) | `data/ApiClient.kt` is pure Kotlin and contract-tested |
| `docs/DATA_SOURCES.md` | Research on every source, its fees and taxes | Re-verify items marked low confidence |

## 4. How to…

### Add a data source
1. Create `backend/flipradar/collectors/<name>.py` with a `Collector` subclass, a `CollectorInfo`
   (key, kind, country, currency, capabilities), `is_configured()`, `search()` and the `@register`
   decorator. Map only the fields the source actually returns into `RawListing`, and never guess.
2. Add the module to `registry.BUILTIN_MODULES`, and any settings to `config.py`, all optional.
3. If the source is **not** a Mexican resale marketplace, give it `country != "MX"` or kind
   `feed`/`retailer`. Its listings are then treated as purchase candidates only
   (`services/analysis.is_resale_reference`).
4. Write respx-mocked tests with fixtures shaped exactly like the documented responses, and update
   `docs/DATA_SOURCES.md` with the auth, cost, limits and terms of service.

### Change fees or taxes
Built-in fee profiles live in `backend/flipradar/services/fees.py`. Seeding **never overwrites**
existing rows, so users keep their edits. To ship corrected defaults to existing installs, change the
values and tell users to update the profile through the dashboard or `PUT /api/v1/fee-profiles/{key}`.
Alternatively, add a one-off migration that updates rows still equal to the old defaults. Always
update `source_url`, `as_of` and `docs/DATA_SOURCES.md`.
**Every January:** check the Ley de Ingresos de la Federación for the year. The 2026 ISR withholding
of 2.5 % comes from LIF 2026 Art. 25 fr. VI and reverts to 1 % unless the new LIF extends it.

### Schema changes
The schema is created with `create_all()`, which only adds **new tables**. It does not add new
columns to existing tables. For a column change on a deployed database, either:
* add Alembic (`pip install alembic`, `alembic init`, point `target_metadata` at
  `flipradar.db.Base.metadata`, and autogenerate a baseline revision before your change), or
* for small additive changes, add the column to `services/bootstrap.ensure_added_columns`. It runs
  `ALTER TABLE ... ADD COLUMN` when `PRAGMA table_info` shows the column is missing, which is how
  `inventory_items.written_off_at` was added.

Back up `backend/data/flipradar.db` first.

### Scale beyond one user
Move to PostgreSQL (`FLIPRADAR_DATABASE_URL=postgresql+psycopg://...`, then install the driver). Run
the scheduler as a separate process (`python -m flipradar run-searches` from cron) with
`FLIPRADAR_SCHEDULER_ENABLED=false` on the API process. Then replace the in-memory rate limiter with a
shared store.

## 5. Decisions that should stay

* **Money is integer centavos and Decimal; percentages are basis points.** No floats in financial paths.
* **No estimate without evidence:** fewer than `min_comparables` comparables means no opportunity.
  Demand is `unknown` without sales evidence.
* **Resale prices come only from resale-market sources:** Mexican marketplaces and the user's manual
  or CSV data. eBay US, deal feeds and retailer pages are buy-side evidence.
* **Suspicious** means a critical flag or 2+ warnings (`engine.risk.is_suspicious`). A single warning
  is shown but does not hide the deal.
* **No scraping behind anti-bot protection, and no bypassing API policy blocks.** See `docs/DATA_SOURCES.md`.
* **AI output is validated against facts,** and is discarded if it contains an unknown number.
* **Inventory accounting:** cost basis is allocated cumulatively, so the sale that empties an item
  takes the rounding remainder. Unit or extra costs can't be edited once an item has sales (409); use
  a sale's `other_costs_cents`. Written-off stock is a realized loss, while `returned` means refunded.
* **No write transaction across an await,** because of the SQLite single-writer lock. See ARCHITECTURE.md.
* **The Android app uses only framework APIs,** so it builds without Google Maven. Adding AndroidX
  would break `build-offline.sh`. If you do add it, keep the Gradle build as the only path and update
  CI and the docs.

## 6. Known gaps

See `TODO.md` for the prioritized backlog and `PROJECT_STATUS.md` for what is verified and what is not.
