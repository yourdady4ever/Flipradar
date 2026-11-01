# Data sources for the Mexican resale market (researched 2026-11-01)

This document records which data sources FLIP RADAR uses, which it deliberately does **not** use, and
why. Every claim was researched on 2026-11-01. The confidence column shows how well it was verified.

**Research limits (read this first).** The research sandbox could not reach most Mexican retailer
domains or `api.mercadolibre.com` directly, because its network policy blocked them. Evidence therefore
comes from official documentation pages reached through search, official SDK and help pages, and
dated reports by independent developers (GitHub issues and PRs). The shared web-search budget ran out
partway through, so some entries are marked **UNVERIFIED** and come from prior knowledge. Re-verify
anything marked *low* before relying on it. `TODO.md` lists the re-verification tasks.

Legend: **Status** = `implemented` (code exists and is tested with mocked responses),
`manual` (supported through CSV import or the manual calculator only), `not used`.

## 1. Sources FLIP RADAR integrates

| Source | Status | Auth | Cost | Limits | What it provides | Confidence |
|---|---|---|---|---|---|---|
| **Mercado Libre México: catalog API** (`/products/search`, `/products/{id}/items`, `/users/{id}`, `/sites/MLM/domain_discovery/search`, `/sites/MLM/listing_prices`) | implemented (`collectors/mercadolibre.py`) | Your own free ML DevCenter app: `client_id` + `client_secret`, using an `authorization_code`/`refresh_token` grant or the undocumented but community-verified `client_credentials` grant. Requires a ML account with validated holder data. | Free | No public number; quotas are per Client ID and per endpoint. Handle 429. | Per-seller prices for **catalog** products (new items with GTIN or brand+model), seller reputation, category prediction, and exact commission per category and listing type | medium |
| **eBay Browse API** (`item_summary/search`) | implemented (`collectors/ebay.py`) | Your own free eBay Developers Program production keyset, using an OAuth `client_credentials` application token | Free | 5,000 Browse calls/day by default; 1,000 token requests/day; token lives 7,200 s | **Active** US listings that ship to Mexico (`deliveryCountry:MX`), in USD, with shipping to MX. Used for cross-border arbitrage. Opportunities include the estimated Mexican courier import duties; items that would need a formal import are not proposed. | high |
| **Banco de México SIE API**, series `SF43718` (FIX USD/MXN) | implemented (`collectors/banxico.py`) | Free 64-character token (header `Bmx-Token`) | Free | 80 queries/min and 40,000/day for "oportuno"; FLIP RADAR calls it at most twice a day | Official FIX rate, which is the basis for customs value conversion | high |
| **Frankfurter** (blended reference rates) | implemented (FX fallback, v1 endpoint) | none | Free | No published number; heavy requests get 503 with `Retry-After` | USD/MXN, labeled *estimate*. **v1 is frozen and deprecated** (it sends a `Deprecation` header dated 2026-05-18); v2 is `GET /v2/rates?base=USD&quotes=MXN`. FLIP RADAR rejects FX quotes older than 7 days. | medium |
| **ExchangeRate-API open access** (`open.er-api.com/v6/latest/USD`) | implemented (second FX fallback) | none | Free; attribution "Rates By Exchange Rate API" required | Recommended at most one call per 24 h | USD/MXN, labeled *estimate* | high |
| **RSS/Atom deal feeds** chosen by the operator (`FLIPRADAR_RSS_FEEDS`) | implemented (`collectors/rss.py`) | none | Free | Poll every 15–30 min or slower | Deal titles and links, plus the price **only when it is unambiguous**. Items are skipped when the price could be misread: space-grouped numbers next to model numbers ("Xbox 360 500 pesos"), coupon, discount or cashback amounts, or several competing amounts. Installment ("12 MSI de $749") and free-shipping-threshold amounts are ignored. | n/a (no feed is enabled by default) |
| **schema.org JSON-LD on a single product page the user pastes** | implemented (`collectors/jsonld.py`) | none | Free | One page per user action | Product name, price, currency, condition and GTIN from structured data. Respects `robots.txt`, has an SSRF guard, and refuses sites whose terms forbid automation (Facebook, Amazon, Mercado Libre). | n/a |
| **CSV import** | implemented (`services/imports.py`) | none | Free | 5 MB / 20,000 rows per file | Anything you collected by hand: Facebook Marketplace, store visits, Walmart, Liverpool, Coppel and others | n/a |

### Mercado Libre details (most important source)

* **The public keyword search `GET /sites/MLM/search` is closed to third-party apps in 2026.** It
  returns `403 PA_UNAUTHORIZED_RESULT_FROM_POLICIES` (blocked by PolicyAgent) with or without a token.
  Independent developers reported this for MLM (2026-10-06), MLA, MLB and MCO (Jul–Oct 2026). The
  official page still documents the endpoint, but FLIP RADAR does not use it.
