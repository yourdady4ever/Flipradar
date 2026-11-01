# FLIP RADAR

**Self-hosted resale-arbitrage intelligence for the Mexican market.** FLIP RADAR collects prices from
legitimate sources, works out which listings are the same product, estimates a realistic resale
price from observed comparables, and calculates the exact net profit after Mexican marketplace fees
and tax withholdings. It flags suspicious listings, ranks opportunities and explains every
recommendation, without inventing data.

* **Backend:** Python, FastAPI and SQLite, with a REST API, a scheduler and a web dashboard
* **Android app:** Kotlin, using only Android framework APIs: opportunities, calculator, inventory, analytics, alerts and push notifications
* **Zero mandatory cost:** everything runs locally, and every integration is optional and free with your own keys
* **Deterministic finance:** integer centavos, basis points and Decimal math, with an auditable breakdown of every peso

> FLIP RADAR is a decision-support tool, not financial or tax advice. Verify every listing before
> buying, and check fee and tax parameters for your own situation.

## What works today

| Area | Status |
|---|---|
| Profit calculator: commissions, fixed and shipping tiers, payment fees, IVA on fees, ISR/IVA withholdings, break-even, max buy price | ✅ tested |
| Product normalization and equivalence: Spanish/English titles, GTIN, storage and variants, accessory and bundle detection | ✅ tested |
| Opportunity engine: comparables, robust statistics, risk flags, demand evidence, 0–100 score, explanations | ✅ tested |
| REST API (about 50 endpoints) with token auth, rate limiting and security headers | ✅ tested |
| Web dashboard at `/`: opportunities, calculator, inventory, analytics, searches, alerts, notifications, settings | ✅ smoke-tested in Chromium |
| Inventory and sales with average-cost basis, realized profit and analytics time series | ✅ tested |
| Alerts, plus in-app, ntfy, Telegram and webhook notifications | ✅ tested (push channels with mocked HTTP) |
| Collectors: Mercado Libre catalog API, eBay Browse API, Banxico/Frankfurter/ExchangeRate-API FX, RSS feeds, JSON-LD single URL check, CSV import | ✅ tested with mocked API responses (see limitations) |
| Android app: framework-only Kotlin, signed debug APK built with `android/scripts/build-offline.sh` | ✅ builds; JVM unit tests pass |
| Optional AI summaries (Ollama or an OpenAI-compatible server), with a number-validation guard | ✅ tested with mocked LLM |

See [PROJECT_STATUS.md](PROJECT_STATUS.md) for exact test results and known limitations.

## Quick start (backend + dashboard)

Requirements: Python 3.11+.

```bash
git clone https://github.com/yourdady4ever/Flipradar.git
cd Flipradar/backend
python -m venv .venv && . .venv/bin/activate       # Windows: .venv\Scripts\activate
pip install -e ".[dev]"

python -m flipradar init                            # creates backend/data/flipradar.db + default fee profiles
python -m flipradar create-token --name laptop      # prints a token ONCE - save it
python -m flipradar seed-demo                       # optional: clearly-labelled synthetic demo data
python -m flipradar serve                           # http://127.0.0.1:8000  (API docs at /docs)
```

Open `http://127.0.0.1:8000` and paste the token. To reach the server from your phone on the same
Wi-Fi, run `python -m flipradar serve --host 0.0.0.0` and read [SECURITY.md](SECURITY.md) first.

### Configure data sources (all optional, all free)

Create `backend/.env` (it is git-ignored):

```ini
# Mercado Libre catalog API - create an app at https://developers.mercadolibre.com.mx/
FLIPRADAR_ML_CLIENT_ID=...
FLIPRADAR_ML_CLIENT_SECRET=...
# FLIPRADAR_ML_REFRESH_TOKEN=...   # if you linked your seller account (authorization_code flow)

# eBay Browse API (US listings that ship to Mexico) - https://developer.ebay.com/
FLIPRADAR_EBAY_CLIENT_ID=...
FLIPRADAR_EBAY_CLIENT_SECRET=...

# Official USD/MXN FIX rate - free token at https://www.banxico.org.mx/SieAPIRest/service/v1/token
FLIPRADAR_BANXICO_TOKEN=...

# RSS/Atom deal feeds you are allowed to read (comma separated)
# FLIPRADAR_RSS_FEEDS=https://example.com/feed.xml

# Push notifications (optional)
# FLIPRADAR_NTFY_URL=https://ntfy.sh
# FLIPRADAR_NTFY_TOPIC=your-private-random-topic
# FLIPRADAR_TELEGRAM_BOT_TOKEN=...
# FLIPRADAR_TELEGRAM_CHAT_ID=...

# Optional local AI summaries (https://ollama.com) - off by default
# FLIPRADAR_LLM_PROVIDER=ollama
# FLIPRADAR_LLM_MODEL=llama3.1:8b
```

Then add tracked searches in the dashboard (**Searches**), or run one now:

```bash
python -m flipradar collect "iphone 15 pro 256gb" --source mercadolibre --source ebay
```

Without any API keys you can still use FLIP RADAR fully: import listings you collected by hand
(Facebook Marketplace, store visits and so on) with **CSV import**, check single product pages with
**Check URL**, and use the calculator and inventory.

## Android app

```bash
cd android
./gradlew assembleDebug                 # with internet access (Android Studio / CI)
# or, with only the Android SDK command-line tools + kotlinc:
bash scripts/build-offline.sh           # -> android/build/outputs/flipradar-debug.apk
adb install -r build/outputs/flipradar-debug.apk
```

On first launch, enter the server URL (for example `http://192.168.1.50:8000`) and your token. See
[android/README.md](android/README.md).

## Documentation

| File | Contents |
|---|---|
| [ARCHITECTURE.md](ARCHITECTURE.md) | Components, data model, opportunity pipeline |
| [API_DOCUMENTATION.md](API_DOCUMENTATION.md) | Every endpoint, with examples |
| [docs/DATA_SOURCES.md](docs/DATA_SOURCES.md) | Researched sources, limits, costs, fee and tax parameters |
| [SECURITY.md](SECURITY.md) | Threat model and deployment checklist |
| [PROJECT_STATUS.md](PROJECT_STATUS.md) | What is done, test results, limitations |
| [TODO.md](TODO.md) | Prioritized backlog |
| [CONTINUATION.md](CONTINUATION.md) | How to continue development |

## License

MIT
