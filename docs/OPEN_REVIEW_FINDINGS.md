# Open findings from the second (regression) review — 2026-11-01

These came from a second adversarial review of the fixes made after commit `47c5c91`. The session
ended before they could be fixed. **CONFIRMED** means an independent verifier reproduced it.
**PENDING** means the verifier had not finished, so reproduce it first. Fix each one with a
regression test, following `CONTINUATION.md` §2.

| # | Severity | Verdict | Finding | Location |
|---|---|---|---|---|
| 1 | high | PENDING | Buying an imported (eBay) opportunity leaves the estimated import duties out of the inventory cost | `backend/flipradar/services/inventory.py:157` |
| 2 | high | PENDING | Band/case words in the title now turn real Apple Watch, Galaxy Watch and AirPods listings into accessories | `backend/flipradar/normalize/extract.py:669` |
| 3 | high | PENDING | Switch OLED/Lite titles with any word outside the SUFFIX_FILLERS allowlist lose their model | `backend/flipradar/normalize/extract.py:915` |
| 4 | high | PENDING | _named_extra_item marks consoles as bundles when a condition description follows 'con'/'y' | `backend/flipradar/normalize/extract.py:834` |
| 5 | medium | PENDING | An opportunity marked bought by mistake can never be reopened from either client (the server allows it) | `backend/flipradar/web/static/app.js:263` |
| 6 | medium | PENDING | Standard Joy-Con named after 'Nintendo Switch OLED' now makes the console an accessory | `backend/flipradar/normalize/extract.py:884` |
| 7 | medium | PENDING | SUFFIX_CHANGERS 6-word window clears iPhone models on common words like 'mini detalle' | `backend/flipradar/normalize/vocab.py:1817` |
| 8 | medium | PENDING | Stale-buy expiry followed by re-sighting resets WATCHING/SEEN opportunities to NEW | `backend/flipradar/services/analysis.py:425` |
| 9 | medium | CONFIRMED | Rate limiting can be skipped by adding any fake Authorization: Bearer header | `backend/flipradar/security.py:309` |
| 10 | medium | CONFIRMED | CORS preflight for the CSV import gets 401, so cross-origin CSV uploads always fail | `backend/flipradar/security.py:369` |
| 11 | medium | CONFIRMED | RSS: a short price followed by any 3-digit spec ('100%', '100W', '300Mbps', '256GB') is treated as ambiguous and the item is skipped | `backend/flipradar/collectors/rss.py:75` |
| 12 | medium | CONFIRMED | RSS: the brand 'MSI' before 'por'/'de'/'x' is read as 'meses sin intereses' and the real price is dropped | `backend/flipradar/collectors/rss.py:89` |
| 13 | medium | PENDING | Recompute after a collection fails with 'database is locked' whenever another connection commits during a product's analysis | `backend/flipradar/services/analysis.py:465` |
| 14 | medium | CONFIRMED | New global stale-buy sweep expires watched/seen opportunities, and re-seeing them resets them to NEW, losing the user's decision | `backend/flipradar/services/analysis.py:425` |
| 15 | medium | PENDING | Cumulative cost allocation produces negative cost basis and inflated profit on items whose cost was edited after a sale (previous release allowed it) | `backend/flipradar/services/inventory.py:291` |
| 16 | low | PENDING | The since_id forward cursor still skips notifications after ids are reused, but the docs and docstring say it never skips | `backend/flipradar/api/routers/notifications.py:39` |
| 17 | low | PENDING | Per-token rate limiting lets an over-limit client still verify tokens (rate-limit check runs after verify_token) | `backend/flipradar/api/deps.py:75` |
| 18 | low | PENDING | check-url: 'too many redirects' on a public site is now a 400 saying the URL is non-public | `backend/flipradar/api/routers/collect.py:63` |
| 19 | low | PENDING | PROJECT_STATUS.md claims that the fixes made false | `PROJECT_STATUS.md:37` |
| 20 | low | PENDING | API_DOCUMENTATION.md CSV example '$12,999' is now rejected in ';'-delimited files | `API_DOCUMENTATION.md:56` |
| 21 | low | PENDING | Import duties computed on the item price alone when eBay shipping is missing; the duty bracket can be wrong | `backend/flipradar/services/analysis.py:151` |
| 22 | low | CONFIRMED | read_capped now fails on non-standard Content-Encoding values that httpx and browsers treat as identity | `backend/flipradar/collectors/netguard.py:131` |
| 23 | low | CONFIRMED | AI validator rejects accurate summaries: qualified risk phrases and loss words that flip the sign of unrelated numbers | `backend/flipradar/ai/narrative.py:47` |
| 24 | low | CONFIRMED | New check-url refusal log writes user-supplied user:password@ credentials to the server log | `backend/flipradar/api/routers/collect.py:64` |
| 25 | low | PENDING | Notification forward cursor ('never skips') still skips notifications because SQLite reuses notification ids | `backend/flipradar/api/routers/notifications.py:39` |
| 26 | low | PENDING | Collection runs are now committed as 'running' before the network call and can stay 'running' forever | `backend/flipradar/services/collection.py:108` |

## 1. Buying an imported (eBay) opportunity leaves the estimated import duties out of the inventory cost

* **Severity:** high — **Verdict:** PENDING
* **Location:** `backend/flipradar/services/inventory.py:157`
* **Problem:** The import-duty fix made the engine count estimated Mexican duties as an acquisition cost (engine/opportunities.py:245: buy_total_cents = price + shipping + import_costs). The duties are also shown in net_profit and the explanation. create_from_opportunity was not updated: when the client sends no costs, which is the default in both clients, it sets unit_cost = listing.price_mxn_cents and extra_costs = shipping_mxn_cents(listing) only, so the duties are dropped. The Android buy dialog shows d.summary.buyTotalCents (duties included) as the 'Current total buy cost estimate', and the dashboard hint says 'Empty = listing shipping'. Both then create an item that costs less than the opportunity said. The cost-lock fix (409 on unit/extra cost edits once an item has sales) means the user cannot correct the cost after the first sale.
* **Failure scenario:** Verified with a probe (/tmp/claude-0/review2/test_probe_import_buy.py). The setup is an eBay listing at USD 200 + USD 20 shipping with the FX rate at 18.2142, resold on Mercado Libre. The opportunity's buy_total_cents is 476847 and its net_profit is 317553. POST /opportunities/{id}/buy with {} creates an inventory item with total_cost_cents 400712, so the 76135 cents of duties are missing. Selling at the expected price then books about $761 more realized profit than the engine predicted. Analytics inventory_cost and realized profit are wrong, and PATCHing unit_cost_cents after a sale returns 409.
* **Suggested fix:** In create_from_opportunity, add the opportunity's import costs when extra_costs_cents is not given. Use the 'import_costs' acquisition line in opp.cost_breakdown_json, or opp.buy_total_cents minus price minus shipping. Make the dashboard and Android hints say that duties are included.

## 2. Band/case words in the title now turn real Apple Watch, Galaxy Watch and AirPods listings into accessories