* The catalog endpoints still work for many apps with a bearer token, but access is **per app**: one
  MLB app got 200, while one MLA app got a PolicyAgent 403. FLIP RADAR detects the PolicyAgent 403 and
  reports a clear message. When that happens, use CSV import.
* Coverage is limited to **catalog products**, which are mostly new items listed against a catalog
  page. Free-form used listings are not reachable through the API.
* Refresh tokens are **single-use**. After a refresh, store the new refresh token. FLIP RADAR logs a
  warning and keeps the newest one in memory.
* `/items?ids=` and `/users?ids=` were deprecated in favor of `/items/bulk` and `/users/bulk`, with a
  migration deadline of 2026-10-25. From 2026-08-30, Mercado Libre and Mercado Pago apps must be
  separate apps.
* `sold_quantity` and `available_quantity` are visible only to the owner. That is why FLIP RADAR never
  claims Mercado Libre sales statistics.
* **Developer terms:** API content may be used only to support use of the ML site for your app's users.
  Storing ML content to build services that compete with ML is restricted. FLIP RADAR is a personal
  analysis tool: keep its database private and do not redistribute collected prices.
* The HTML storefront (`listado.mercadolibre.com.mx`) sits behind anti-bot proof-of-work challenges.
  Bypassing them could conflict with Mexican federal law (Código Penal Federal arts. 211 bis). **FLIP
  RADAR does not scrape it.**

## 2. Sources evaluated and NOT integrated

| Source | Reason | Confidence |
|---|---|---|
| Amazon Product Advertising API 5.0 | Retired 2026-05-15, replaced by the Creators API | high |
| Amazon Creators API (MX) | Requires an approved MX Associates account with **at least 10 qualifying sales in the past 30 days**. A new user does not qualify. The license also restricts caching and side-by-side price display. | medium |
| Amazon SP-API (MX marketplace `A1AM78C64UM0Y8`) | Requires a **Professional seller plan**, which is a recurring cost (see the cost table). It is meant for a seller's own operations. The developer fee announced for 2026 was cancelled in May 2026. A good future optional connector for users who already sell on Amazon. | medium |
| Keepa API | Paid only (about EUR 49/month, third-party figure). No free tier. | medium |
| camelcamelcamel | Does not cover Amazon Mexico | high |
| eBay Marketplace Insights (sold prices) | Limited release; requires eBay business approval | high |
| Scraping amazon.com.mx, ebay.com, aliexpress.com, facebook.com, mercadolibre.com.mx | Prohibited by their terms and/or protected by anti-bot measures; official APIs exist where possible | medium |
| Facebook Marketplace | No read API. Automated collection is prohibited without Meta's written permission. Use manual entry or CSV import. | medium |
| Walmart MX / Bodega Aurrera / Sam's / Costco MX / Liverpool / Coppel / Elektra / Claro Shop | No public price API. The only APIs are seller APIs for a seller's own catalog, whose terms forbid reusing the data. Robots and terms could not be read from the sandbox (UNVERIFIED). Manual or CSV only. | low–medium |
| Segundamano, Vivanuncios, OLX MX, Linio MX, Kichink, Cuponatic | Closed, pivoted, or no API (UNVERIFIED, prior knowledge) | low |
| AliExpress Affiliate API | Probably usable with the user's own app key (`ship_to_country=MX`). The signing details and rate limits were not verified, so it was **not implemented** rather than guessed. | low |
| Google Custom Search JSON API | Reportedly closed to new customers in 2025, with existing keys ending 2027-01-01 (UNVERIFIED) | low |
| Google Shopping | No buyer-side API. The Content/Merchant API is seller-side only. | medium |
| SerpApi and similar SERP APIs | Tiny free quota and Google ToS/legal risk (UNVERIFIED details). Not implemented. | low |
| PROFECO "Quién es Quién en los Precios" | Official free open data, licensed CC-BY-4.0 (datos.gob.mx record). It lags 5–9 weeks and ships as one RAR5 archive per year: 2025 is 85 MB compressed and 4.2 GB of CSV, with a 15-column lowercase header. It covers 30 states and 242 chains, including the "Electrodomésticos" catalogue (TVs, appliances); phones and consoles are unverified. These are shelf prices of new goods, so they are useful only as a reference for retail prices. Importing it is in the TODO list. The Buen Fin 2026 electronics tracker has no API. | medium |
| INEGI indicators API | Free token, but it provides macro indices (INPC), not product prices | medium |
| UPCitemdb / Open Products Facts / Icecat / Wikidata | Possible future normalization helpers (GTIN to brand/model). None provides prices. Not needed for v1. | low–medium |

## 3. Fee, tax and import parameters used by the calculator

