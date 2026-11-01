# Project status

_Last updated: 2026-11-01 (version 0.1.0)_

## Summary

FLIP RADAR v0.1.0 is a complete, self-hosted product. It has a backend (FastAPI + SQLite), a REST API
(51 endpoints), a web dashboard, an Android app, a data collection engine, a deterministic opportunity
engine, inventory and analytics, alerts with push notifications, and optional AI summaries guarded by
a validator. Every component below was **executed and tested** in the build environment. The only
exception is the live third-party APIs, which that environment's network policy blocked.

## Verification evidence

| Check | Result | How to reproduce |
|---|---|---|
| Backend unit and integration tests | **1327 passed**, hermetic (a guard blocks DNS and outbound connections) | `cd backend && ../.venv/bin/python -m pytest` |
| Lint | ruff clean | `ruff check flipradar tests scripts` |
| Live end-to-end over HTTP (demo data) | **74/74 checks passed**: auth, every list/detail, calculator, buy → inventory → sale (realized profit equals predicted profit to the centavo; imported buys include their estimated duties in the inventory cost), analytics, alerts, tracked searches, CSV import (normalized and clustered), SSRF refusals | `backend/scripts/e2e_check.py --mutate` |
| Web dashboard in Chromium (Playwright) | **26/26 steps passed**: every view, create/edit/delete flows, CSV import, 360 px phone width, no console or CSP errors | `node backend/tests/web_smoke/smoke.mjs` |
| Android app's real `ApiClient` + parsers against the live server (JVM) | **28/28 passed** | `android/scripts/run-contract-check.sh` |
| Android JVM unit tests | **80 passed** | `android/scripts/run-unit-tests.sh` |
| Android APK, offline toolchain | Signed debug APK, about 0.87 MB, minSdk 26 / targetSdk 36; `apksigner verify` OK | `android/scripts/build-offline.sh` |
| Android build, standard Gradle/AGP 8.13 + unit tests | **Green on GitHub Actions** | `.github/workflows/ci.yml`, job `android-gradle` |
| Fresh `pip install -e backend[dev]` + wheel contents | OK (web assets included) | see `CONTINUATION.md` |

## Reviews

* **First adversarial review:** 8 specialist reviewers plus independent verifiers. 68 findings: **67
  confirmed and fixed**, each with a regression test that failed before the fix. One was refuted
  because it was already fixed.
* **Second (regression) review of those fixes:** 26 findings (8 confirmed, 18 pending verification
  when recorded), each listed with its failure scenario and suggested fix in
  [`docs/OPEN_REVIEW_FINDINGS.md`](docs/OPEN_REVIEW_FINDINGS.md). That file is the authoritative list
  of what is still open; start there. Fixed so far, each with a regression test:
  - #1: buying an imported opportunity now includes its estimated import duties in the inventory cost;
  - #8 / #14, watching case: the stale-buy sweep no longer expires WATCHING opportunities;
  - #5: a bought opportunity can be restored to new from the dashboard and the Android app (the
    server still refuses it, with a 409 the clients explain, while an inventory item references it);
  - #19 and #20: this file and the CSV amount rules in `API_DOCUMENTATION.md`.

## Components

| Component | Status | Notes |
|---|---|---|
| Profit calculator | ✅ | Commission, fixed/shipping tiers, payment fees, IVA on fees, free-shipping threshold, ISR/IVA withholdings, break-even, max buy for a target ROI |
| Import-cost estimator | ✅ | Mexican courier regime: USMCA ≤ USD 50 exempt / 17 % / 19 %; non-FTA 33.5 % |
| Fee profiles | ✅ | 10 built-in profiles with sources and dates; all editable. Some values are **low confidence** (see `docs/DATA_SOURCES.md`) |
| Product normalization and matching | ✅ | 40+ brands, 62 model families, Spanish/English, GTIN, storage and variants, accessory/bundle detection |
| Opportunity engine | ✅ | Comparables with exclusion reasons, robust statistics, sold-evidence preference, risk rules, demand evidence, 0–100 score, explanations |
| Collectors | ✅ code + mocked tests / ⚠️ not live-verified | Mercado Libre catalog API, eBay Browse, Banxico + 2 key-less FX fallbacks, RSS, JSON-LD single URL, CSV |
| REST API + auth + rate limiting + security headers | ✅ | Bearer tokens (SHA-256 stored); in-memory rate limiter per client address, and per token for API requests with a valid token; strict CSP |
| Scheduler | ✅ | Tracked searches, FX refresh with back-off |
| Alerts and notifications | ✅ | In-app inbox; ntfy / Telegram / webhook (mock-tested) |
| Inventory and sales | ✅ | Average-cost basis, multi-unit sales, delete-restores |
| Analytics | ✅ | Summary, aging, by source, top products, time series |
| AI summaries | ✅ (optional) | Ollama or an OpenAI-compatible server; output rejected if it contains numbers not in the facts |
| Web dashboard | ✅ | Vanilla JS, no CDN, strict CSP, light/dark, phone width |
| Android app | ✅ builds and is contract-tested / ⚠️ not run on a device | Framework-only Kotlin; 11 activities; background notifications |

## Working integrations

* **Verified working here:** SQLite, the CLI, the REST API, the dashboard, CSV import, the JSON-LD URL
  check (SSRF guard and robots logic tested), and the demo dataset.
* **Implemented against documented API shapes, mock-tested, not live-tested:** Mercado Libre catalog
  API, eBay Browse API, Banxico SIE, Frankfurter, ExchangeRate-API, RSS feeds, ntfy, Telegram,
  webhooks, Ollama.

## Limitations (honest list)

1. **No live third-party call was possible from the build environment.** The network policy blocked
   `api.mercadolibre.com`, `api.ebay.com`, Banxico and the other hosts, so the collectors are tested
   against fixtures that follow the documented shapes. Run the P1 checks in `TODO.md` with real keys.
2. **Mercado Libre coverage is catalog-only.** Its public keyword search is closed to third-party apps
   in 2026, so free-form used listings can't be collected automatically. Use CSV import for Facebook
   Marketplace and Mercado Libre used listings you find manually.
3. **Demand estimates need history.** Mercado Libre hides `sold_quantity` from non-owners, so demand is
   usually `unknown` until FLIP RADAR has observed listings sell, or you record your own sales.
4. **Resale estimates use competing asking prices** (25th percentile by default), unless at least 3
   observed sold prices exist. Asking prices are not guaranteed sale prices; the explanation says so.
5. **Fees and taxes change.** The 2026 values are dated and sourced, and several are low confidence.
   This is not tax advice.
6. **The Android app has not been run on a physical device or emulator**, since neither was available.
   Its HTTP layer was exercised against the real server on the JVM.
7. **The deployment is single-process** (in-memory rate limiter, embedded scheduler). See
   `CONTINUATION.md` for scaling.

## Recurring costs

None are required. See `docs/DATA_SOURCES.md` § 4 for the full inventory of optional costs.