* **Severity:** high — **Verdict:** PENDING
* **Location:** `backend/flipradar/normalize/extract.py:669`
* **Problem:** The new spans loop in detect_accessory (lines 669-680) runs when a title has no storage and no device noun. It now treats WEAK accessory words after the model name as accessory evidence. Only the narrow _PART_DESCRIPTORS list escapes. That condition is always true for watches and AirPods, which have no storage. The band ('correa', 'pulso') and the charging case ('estuche') are part of the official product name, so they are almost always in the title. The listing gets is_accessory=True and canonical_key=None. It drops out of every price cluster and is never a buy candidate. The old code (47c5c91) clustered all of these titles correctly.
* **Failure scenario:** Checked with the old and new code: 'Apple Watch Series 9 45mm Correa Loop Deportiva' was apple|apple watch series 9|45mm and is now None (accessory strap). Same for Apple's own ML wording 'Apple Watch Series 9 (GPS) - Caja de aluminio ... 45 mm - Correa deportiva ... M/L', 'Apple Watch SE 2da Gen 44mm GPS Correa Deportiva', 'Apple Watch Ultra 2 49mm Titanio Correa Ocean', 'Apple Watch Serie 7 45mm Pulso Deportivo' and 'Samsung Galaxy Watch6 Classic 47mm Bluetooth Correa Hibrida'. The Amazon MX title 'Apple AirPods 4 Audífonos Inalámbricos, ..., Estuche de Carga USB-C' and 'Apple AirPods Pro (2.ª generación) - Estuche de carga MagSafe (USB-C)' are now accessory (case). Almost all smartwatch recall is lost.
* **Suggested fix:** Do not count the standard in-box part of the identified family after the model name: strap/band for smartwatches, case/estuche for earbuds. A simple way is a per-category allowlist of standard-part accessory types that the spans loop skips. Or only apply the weak-word rule when the weak word comes right after the model name and nothing descriptive (mm, gps, colour, material) sits between them.

## 3. Switch OLED/Lite titles with any word outside the SUFFIX_FILLERS allowlist lose their model

* **Severity:** high — **Verdict:** PENDING
* **Location:** `backend/flipradar/normalize/extract.py:915`
* **Problem:** When a console title has no storage and no 'consola' (lines 914-930), every word after the model name must be in the closed allowlist vocab.SUFFIX_FILLERS (vocab.py:660), or else the model is set to None. Switch OLED and Lite are FIXED_STORAGE_MODELS, so device_storage is always False and the rule applies to almost every Switch listing written without 'Consola'. Ordinary Mexican condition, logistics and colour words are not in the list.
* **Failure scenario:** All of these were clustered at 47c5c91 and are now None ('console name followed by other words ...'): 'Nintendo Switch Lite Semi Nuevo', 'Nintendo Switch OLED Blanco Bien Cuidado', 'Nintendo Switch Lite Turquesa Sin Rayones', 'Nintendo Switch OLED Usado Buenas Condiciones', 'Nintendo Switch OLED Usado 10/10', 'Nintendo Switch Lite Turquesa 9/10', 'Nintendo Switch OLED Blanco CDMX', 'Nintendo Switch OLED Blanco Entrega Inmediata', 'Nintendo Switch OLED Blanco Precio Negociable', 'Nintendo Switch OLED Seminuevo Envio Hoy', 'Nintendo Switch Lite Azul Celeste', 'Nintendo Switch Lite Verde Azulado'. The most common used console in MX loses a large share of its comparables and buy candidates.
* **Suggested fix:** Turn the logic around: clear the model only when the suffix contains positive game or accessory evidence (a known game or franchise lexicon, accessory words, 'juego', 'edicion <name>'). Do not require every word to be in a closed allowlist. At minimum, allow condition and logistics vocabulary, n/10 scores and plural/derived colour forms.

## 4. _named_extra_item marks consoles as bundles when a condition description follows 'con'/'y'

* **Severity:** high — **Verdict:** PENDING
* **Location:** `backend/flipradar/normalize/extract.py:834`
* **Problem:** _named_extra_item (lines 834-841, applied at line 932) sets bundle=True for any console title where a word after 'con'/'y'/'mas'/'e'/'+' fails _known_token. It runs even when the title has 'Consola' and storage. Condition and paperwork phrases are not known tokens, and neither are plural colours. Bundles are excluded from comparables and from buy candidates, so these units disappear from the analysis even though the key is correct.
* **Failure scenario:** Checked with the old and new code (all bun=False before, bun=True now): 'Consola PS5 Slim Digital 1TB con detalle estetico', 'Consola PS5 Slim Digital 1TB con muy poco uso', 'Consola PS5 Slim 1TB Disco con ligero rayon', 'Consola PS5 Slim Digital 1TB con todo lo original', 'Consola PS5 Slim Digital 1TB con Ticket de compra', 'Consola PS5 Slim Digital 1TB Nueva Sellada y Facturada', 'Consola PS5 Slim Digital 1TB con base vertical', 'Nintendo Switch Lite con estuche y mica de cristal', 'Nintendo Switch Lite con un rayon', and 'Nintendo Switch Oled Blanco Con Joycons Blancos' (the standard white Joy-Con, flagged because 'blancos' is not recognised as a colour).
* **Suggested fix:** Only treat the words after 'con'/'y' as an extra item when they carry positive evidence: a model hit, a game/franchise lexicon word, 'juego(s)', or a known non-standard accessory. Unknown words should not count. Also accept plural and feminine colour forms in _is_color_word.

## 5. An opportunity marked bought by mistake can never be reopened from either client (the server allows it)

* **Severity:** medium — **Verdict:** PENDING
* **Location:** `backend/flipradar/web/static/app.js:263`
* **Problem:** The server-side fix only refuses a status change on a bought opportunity while an inventory item references it (routers/opportunities.py:131). Both clients were changed to hide Watch/Dismiss/Restore for every bought opportunity: dashboard oppStatusActions() returns [] (app.js:263-264), and Android hides the actions when o.status == "bought" (OpportunityDetailActivity.kt:79). Deleting the inventory item, the natural undo, does not reset the opportunity (inventory.py:208 delete_item). Bought is also in NEVER_EXPIRE (analysis.py:71), so a recompute never changes it. The dashboard note for bought ('the purchase is tracked in Inventory') is then false.
* **Failure scenario:** Verified with a probe. The user taps 'Mark bought' by mistake, then deletes the new inventory item. GET /opportunities/{id} still returns status 'bought'. The row stays in the default 'Open (new, seen, watching, bought)' list forever, never expires, and is excluded from analytics open potential profit. Neither client offers any action to restore it, although PATCH {status:'seen'} now returns 200. Before the fixes, both clients offered Watch/Dismiss.
* **Suggested fix:** Choose one: (a) in delete_item, set a bought opportunity with no remaining linked items back to 'seen'; or (b) expose whether the opportunity is linked (e.g. an inventory_item_count field) and hide the actions only while it is linked.

## 6. Standard Joy-Con named after 'Nintendo Switch OLED' now makes the console an accessory

