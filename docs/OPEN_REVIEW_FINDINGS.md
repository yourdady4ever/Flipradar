# Second-round (regression) review findings: resolved

A second adversarial review of the round-1 fixes (commits after `47c5c91`) produced 26 findings.
All are resolved below. Each fix has a regression test that failed before the fix, in
`backend/tests/test_fix_round2_*.py`, `backend/tests/test_integration_followups.py`,
`backend/tests/web_smoke/round2.test.mjs` and the Android JVM tests.

| # | Severity | Finding | Location | Resolution |
|---|---|---|---|---|
| 1 | high | Buying an imported (eBay) opportunity leaves the estimated import duties out of the inventory cost | `backend/flipradar/services/inventory.py:157` | FIXED: inventory cost includes estimated import duties |
| 2 | high | Band/case words in the title now turn real Apple Watch, Galaxy Watch and AirPods listings into accessories | `backend/flipradar/normalize/extract.py:669` | FIXED: a band or charging case does not make a described device an accessory (one dash-separated AirPods title stays an accessory on purpose) |
| 3 | high | Switch OLED/Lite titles with any word outside the SUFFIX_FILLERS allowlist lose their model | `backend/flipradar/normalize/extract.py:915` | FIXED: the description/condition word allowlist was extended |
| 4 | high | _named_extra_item marks consoles as bundles when a condition description follows 'con'/'y' | `backend/flipradar/normalize/extract.py:834` | FIXED: descriptive phrases after "con"/"y" are not bundles |
| 5 | medium | An opportunity marked bought by mistake can never be reopened from either client (the server allows it) | `backend/flipradar/web/static/app.js:263` | FIXED: "Restore to new" in the dashboard and Android; the 409 explains how to remove the inventory link |
| 6 | medium | Standard Joy-Con named after 'Nintendo Switch OLED' now makes the console an accessory | `backend/flipradar/normalize/extract.py:884` | PARTLY FIXED: Joy-Con after a colour or model word is part of the OLED; "Switch OLED Joy-Con Neon" stays unclustered on purpose |
| 7 | medium | SUFFIX_CHANGERS 6-word window clears iPhone models on common words like 'mini detalle' | `backend/flipradar/normalize/vocab.py:1817` | FIXED: "mini <defect>" after the storage is an adjective ("<storage> Max bateria" stays unclustered on purpose) |
| 8 | medium | Stale-buy expiry followed by re-sighting resets WATCHING/SEEN opportunities to NEW | `backend/flipradar/services/analysis.py:425` | FIXED (same fix as state#1) |
| 9 | medium | Rate limiting can be skipped by adding any fake Authorization: Bearer header | `backend/flipradar/security.py:309` | FIXED: every request is counted in middleware; invalid tokens are charged to the IP |
| 10 | medium | CORS preflight for the CSV import gets 401, so cross-origin CSV uploads always fail | `backend/flipradar/security.py:369` | FIXED: preflights are answered by CORS before the limiters; the CSV limit applies to POST/PUT/PATCH only |
| 11 | medium | RSS: a short price followed by any 3-digit spec ('100%', '100W', '300Mbps', '256GB') is treated as ambiguous and the item is skipped | `backend/flipradar/collectors/rss.py:75` | FIXED: a known unit after the price (128GB, 100W, 144Hz, 100%) is accepted |
| 12 | medium | RSS: the brand 'MSI' before 'por'/'de'/'x' is read as 'meses sin intereses' and the real price is dropped | `backend/flipradar/collectors/rss.py:89` | FIXED: "MSI" needs a count to mean installments |
| 13 | medium | Recompute after a collection fails with 'database is locked' whenever another connection commits during a product's analysis | `backend/flipradar/services/analysis.py:465` | FIXED: one BEGIN IMMEDIATE transaction per product; the sweep runs in its own transaction |
| 14 | medium | New global stale-buy sweep expires watched/seen opportunities, and re-seeing them resets them to NEW, losing the user's decision | `backend/flipradar/services/analysis.py:425` | FIXED: opportunities.status_before_expiry restores seen/watching when the listing is seen again |
| 15 | medium | Cumulative cost allocation produces negative cost basis and inflated profit on items whose cost was edited after a sale (previous release allowed it) | `backend/flipradar/services/inventory.py:291` | FIXED: average-cost fallback; cost basis is never negative |
| 16 | low | The since_id forward cursor still skips notifications after ids are reused, but the docs and docstring say it never skips | `backend/flipradar/api/routers/notifications.py:39` | FIXED (same fix as state#3); the docs no longer say "never skips" |
| 17 | low | Per-token rate limiting lets an over-limit client still verify tokens (rate-limit check runs after verify_token) | `backend/flipradar/api/deps.py:75` | BY DESIGN, mitigated: a valid token works while its IP is over the limit; malformed tokens cost no database query |
| 18 | low | check-url: 'too many redirects' on a public site is now a 400 saying the URL is non-public | `backend/flipradar/api/routers/collect.py:63` | FIXED: too many redirects is reported as a fetch failure |
| 19 | low | PROJECT_STATUS.md claims that the fixes made false | `PROJECT_STATUS.md:37` | FIXED: PROJECT_STATUS.md corrected |
| 20 | low | API_DOCUMENTATION.md CSV example '$12,999' is now rejected in ';'-delimited files | `API_DOCUMENTATION.md:56` | FIXED: the CSV amount examples are documented per delimiter |
| 21 | low | Import duties computed on the item price alone when eBay shipping is missing; the duty bracket can be wrong | `backend/flipradar/services/analysis.py:151` | FIXED: with no shipping quote, the highest reachable courier rate is used and the note says so |
| 22 | low | read_capped now fails on non-standard Content-Encoding values that httpx and browsers treat as identity | `backend/flipradar/collectors/netguard.py:131` | FIXED: non-compression Content-Encoding values are read as identity |
| 23 | low | AI validator rejects accurate summaries: qualified risk phrases and loss words that flip the sign of unrelated numbers | `backend/flipradar/ai/narrative.py:47` | FIXED: truthfully qualified risk phrases and negated loss words are accepted |
| 24 | low | New check-url refusal log writes user-supplied user:password@ credentials to the server log | `backend/flipradar/api/routers/collect.py:64` | FIXED: userinfo is stripped before logging |
| 25 | low | Notification forward cursor ('never skips') still skips notifications because SQLite reuses notification ids | `backend/flipradar/api/routers/notifications.py:39` | FIXED: notifications use AUTOINCREMENT (older tables are rebuilt at startup) |
| 26 | low | Collection runs are now committed as 'running' before the network call and can stay 'running' forever | `backend/flipradar/services/collection.py:108` | FIXED: interrupted runs are recorded as errors; runs still "running" after 30 min are closed |

Intentionally kept recall losses (precision first) are listed in `TODO.md` under "Recover matching recall safely".