The default numbers ship in `backend/flipradar/services/fees.py` (fee profiles) and
`backend/flipradar/finance/import_costs.py` (import rules). Each one is stored with `source_url`
and `as_of`, and **you can edit all of them** through the API, the dashboard or the Android app.
Several are marked low confidence because the official pages could not be fetched directly.

### Mercado Libre México (seller side)
* Commission: Clásica (`gold_special`) about 8–17 %, Premium (`gold_pro`) about 11–21.5 %, depending
  on category (help page 870 as of 2026-10-28). Commissions were cut by up to 2.5 pp (Clásica) and
  5 pp (Premium) on 2026-04-22. *Low confidence.* Use `/sites/MLM/listing_prices` (implemented) for
  the exact per-category value.
* Per-unit cost under $299: the legacy $25/$30/$37 fixed fee was reportedly replaced on 2026-04-06
  by a per-unit "costo de los envíos". That cost depends on weight, price band and reputation, and it
  applies even when the buyer pays shipping. *Low confidence.*
* Seller shipping cost when free shipping applies, for new items with green reputation up to 0.3 kg:
  $25 (<$99) / $32 / $35 / $52.40 ($299–498.99) / $65.50 ($499+). For 1–2 kg: $35 / $40 / $41 /
  $67.60 / $84.50. Used items cost more. *Low confidence* (ayuda 40538/40545/49978).
* Free shipping is mandatory for new items priced at **$299 MXN** or more. Minimum item price is $35.
  *Medium confidence.*

### Platform tax withholdings (persona física selling through platforms)
* ISR withholding: **2.5 %** of the price excluding IVA, **for fiscal year 2026 only**. This is set by
  the Ley de Ingresos de la Federación 2026, Art. 25 fr. VI (DOF 07-11-2025), which replaces the 1 %
  of LISR Art. 113-A fr. III. Re-check when the LIF 2027 is published; the statutory fallback is 1 %.
  Without an RFC the rate is **20 %** (LISR 113-C). *High confidence.* In 2026, personas morales are
  also withheld 2.5 %.
* IVA withholding: **8 %** of the price excluding IVA (50 % of the 16 % IVA, LIVA Art. 18-J fr. II a).
  Without an RFC the rate is **16 %**. *Medium confidence.*
* Mercado Libre's seller-center page "¿Qué son y cómo se aplican las retenciones?" (quoted in a
  secondary source, read 2026-10-28) says withholdings apply to **new** items. **Used items sold by
  sellers with an RFC have 0 % withholding.** FLIP RADAR therefore ships separate fee profiles for
  used items. *Medium confidence.*
* Whether withholdings are a final cost depends on your tax regime. The calculator lets you toggle
  this. **This is not tax advice.**

### Amazon México (for manual calculations)
* Referral fee: electronics 10 %; video games, home and media 15 %; toys 8 % or 15 % depending on
  price; overall range 8–20 %. The fee is charged on the total sale price, including shipping, IVA
  included, with a minimum of $8 MXN per item (as of about Feb 2026). *Medium confidence.*
* Professional plan: $600 MXN/month (high tier) or $75 MXN/month (low tier), effective 2026-02-17,
  with 12 free months for new sellers. *High confidence.*

### Import into Mexico by courier ("mensajería y paquetería", RGCE regla 3.7.35)
* From the US or Canada (USMCA): customs value **≤ USD 50** is exempt from IGI and IVA, though DTA
  may still apply. **USD 50–117**: 17 % global rate. **> USD 117**: 19 % global rate (from
  2025-08-15). *High confidence.*
* From non-FTA countries (for example China): a **33.5 %** global rate since 2025-08-15. It was 19 %
  from 2025-01-01. *High confidence.*
* Customs converts with the Banxico FIX rate published in the DOF, which is why Banxico is the
  preferred FX source.

## 4. Recurring-cost inventory

| Item | Required? | Cost |
|---|---|---|
| FLIP RADAR software, SQLite, Python, Android app | required | **$0** (open source, runs locally) |
| Mercado Libre DevCenter app | optional | $0 |
| eBay Developers Program | optional | $0 |
| Banxico SIE token | optional | $0 |
| ntfy.sh push notifications (or self-hosted ntfy) | optional | $0 on the public free tier or self-hosted |
| Telegram bot notifications | optional | $0 |
| Ollama local LLM for AI summaries | optional | $0 (uses your own hardware) |
| OpenAI-compatible hosted LLM | optional, off by default | **Paid** when pointed at a commercial provider |
| GitHub Actions CI | optional | $0 for public repos; private repos get 2,000 free minutes/month on GitHub Free |
| Server hosting (if not your own PC) | optional | A $0 option is running it on your own PC or LAN. A VPS is a recurring cost you choose. |
| Amazon Professional seller plan (only to use SP-API in the future) | not used | $75–600 MXN/month |
| Keepa / SerpApi / scraping APIs | not used | paid |

No paid dependency is required or enabled by default.