* **Severity:** medium — **Verdict:** PENDING
* **Location:** `backend/flipradar/normalize/extract.py:884`
* **Problem:** The new hit-based rule (lines 884-900) marks the listing as an accessory when any accessory-type model hit (here the joycon rule, type controller) comes after model_end and the title has no storage or device noun. Switch OLED is fixed-storage, so device_storage is always False. The Joy-Con are the standard in-box controllers, and the official product name includes them ('OLED Model w/ White Joy-Con', 'Joy-Con Neón'). is_bundle's _WITH_RE explicitly exempts accessory_type 'controller' for this reason, but this new rule does not, so the two components disagree.
* **Failure scenario:** 'Nintendo Switch OLED Joy-Con Neon', 'Nintendo Switch OLED Joy-Con Rojo y Azul Neón' and 'Nintendo Switch OLED Blanco Joy Con Blancos', plus the eBay title 'Nintendo Switch OLED Model White Joy-Con'. All were nintendo|switch oled at 47c5c91 and are now is_accessory=True with key None ('joycon (controller) named after the device: accessory listing').
* **Suggested fix:** Skip hits whose accessory_type is the family's standard in-box accessory (controller/joycon for Switch, as _WITH_RE already does). Or require the accessory hit to come right after the model name, with no colour or edition words between them.

## 7. SUFFIX_CHANGERS 6-word window clears iPhone models on common words like 'mini detalle'

* **Severity:** medium — **Verdict:** PENDING
* **Location:** `backend/flipradar/normalize/vocab.py:1817`
* **Problem:** _unconsumed_suffix scans up to 6 words after an iPhone hit. If it finds 'mini', 'max', 'pro', 'plus' or 'pm' that the built model did not use, the hit loses its model (find_model_hits, line 1864-1869). In Mexican Spanish 'mini detalle' / 'mini rayon' (a tiny cosmetic defect) is a frequent title phrase. The scan does not check whether the changer word is part of the model name or of a later phrase.
* **Failure scenario:** 'iPhone 13 128gb Mini Detalle En Pantalla' and 'iPhone 14 128GB Azul Mini Rayon' were apple|iphone 13|128gb and apple|iphone 14|128gb at 47c5c91. Both are now None (''mini' after iphone 13 does not belong to that model name'). The same happens for 'iPhone 15 128gb negro Max bateria'. Ordinary used iPhones are dropped from the clusters.
* **Suggested fix:** Only treat a changer as ambiguous when it comes directly after the model number, or after storage or colour tokens only. Ignore it when it is followed by a defect or condition noun ('mini detalle/rayon/golpe'), or stop the window at the first condition word.

## 8. Stale-buy expiry followed by re-sighting resets WATCHING/SEEN opportunities to NEW

* **Severity:** medium — **Verdict:** PENDING
* **Location:** `backend/flipradar/services/analysis.py:425`
* **Problem:** _expire_stale_buys (lines 425-437) and the engine's new 'stale' skip (opportunities.py:73-75) expire every NEW/SEEN/WATCHING opportunity whose buy listing was not seen for buy_max_age_days (default 3). It correctly never touches bought or dismissed rows, and it does run for products that were not re-analysed. But when the listing is seen again, analyze_one_product (lines 406-407) moves EXPIRED to NEW without any memory of the earlier status. The module docstring promises 'seen/watching ... keep their status'. Before the fix a still-active listing was never expired just for not being re-sighted, so this path did not exist. A listing that drops out of a collector's result pages for 3 days, a short collector outage, or a manual/CSV (e.g. Facebook Marketplace) entry now silently loses the user's decision. Demo data also disappears 3 days after seeding, because demo listings are never re-seen.
* **Failure scenario:** Reproduced on a temp DB (script /tmp/claude-0/review2/stale.py): seed_demo at T creates 10 NEW opportunities. One is set to 'watching'. recompute(now=T+4d) expires all 10 (watching -> expired). The listing's last_seen_at is then set to T+4d (collector sees it again) and recompute(now=T+4d) runs: the row comes back as 'new', not 'watching'.
* **Suggested fix:** Keep the pre-expiry status when expiring for staleness, e.g. a separate expired_reason or previous_status column. On revival, restore SEEN/WATCHING instead of setting NEW. Alternatively, leave WATCHING rows out of staleness expiry and only mark them stale. Consider exempting manual-source listings or giving them a longer window.

## 9. Rate limiting can be skipped by adding any fake Authorization: Bearer header

* **Severity:** medium — **Verdict:** CONFIRMED
* **Location:** `backend/flipradar/security.py:309`
* **Problem:** RateLimitMiddleware no longer counts a request under /api/v1/ when it carries any bearer credentials. It leaves the counting to require_token (deps.py:_rate_limit). FastAPI reads and parses the request body before it resolves dependencies (fastapi/routing.py get_request_handler parses JSON, then calls solve_dependencies). So a request whose JSON body fails to parse returns 422 without require_token ever running. A request to an unknown /api/v1/ path (404/405) never reaches require_token either. Neither kind is counted anywhere. Before 47c5c91 every such request was counted per IP. This regression undoes the stated aim of slowing down runaway clients and floods.
* **Failure scenario:** rate_limit_per_minute=5. Sending POST /api/v1/calculator/profit with 'Authorization: Bearer junk' and a 240 KB JSON body that is malformed at the end returns 422 every time, with no 429 even after many requests. The same request without the Authorization header returns 429 from the 6th request on. GET /api/v1/does-not-exist with 'Bearer junk' also never gets 429. Reproduced with /tmp/claude-0/review2/scripts/mw_probe.py and mw_probe3.py.
* **Suggested fix:** Count deferred requests in the middleware once the response is known. For example, wrap send: if the status is not 2xx/3xx and require_token never ran (set a scope flag in _rate_limit), call limiter.hit('ip:'+ip) and answer 429 when over the limit. Alternatively keep a looser per-IP limit in the middleware for every request and add the per-token limit on top of it.
* **Verifier evidence:** Code path: at backend/flipradar/security.py:309-313, RateLimitMiddleware returns early for any request under defer_prefix ("/api/v1/", set in main.py:197 when require_auth=True) that has a non-empty "Bearer <x>" header. bearer_credentials() only checks the header's syntax. The early return happens before self.limiter.hit(). The only other place that counts these requests is backend/flipradar/api/deps.py:39-49 (_rate_limit), which require_token calls (deps.py:78 and :81). There is one more counting point, authorize_upload in main.py:168-177, but it runs only for the CSV path when the body exceeds 256 KB. Any request that fails before FastAPI resolves dependencies is therefore never counted:
(a) A JSON decode error. FastAPI 0.162 get_request_handler parses the body before solve_dependencies and raises RequestValidationError, so the response is 422.
(b) An unknown path (404) or a wrong method (405). The router answers before any route dependency runs.
A body that is valid JSON but has the wrong schema IS still counted, because dependencies run before the body is validated.

Reproduced with /tmp/claude-0/review2/security-0/probe.py: rate_limit_per_minute=5, client 203.0.113.7, 12 requests each, current HEAD.
- Malformed 240 KB JSON with "Bearer junk": 422 x12, never 429.
- The same request with no auth header: 422 x5, then 429 x7.
- GET /api/v1/does-not-exist with "Bearer junk": 404 x12. Without the header: 404 x5, then 429.
- DELETE /api/v1/calculator/profit with "Bearer junk": 

## 10. CORS preflight for the CSV import gets 401, so cross-origin CSV uploads always fail

* **Severity:** medium — **Verdict:** CONFIRMED
* **Location:** `backend/flipradar/security.py:369`
* **Problem:** For paths whose limit is above the default (/api/v1/import/csv), BodySizeLimitMiddleware calls authorize(scope) whenever Content-Length is missing. A CORS preflight (OPTIONS) never has a body or an Authorization header, so authorize_upload returns False and the middleware answers 401. CORSMiddleware is added first, which makes it the innermost layer, so it never sees the preflight. The 401 has no Access-Control-* headers. Other API paths preflight normally.
* **Failure scenario:** FLIPRADAR_CORS_ORIGINS=https://dash.example.com and require_auth=true. The browser sends OPTIONS /api/v1/import/csv with Origin and Access-Control-Request-Method: POST and gets 401 {"detail":"A valid bearer token is required for this upload"} with no CORS headers. The browser then blocks the multipart upload, even with a valid token. OPTIONS /api/v1/calculator/profit returns 200 with CORS headers. Reproduced with /tmp/claude-0/review2/scripts/mw_probe2.py.
* **Suggested fix:** Only run the upload pre-authorization for methods that carry a body (POST/PUT/PATCH), and skip OPTIONS, HEAD and GET. Alternatively add CORSMiddleware after BodySizeLimitMiddleware so it sits outside it.
* **Verifier evidence:** The claim holds, and the bug is a regression from the fixes.

How the code behaves:
- backend/flipradar/main.py:181-191 adds CORSMiddleware first and BodySizeLimitMiddleware second. The last middleware added is the outermost, so BodySizeLimit runs before CORS.
- In backend/flipradar/security.py:367-372, for CSV_IMPORT_PATH (limit is above the default), the middleware calls authorize_upload when there is no Content-Length (`declared is None`). It does not check the HTTP method.
- A browser preflight carries no Content-Length and no Authorization header. With require_auth=true, authorize_upload (main.py:168-177) therefore returns False.
- The middleware then answers 401 with only a www-authenticate header. CORSMiddleware never sees the request, so the response has no Access-Control-* headers.
- LocalRequestGuard is not installed when require_auth=true. RateLimit only counts the preflight. Nothing else answers the preflight first.

Reproduction: /tmp/claude-0/review2/security-1/preflight_probe.py sends a raw ASGI request (no TestClient, so no added headers) with exactly the browser preflight headers: Origin, Access-Control-Request-Method: POST, Access-Control-Request-Headers: authorization. Settings were cors_origins=https://dash.example.com and require_auth=True.
- HEAD, OPTIONS /api/v1/import/csv: 401 `{"detail":"A valid bearer token is required for this upload"}`, no access-control headers.
- HEAD, OPTIONS /api/v1/calculator/profit: 200 with access-control-allow-origin and al

## 11. RSS: a short price followed by any 3-digit spec ('100%', '100W', '300Mbps', '256GB') is treated as ambiguous and the item is skipped

* **Severity:** medium — **Verdict:** CONFIRMED
* **Location:** `backend/flipradar/collectors/rss.py:75`
* **Problem:** _GROUP_FOLLOWS = r"[  ]\d{3}(?!\d)" only checks that no digit follows the 3 digits. So any '$NNN' price (3 digits or fewer, no cents) followed by a space and a 3-digit spec is marked pending and then reported as an 'ambiguous space-separated amount'. This happens even when a unit is attached to the digits ('100W', '100%', '300Mbps', '128GB'), where thousands grouping is impossible. Before the fix, all of these titles produced a price.
* **Failure scenario:** extract_price() returns None ('ambiguous space-separated amount in title') for 'Cable USB-C $99 100W', 'Audífonos $499 100% originales', 'Router TP-Link $499 300Mbps', 'Memoria microSD Samsung $199 256GB', 'Teclado mecánico $799 104 teclas' and 'Smartwatch Xiaomi $899 120 modos deportivos'. At 47c5c91 they returned 9900, 49900, 49900, 19900, 79900 and 89900 cents. Reproduced with /tmp/claude-0/review2/scripts/rss_titles.py.
* **Suggested fix:** Only treat the following group as a possible thousands group when it is a standalone number: require (?![\w%]) after the 3 digits (and optionally a currency word or end of text after it). For example: r"[  ]\d{3}(?![\d\w%.,])".
* **Verifier evidence:** The defect is in backend/flipradar/collectors/rss.py:75: `_GROUP_FOLLOWS = re.compile(r"[  ]\d{3}(?!\d)")`. In `_scan` (around lines 160-168), a `_DOLLAR` or `_CODE_FIRST` match is put in `pending` when it has no cents, is all digits, has at most 3 digits and `_GROUP_FOLLOWS.match(text, end)` succeeds. A pending amount only clears when a `_CODE_AFTER` ("... pesos"/"MXN") match overlaps it. If none does, `problems` gets `_AMBIGUOUS_SPACED`. `extract_price` then returns None for that text right away, so the description is never tried.

The lookahead only rules out a 4th digit. Units stuck to the number ("100W", "100%", "300Mbps", "256GB", "144Hz") still count as a possible thousands group, even though "$99,100 W" or "$199,256 GB" makes no sense as a price.

Reproduced with /tmp/claude-0/review2/security-2/rss_group_follows.py at HEAD and at 47c5c91 (the base was extracted with git archive). At HEAD these titles all return None with "ambiguous space-separated amount in title": 'Cable USB-C $99 100W', 'Audífonos $499 100% originales', 'Router TP-Link $499 300Mbps', 'Memoria microSD Samsung $199 256GB', 'Monitor $999 144Hz', 'Teclado mecánico $799 104 teclas', 'Smartwatch Xiaomi $899 120 modos deportivos'. At 47c5c91 they returned 9900, 49900, 49900, 19900, 99900, 79900 and 89900. extract_price('Memoria microSD Samsung $199 256GB', 'Precio: $199 en Amazon') was 19900 and is now None, because the title problem stops the description fallback.

Points against the claim:
(1) This is p

## 12. RSS: the brand 'MSI' before 'por'/'de'/'x' is read as 'meses sin intereses' and the real price is dropped

* **Severity:** medium — **Verdict:** CONFIRMED
* **Location:** `backend/flipradar/collectors/rss.py:89`
* **Problem:** _INSTALLMENT_BEFORE matches a bare 'msi' followed by de/por/x right before the amount. It does not require an installment count, as in '12 MSI'. MSI is a common laptop, monitor and GPU brand in Mexican deal titles, so an item price written right after the brand is classified as an installment and ignored. The result is 'no MXN price found'.
* **Failure scenario:** extract_price('Laptop Gamer MSI por $15,999'), extract_price('Monitor MSI de $3,499 en Amazon') and extract_price('Tarjeta de video MSI x $8,999') all return None ('no MXN price found (ignored installment amounts)'). At 47c5c91 they returned 1599900, 349900 and 899900 cents.
* **Suggested fix:** Require a count before the installment word, e.g. r"\b\d{1,2}\s*(?:msi|meses...)\s+(?:de|por|x)$", or at least for 'msi' itself. 'pagos de' and 'mensualidades de' can keep their current form.
* **Verifier evidence:** Code: backend/flipradar/collectors/rss.py:88-91 defines `_INSTALLMENT_BEFORE = r"\b(?:msi|meses(...)?|mensualidades|pagos(...)?|quincenas|semanas)\s+(?:de|por|x)$"`. Nothing requires a count (a digit) before "msi". `_amount_role` (line 134) runs it on the accent-stripped, lower-cased 40 characters before each amount. When it matches, `_scan` adds "installment" to `ignored` and drops the amount (`if role in ("installment","threshold"): ignored.add(role); continue`). At 47c5c91 this logic did not exist, so the regression comes from the installment fix.

I reproduced it with /tmp/claude-0/review2/security-3/repro_msi.py, repro_msi2.py and repro_msi3.py, running each against HEAD and against a `git archive` export of 47c5c91.

Dropped price (HEAD -> 47c5c91):
- 'Laptop Gamer MSI por $15,999' -> None 'no MXN price found (ignored installment amounts)' (was 1599900)
- 'Monitor MSI de $3,499 en Amazon' -> None (was 349900)
- 'Tarjeta de video MSI x $8,999' -> None (was 899900)
- 'Laptop MSI x $15,999 - Amazon' -> None (was 1599900)

Worse, a wrong price is accepted as the single unambiguous price. At 47c5c91 each of these was correctly skipped as having 2 prices:
- 'PC Gamer MSI por $25,999 + audifonos de regalo valor $499' -> 49900 'single price in title'. A $25,999 PC is priced at $499, a false mega-deal.
- 'Laptop MSI de $18,999 incluye mochila $799' -> 79900
- 'Monitor MSI de $3,499 (antes $4,999)' -> 499900, the old price.
- title 'Laptop Gamer MSI por $15,999' with description 

## 13. Recompute after a collection fails with 'database is locked' whenever another connection commits during a product's analysis

* **Severity:** medium — **Verdict:** PENDING
* **Location:** `backend/flipradar/services/analysis.py:465`
* **Problem:** This fix is incomplete. The comment in `_close_run` (collection.py:155-159) names the hazard: a transaction that reads first cannot later upgrade to a writer, because SQLite WAL returns SQLITE_BUSY at once and the busy_timeout does not help. `_close_run` avoids it by writing first. `recompute` does not. It runs right after `_close_run` commits (collection.py:236), so no transaction is open. Each product's `session.begin_nested()` therefore becomes the outermost transaction: the SAVEPOINT opens a deferred transaction, `analyze_one_product` reads listings, opportunities and observations, and the final flush (UPDATE listings risk_flags / opportunities) has to upgrade to a write. If any other connection committed after that first read, the flush fails immediately. The product is logged as 'analysis failed', its opportunities stay stale and its alerts are never created. The fix also made this more likely: two collections (scheduler plus manual /collect) used to be serialized by the write lock and now interleave, so one collection's `_open_run`/`_close_run` commits land inside the other's recompute. A UI write such as the auto 'seen' PATCH can do the same.
* **Failure scenario:** Reproduced with /tmp/claude-0/review2/race_recompute.py on a real file DB using init_engine/SessionLocal and the seeded demo data with prices changed. A second session commits one AppSetting row while `analyze_one_product` is running, simulating PATCH /opportunities/{id}, PUT /settings or another collection's `_open_run`. Result: `sqlalchemy.exc.OperationalError: database is locked [SQL: UPDATE listings SET risk_flags_json=?]`, 'analysis failed for product N' for all 6 products, and RecomputeResult reports 0 created and 0 updated. After a real collection, the newly ingested price drops produce no opportunities or notifications until another collection touches the same product.
* **Suggested fix:** Make the first statement of each per-product transaction a write, or open recompute's transaction with BEGIN IMMEDIATE (the pysqlite recipe: isolation_level=None plus a 'begin' event that emits BEGIN IMMEDIATE). The busy_timeout then applies and the product waits instead of failing. Alternatively, retry a product once on OperationalError.

## 14. New global stale-buy sweep expires watched/seen opportunities, and re-seeing them resets them to NEW, losing the user's decision

* **Severity:** medium — **Verdict:** CONFIRMED
* **Location:** `backend/flipradar/services/analysis.py:425`
* **Problem:** `_expire_stale_buys` (new, called on every recompute at line 477) sets every open opportunity to `expired` when its buy listing's last_seen_at is older than min(buy_max_age_days, comparable_max_age_days). buy_max_age_days is a new setting with a default of 3, and the sweep covers products the recompute did not analyse. `watching` and `seen` are not in NEVER_EXPIRE. When the listing is seen again, the update path at line 406-407 turns EXPIRED into NEW, so the user's 'watching'/'seen' choice is silently lost. The API still accepts tracked searches with interval_minutes up to 10080 (7 days; schemas.py:103). Manual and CSV listings are never re-seen at all. For those workflows every opportunity cycles to expired 3 days after the run and back to NEW at the next run, which contradicts the module docstring's promise that user decisions are preserved.
* **Failure scenario:** Reproduced with /tmp/claude-0/review2/stale_watch.py. Mark an opportunity 'watching' and set its listing's last_seen_at to 4 days ago (a weekly tracked search). A recompute of an unrelated product returns opportunities_expired=1 and the watched opportunity is 'expired'. Set last_seen_at to now (the weekly search sees it again) and recompute its product: the status is now 'new' and the watch is gone. The same happens to every opportunity created from a CSV import 3 days after the import.
* **Suggested fix:** Do not reset user statuses: remember the pre-expiry status, or exclude watching/seen from the stale sweep, or restore EXPIRED to the previous user status instead of NEW. Also keep buy_max_age_days consistent with tracked-search intervals (validate it, or derive the window from the search interval) and treat manual/CSV sources separately.
* **Verifier evidence:** I could not refute the claim. I reproduced it at HEAD and compared against the 47c5c91 snapshot (from `git archive`).

What changed in the fixes:
- `engine/opportunities.py:73-75` (`_skip_reason`) now returns "stale" for any buy listing whose `observed_at` (= `last_seen_at`) is older than `min(buy_max_age_days, comparable_max_age_days)`. `buy_max_age_days` is new and defaults to 3 (`settings_store.py:45`).
- `services/analysis.py:425-437`: the new `_expire_stale_buys` runs on every recompute (`analysis.py:477`). It sets EXPIRED on every opportunity whose status is not in `NEVER_EXPIRE`, across all products, including ones the recompute did not analyse. `NEVER_EXPIRE` (line 71) holds only bought, dismissed and expired, so `watching` and `seen` get expired.
- The existing re-entry path at `analysis.py:406-407` sets `if opp.status == EXPIRED: opp.status = NEW`. It does not remember the earlier status, so the user's watching/seen decision is gone for good.
- The API's default list filter (`routers/opportunities.py:35`, new/seen/watching/bought) hides expired rows, so a watched item disappears from view in the meantime.
- `ARCHITECTURE.md:111` still says "User statuses (seen/watching/dismissed/bought) survive recomputes". The module docstring says the same.

Before the fixes, the reset to NEW also existed. But a watched opportunity only expired when it stopped being a candidate (price or comparables changed). Not being re-seen for a few days did not expire it. That made the reset 

## 15. Cumulative cost allocation produces negative cost basis and inflated profit on items whose cost was edited after a sale (previous release allowed it)

* **Severity:** medium — **Verdict:** PENDING
* **Location:** `backend/flipradar/services/inventory.py:291`
* **Problem:** Each new sale is now costed as `cumulative_cost_cents(item, sold_after) - sum(existing sales' cost_basis)`, and the inventory value as `total - allocated`. That invariant only holds if the cost never changed after a sale. The previous release had no cost lock (the lock is part of this fix), so existing databases contain items whose unit_cost/extra_costs were corrected after units were sold. For those items the subtraction goes negative. The new Sale is stored with a negative cost_basis_cents and a profit larger than the sale price. The same overstated figure feeds analytics realized profit and ROI, and a later write-off of such an item books a gain. The old per-sale average-cost formula never produced a negative basis.
* **Failure scenario:** Reproduced with /tmp/claude-0/review2/legacy_cost.py. Create an item with quantity 3 and unit_cost 1,000,000 (a $10,000 typo) and sell 1 unit (basis 1,000,000). Correct unit_cost_cents to 100,000, as the old PATCH allowed (simulated with SQL). Sell 1 more unit at 150,000: the sale is stored with cost_basis_cents = -800,000 and profit_cents = 950,000 on a $1,500 sale, and /analytics/summary realized profit is inflated by the same amount.
* **Suggested fix:** Clamp the per-sale basis at 0, or detect inconsistent legacy allocation (allocated_before > cumulative_cost_cents(item, units_sold_before)) and fall back to cost_basis_for(item, units). Apply the same guard in remaining_cost_cents/written_off_cost_cents, or add a one-off migration that rebases the existing sales.

## 16. The since_id forward cursor still skips notifications after ids are reused, but the docs and docstring say it never skips

* **Severity:** low — **Verdict:** PENDING
* **Location:** `backend/flipradar/api/routers/notifications.py:39`
* **Problem:** The fix made since_id>0 return the OLDEST page with id > since_id. The router docstring and docs/API_CONTRACT.md:76-77 promise 'a poller ... never skips notifications'. notifications has no sqlite_autoincrement, so SQLite reuses rowids once the newest rows are deleted (clear-demo), or after a reset database. The Android client was changed to since_id=0 for exactly this reason (NotificationPoller.kt:39-41), and TODO.md lists the missing autoincrement. Any other poller that follows the documented contract still misses notifications.
* **Failure scenario:** Verified. Notifications 1-5 exist and the poller's cursor is 5. clear-demo deletes ids 3-5, and three new alerts get ids 3, 4 and 5. GET /notifications?unread_only=true&since_id=5 returns [], so the new alerts are never delivered to that poller.
* **Suggested fix:** Add sqlite_autoincrement=True to the notifications table (needs a migration for existing databases), or document since_id as unsafe across deletes and recommend the created_at cursor that the Android app uses.

## 17. Per-token rate limiting lets an over-limit client still verify tokens (rate-limit check runs after verify_token)

* **Severity:** low — **Verdict:** PENDING
* **Location:** `backend/flipradar/api/deps.py:75`
* **Problem:** After the fix, the middleware defers every /api/v1 request that carries a Bearer header, and require_token calls verify_token (SHA-256 plus a DB SELECT, and a commit for a valid token) before _rate_limit. Invalid tokens are counted per IP only after the database work is done. A valid token is counted against its own bucket, not the IP's. So an IP that is over its limit still gets a token-validity answer: 200 for the right token, 429 for wrong ones. Floods with bogus tokens still cost a DB lookup per request, and before the fix the middleware rejected them before any DB work. SECURITY.md:17 presents per-IP limiting as the brute-force control. 256-bit tokens make guessing impractical, so the main impact is the lost abuse protection.
* **Failure scenario:** Verified with rate_limit_per_minute=3 (/tmp/claude-0/review2/test_probe_ratelimit.py). Six wrong tokens get [401, 401, 401, 429, 429, 429]. The next request from the same IP with the correct token gets 200, so the IP limit does not stop the guess from being checked.
* **Suggested fix:** Before verify_token, check the per-IP bucket without consuming it, and refuse with 429 when it is exhausted. Count a hit per IP only for invalid tokens and per token for valid ones.

## 18. check-url: 'too many redirects' on a public site is now a 400 saying the URL is non-public

* **Severity:** low — **Verdict:** PENDING
* **Location:** `backend/flipradar/api/routers/collect.py:63`
* **Problem:** netguard.guarded_get raises UnsafeUrlError('too many redirects (limit 3)') (netguard.py:493). jsonld.check_url re-raises every UnsafeUrlError, and the new router branch turns all of them into HTTP 400 with UNSAFE_URL_MESSAGE ('it (or a redirect) points to a non-public or disallowed address. Only public http(s) sites on allowed ports, without user:password@, can be checked'). Before the fix this case returned 200 with found=false and the real reason. A legitimate public product link with more than three redirects (shortener or affiliate link, then http to https, then a locale redirect) is now reported as a non-public address in the dashboard.
* **Failure scenario:** A user pastes a public retailer short link that redirects four times (bit.ly, then http, then https, then www, then the product page). The dashboard shows 'URL refused: ... non-public or disallowed address', which wrongly suggests an SSRF problem. The user gets no hint that the redirect limit was the cause.
* **Suggested fix:** Raise a plain CollectorError for the redirect limit, or catch it in the router before the generic UnsafeUrlError branch, so it keeps a specific message.

## 19. PROJECT_STATUS.md claims that the fixes made false

* **Severity:** low — **Verdict:** PENDING
* **Location:** `PROJECT_STATUS.md:37`
* **Problem:** PROJECT_STATUS.md was not updated with the fixes, and several claims are now false. Line 37 says 'per-IP limiter', but authenticated requests are now limited per token (security.py RateLimitMiddleware defer_prefix, deps._rate_limit). Line 17 says '868 passed', but the suite now has 1326 passing tests. Line 22 says the Android JVM unit tests are '46 passed', but there are now 77 @Test methods (new Pager, TaskRunner, TokenBinding, NotificationPoller, InventoryUpdate and OpportunityStatusFilter tests). Line 19's 'realized profit equals predicted profit to the centavo' no longer holds for imported opportunities (see the import-duty finding).
* **Failure scenario:** A reader of PROJECT_STATUS.md concludes that rate limiting is per IP and that the verification numbers are current. Running pytest prints 1326 passed, and reverse-proxy and multi-token deployments behave differently from what is described.
* **Suggested fix:** Update lines 17, 19, 22 and 37 to the current numbers and behaviour.

## 20. API_DOCUMENTATION.md CSV example '$12,999' is now rejected in ';'-delimited files

* **Severity:** low — **Verdict:** PENDING
* **Location:** `API_DOCUMENTATION.md:56`
* **Problem:** The CSV table still says price is 'Pesos, e.g. `12999.00` or `$12,999`' and does not mention the new delimiter-dependent grammar. After the fix, parse_amount(decimal_comma=True) treats '12,999' in a ';'-delimited file as ambiguous, and the row is rejected (imports._money_cell). Before the fix, commas were stripped and the value was accepted. Only docs/API_CONTRACT.md documents the new rule.
* **Failure scenario:** A user follows API_DOCUMENTATION.md and uploads a ';'-separated sheet with prices written as '$12,999'. Every row is rejected with 'price has an ambiguous decimal separator', although the documentation shows that exact format as valid.
* **Suggested fix:** Add the delimiter rule from docs/API_CONTRACT.md to the CSV section of API_DOCUMENTATION.md (or to its generator template), or qualify the '$12,999' example as valid only in ','-delimited files.

## 21. Import duties computed on the item price alone when eBay shipping is missing; the duty bracket can be wrong

* **Severity:** low — **Verdict:** PENDING
* **Location:** `backend/flipradar/services/analysis.py:151`
* **Problem:** import_estimate uses shipping_usd = int(listing.shipping_cents or 0). When eBay gives no shipping quote to MX (shippingOptions missing, or the shipping currency differs from the price currency), the customs value is the item price only. That value also picks the courier bracket (0% up to USD 50, 17% up to 117, 19% above). Meanwhile the engine prices shipping with unknown_shipping_assumption_cents (default None, i.e. 0) and still makes the listing a buy candidate. The note mentions it, but the fix stays incomplete: duties are understated exactly where the bracket edges matter, and the listing is still ranked. eBay listings with no fx_rate are correctly never candidates (price_mxn_cents is None, so listing_view returns None). Non-USD currencies such as CAD correctly fall through to import_costs_unknown.
* **Failure scenario:** An eBay US listing at USD 45.00 with shipping_cents=None gives a customs value of USD 45 and duties of MXN 0 under courier_usmca. The real shipment (USD 45 + about USD 25 shipping = USD 70) pays 17%. The opportunity shows inflated profit and ROI.
* **Suggested fix:** When an import has no shipping quote, either skip it (like import_costs_unknown) or add a configured international-shipping assumption in USD to the customs value before choosing the bracket, and use the same assumption for the MXN shipping line.

## 22. read_capped now fails on non-standard Content-Encoding values that httpx and browsers treat as identity

* **Severity:** low — **Verdict:** CONFIRMED
* **Location:** `backend/flipradar/collectors/netguard.py:131`
* **Problem:** The new _content_decoder raises CollectorError for any coding other than gzip, x-gzip, deflate or identity. Before the fix, read_capped used aiter_bytes(), and httpx skips unknown codings (as browsers do) and passes the body through unchanged. Some misconfigured servers send 'Content-Encoding: none' or a charset such as 'UTF-8'. RSS feeds and JSON-LD product pages from those servers used to work and now fail.
* **Failure scenario:** A feed answers 200 with 'Content-Encoding: none' (or 'UTF-8') and a plain RSS body. Now: CollectorError "unsupported content encoding 'none'", the feed lands in last_errors and its items are lost (check-url reports 'page fetch failed'). At 47c5c91 the body was read normally. Reproduced with /tmp/claude-0/review2/scripts/decomp_probe.py.
* **Suggested fix:** Return None (identity) for codings that are not compressed codings, and raise only for real compressions that are not supported (br, zstd, compress) or for stacked gzip/deflate.
* **Verifier evidence:** The code confirms the claim and I reproduced it. `_content_decoder` (backend/flipradar/collectors/netguard.py:116-131) drops only "identity" and raises `CollectorError("unsupported content encoding ...")` at line 131 for any other coding that is not gzip, x-gzip or deflate. `read_capped` calls it at line 142 before it reads anything, so the error fires whatever the body is.

At 47c5c91, `read_capped` used `response.aiter_bytes()`. httpx 0.28.1 (the version in .venv) builds its decoder in `_models.py:_get_content_decoder` (lines 704-720), which does `try: SUPPORTED_DECODERS[value] ... except KeyError: continue`. So httpx skipped unknown codings and returned the body unchanged.

Reproduction: /tmp/claude-0/review2/security-4/probe2.py uses a real TCP server on 127.0.0.1 that sends exact raw headers, a 200 response and a plain RSS body. It runs the old `read_capped` (taken from `git show 47c5c91`) against the current one:
- No header, or "identity": old and new both return 211 bytes, identical to the body.
- "none", "UTF-8", "binary", "gzip-not-really": the old version returns 211 bytes, identical to the body. The new one raises `CollectorError: unsupported content encoding 'none'` (or 'utf-8', and so on).
- `RssDealsCollector.fetch_feed`, run through MockTransport with a real async stream: identity parses the feed; "none" gives `CollectorError: unsupported content encoding 'none'`. In `_fetch_all` that error lands in `last_errors[url]` (rss.py:519) and the feed's items are lost

## 23. AI validator rejects accurate summaries: qualified risk phrases and loss words that flip the sign of unrelated numbers

* **Severity:** low — **Verdict:** CONFIRMED
* **Location:** `backend/flipradar/ai/narrative.py:47`
* **Problem:** (1) _RISK_DENIAL_RE matches 'no hay riesgos' / 'sin riesgos' even when the phrase is qualified ('no hay riesgos críticos'). It does this whenever risk_level is not 'none', including 'info', which almost every candidate has (e.g. shipping_unknown). (2) _signed_numbers (line 150) treats any loss word ('perder', 'pérdida', 'negativo') between the previous number and this one as a minus sign. So in 'para no perder dinero no vendas por menos de $10,000.00', the positive break-even or buy figure is read as -10000 and rejected. Neither problem stores a bad summary, but a large share of correct AI summaries are discarded.
* **Failure scenario:** Facts have risk_level='info' (shipping_unknown), net_profit '$1,500.00' and buy_price '$10,000.00'. 'No hay riesgos críticos; solo falta el costo de envío. Ganancia neta $1,500.00 (ROI 15.00%).' is rejected with ['no hay riesgos']. 'Comprar a $10,000.00 y vender en $12,999.00 deja $1,500.00 de ganancia; para no perder dinero no vendas por menos de $10,000.00.' is rejected with ['10,000.00']. Reproduced with /tmp/claude-0/review2/scripts/ai_probe.py.
* **Suggested fix:** Reject a risk denial only when no qualifier follows it (críticos, graves, de réplica, ...), or only when risk_level is 'warning' or 'critical'. Count a loss word as a sign only when it sits right before the number (e.g. within 'pérdida (neta)? de $'), and ignore it after a negation ('no perder', 'sin pérdida').
* **Verifier evidence:** I reproduced both halves of the claim against real facts produced by build_facts, taken from opportunity 1 in /tmp/claude-0/review2/db/flipradar-test.db. That opportunity has risk_level='info' with one flag, price_statistical_outlier. 8 of the 10 opportunities in that database are 'info', so the risk-denial check applies to most candidates.

(1) Risk denial, backend/flipradar/ai/narrative.py:47-51 and 235-240. The pattern ends at `riesgos?\b`, so it does not check what follows, and risk_denials() fires for every level except None and 'none', including 'info'. Results:
- "No hay riesgos críticos; solo un aviso informativo: ..." is rejected with ['no hay riesgos'].
- "Sin riesgos graves. ..." is rejected with ['sin riesgos'].
Both sentences are accurate for an info-only opportunity.

(2) Loss word flips the sign, narrative.py:150. `_LOSS_RE.search(text, previous_end, match.start())` makes the next number negative if a loss word appears anywhere after the previous number, even when the word is negated. Results:
- "Para no perder dinero, el precio de venta mínimo es $11,824.71." gives _signed_numbers → [('11,824.71', Decimal('-11824.71'))], rejected with ['11,824.71'] (break_even_sale is +$11,824.71).
- "Ganancia neta de $3,058.12 sin pérdidas; ROI de 30.83%." is rejected with ['30.83'].
- "Comprar a $9,920.00; para evitar pérdidas no pagues más de $11,798.29." is rejected with ['11,798.29'] (max_buy_price).
- Control: the same figures without loss or denial words pass as (True, 

## 24. New check-url refusal log writes user-supplied user:password@ credentials to the server log

* **Severity:** low — **Verdict:** CONFIRMED
* **Location:** `backend/flipradar/api/routers/collect.py:64`
* **Problem:** The fix logs every refused URL with log.info('check-url refused %s: %s', url[:200], exc). UrlCheckIn.url is a pydantic HttpUrl, which accepts userinfo. One refusal reason is exactly 'URLs containing credentials (user:password@) are not allowed', so the credentials the guard refuses are written in clear text to the INFO log. Before the fix nothing was logged. This adds a new hole in log redaction, which the notify filter only covers for notification URLs.
* **Failure scenario:** POST /api/v1/collect/check-url {"url":"https://admin:S3cret@shop.example/p/1"} returns the generic 400. The server log then contains 'check-url refused https://admin:S3cret@shop.example/p/1: URLs containing credentials ...'.
* **Suggested fix:** Remove the userinfo before logging, e.g. rebuild the URL from httpx.URL(url).copy_with(username=None, password=None), or log only scheme://host[:port].
* **Verifier evidence:** backend/flipradar/api/routers/collect.py:63-65 (new since 47c5c91):
    except netguard.UnsafeUrlError as exc:
        log.info("check-url refused %s: %s", url[:200], exc)
`url = str(body.url)`. UrlCheckIn.url is a pydantic HttpUrl (schemas.py:646), and it keeps the userinfo. jsonld.check_url calls netguard.parse_and_validate_url, which raises UnsafeUrlError("URLs containing credentials (user:password@) are not allowed") at netguard.py:263-264. So the one input the guard refuses for carrying credentials is the input whose credentials get logged in clear text.

Nothing blocks the record:
- log_level defaults to "INFO" (config.py:57), and main.py:219 and cli.py:309 run basicConfig at that level.
- The only redaction filter, notify._SecretUrlFilter, is attached to the "httpx" logger (notify.py:66), so it never sees records from flipradar.api.routers.collect.
- The root logger and its handlers have no filters (I checked at runtime).

At 47c5c91 this branch did not exist. UnsafeUrlError (a CollectorError subclass) went to the CollectorError handler, which only echoed the URL back to the caller who sent it. Nothing was logged, so this is a new leak introduced by the fix.

Reproduced. I wrote a pytest file at /tmp/claude-0/review2/security-6/test_cred_log.py and ran it with the repo conftest. It got the generic HTTP 400, and caplog captured:
INFO flipradar.api.routers.collect:collect.py:64 check-url refused https://admin:S3cret@shop.example/p/1: URLs containing credentials (user:pas

## 25. Notification forward cursor ('never skips') still skips notifications because SQLite reuses notification ids

* **Severity:** low — **Verdict:** PENDING
* **Location:** `backend/flipradar/api/routers/notifications.py:39`
* **Problem:** The fix made `since_id > 0` a forward cursor and documents that a poller passing the highest id seen 'never skips notifications'. `Notification.id` (models.py:365) is a plain INTEGER PRIMARY KEY without AUTOINCREMENT, so SQLite gives a new row max(rowid)+1. When the newest notifications are deleted, their ids are reused below the poller's cursor. `demo.clear_demo` deletes demo notifications this way (services/demo.py:129), and so does the ON DELETE CASCADE from opportunities. Those new notifications are never returned to a `since_id` poller. The Android client avoids since_id for exactly this reason, but the API contract now advertises the cursor as safe.
* **Failure scenario:** Reproduced with /tmp/claude-0/review2/notif_reuse.py. With an alert rule, seed the demo: notification ids are 1..9 and the poller's cursor is 9. Clear the demo, then a new real notification is created with id 2. GET /notifications?since_id=9 returns [] and the new notification is never delivered to the poller.
* **Suggested fix:** Declare the notifications table with `sqlite_autoincrement=True` (with a migration for existing databases) so ids are never reused, or base the cursor on (created_at, id). Until then, do not document the cursor as gap-free.

## 26. Collection runs are now committed as 'running' before the network call and can stay 'running' forever

* **Severity:** low — **Verdict:** PENDING
* **Location:** `backend/flipradar/services/collection.py:108`
* **Problem:** `_open_run` now commits the CollectionRun row with status='running' before `_fetch`, and nothing ever cleans up such rows. Two paths leave them unfinished. (1) `_fetch` catches only `Exception`, so a CancelledError (scheduler.stop() at shutdown or restart while a tracked search awaits the network) skips `_close_run`/`_abort_run`. (2) `_abort_run` swallows a second failed commit after a lock timeout (line 182-186). Before the fix the run row was uncommitted and simply disappeared on failure. Now /runs history and the dashboard show a permanently 'running' run, and Source.last_status/last_run_at are never updated for it.
* **Failure scenario:** A tracked search starts on the scheduler and the server is restarted or redeployed while the collector awaits the API. The task is cancelled, `tick`'s finally closes the session, and the collection_runs row keeps status='running' with finished_at NULL forever. /api/v1/runs lists it as running on every later visit.
* **Suggested fix:** Catch BaseException/CancelledError around the fetch-and-close step and finish the run (in a fresh session if needed), and/or at startup mark any 'running' rows older than a threshold as error ('interrupted').
