# FLIP RADAR HTTP API contract (v1)

Source of truth for request/response bodies: `backend/flipradar/schemas.py` (Pydantic models named below).
All endpoints except `GET /health` live under `/api/v1` and require
`Authorization: Bearer <token>` when `FLIPRADAR_REQUIRE_AUTH=true` (default).  Errors use FastAPI's
standard shape `{"detail": "..."}` (or a list for validation errors, status 422).

Money = integer centavos (`*_cents`), percentages = integer basis points (`*_bp`, 1600 = 16 %),
datetimes = ISO-8601 UTC strings.

| Method | Path | Request | Response |
|---|---|---|---|
| GET | `/health` (no auth) | – | `HealthOut` |
| GET | `/api/v1/auth/check` | – | `AuthCheckOut` |
| GET | `/api/v1/sources` | – | `list[SourceOut]` |
| PATCH | `/api/v1/sources/{key}` | `SourceUpdate` | `SourceOut` |
| POST | `/api/v1/sources/{key}/test` | – | `SourceTestOut` |
| GET | `/api/v1/searches` | – | `list[TrackedSearchOut]` |
| POST | `/api/v1/searches` | `TrackedSearchIn` | `TrackedSearchOut` (201) |
| PATCH | `/api/v1/searches/{id}` | `TrackedSearchUpdate` | `TrackedSearchOut` |
| DELETE | `/api/v1/searches/{id}` | – | `Message` |
| POST | `/api/v1/searches/{id}/run` | – | `CollectResult` |
| POST | `/api/v1/collect` | `CollectRequest` | `CollectResult` |
| GET | `/api/v1/runs?limit=50` | – | `list[CollectionRunOut]` |
| GET | `/api/v1/listings?q=&source=&product_id=&suspicious=&limit=50&offset=0` | – | `Page[ListingOut]` |
| GET | `/api/v1/listings/{id}` | – | `ListingDetailOut` |
| GET | `/api/v1/products?q=&limit=50&offset=0` | – | `Page[ProductOut]` |
| GET | `/api/v1/products/{id}` | – | `ProductDetailOut` |
| GET | `/api/v1/opportunities?status=&q=&min_score=&min_profit_cents=&min_roi_bp=&include_suspicious=false&include_demo=&sort=score\|profit\|roi\|recent&limit=50&offset=0` | – | `Page[OpportunityOut]` |
| GET | `/api/v1/opportunities/{id}` | – | `OpportunityDetailOut` |
| PATCH | `/api/v1/opportunities/{id}` | `OpportunityUpdate` | `OpportunityOut` |
| POST | `/api/v1/opportunities/{id}/buy` | `OpportunityBuyIn` | `InventoryOut` (201) |
| POST | `/api/v1/opportunities/{id}/ai-summary` | – | `AiSummaryOut` |
| POST | `/api/v1/analyze/recompute` | – | `RecomputeResult` |
| POST | `/api/v1/calculator/profit` | `CalculatorIn` | `CalculatorOut` |
| POST | `/api/v1/calculator/import-costs` | `ImportCostIn` | `ImportCostOut` |
| GET | `/api/v1/fee-profiles` | – | `list[FeeProfileOut]` |
| POST | `/api/v1/fee-profiles` | `FeeProfileIn` | `FeeProfileOut` (201) |
| PUT | `/api/v1/fee-profiles/{key}` | `FeeProfileIn` | `FeeProfileOut` |
| DELETE | `/api/v1/fee-profiles/{key}` (non-builtin only) | – | `Message` |
| GET | `/api/v1/inventory?status=&q=&limit=100&offset=0` | – | `Page[InventoryOut]` |
| POST | `/api/v1/inventory` | `InventoryIn` | `InventoryOut` (201) |
| GET | `/api/v1/inventory/{id}` | – | `InventoryOut` |
| PATCH | `/api/v1/inventory/{id}` | `InventoryUpdate` | `InventoryOut` |
| DELETE | `/api/v1/inventory/{id}` | – | `Message` |
| POST | `/api/v1/inventory/{id}/sales` | `SaleIn` | `SaleOut` (201) |
| GET | `/api/v1/sales?limit=100&offset=0` | – | `Page[SaleOut]` |
| DELETE | `/api/v1/sales/{id}` (restores quantity) | – | `Message` |
| GET | `/api/v1/analytics/summary?days=30` | – | `AnalyticsSummaryOut` |
| GET | `/api/v1/analytics/timeseries?days=90&bucket=week` | – | `TimeseriesOut` |
| GET | `/api/v1/alerts` | – | `list[AlertRuleOut]` |
| POST | `/api/v1/alerts` | `AlertRuleIn` | `AlertRuleOut` (201) |
| PATCH | `/api/v1/alerts/{id}` | partial `AlertRuleIn` | `AlertRuleOut` |
| DELETE | `/api/v1/alerts/{id}` | – | `Message` |
| GET | `/api/v1/notifications?unread_only=false&since_id=0&limit=50` | – | `list[NotificationOut]` (newest first) |
| POST | `/api/v1/notifications/{id}/read` | – | `Message` |
| POST | `/api/v1/notifications/read-all` | – | `Message` |
| POST | `/api/v1/import/csv` (multipart field `file`) | CSV | `ImportResult` |
| POST | `/api/v1/check-url` | `UrlCheckIn` | `UrlCheckOut` |
| GET | `/api/v1/settings` | – | `SettingsOut` |
| PUT | `/api/v1/settings` | `SettingsUpdate` | `SettingsOut` |
| GET | `/api/v1/fx/latest` | – | `FxOut` (404 if none) |

## CSV import format

Header row required.  Columns (case-insensitive): `title` (required), `price` (required, pesos, e.g.
`12999.00`), `currency` (default MXN), `url`, `condition`, `shipping`, `source` (default `manual`),
`external_id` (default: hash of url/title), `brand`, `model`, `gtin`, `seller`, `location`, `sold_quantity`,
`available_quantity`, `observed_at` (ISO date).  Max 5 MB / 20 000 rows.

Amounts: in `,`-delimited files use `.` for decimals (`12999.00`, `$12,999`, `1,234.50`); in
`;`-delimited files (decimal-comma spreadsheets) `1.234,50` / `12999,00` are also accepted.  Ambiguous
values (`12,50` in a `,` file, `1,234` in a `;` file), amounts above 10^12 centavos and cells longer than
2,000 characters reject only their row (reported in `errors`).

Notifications: `since_id=0` returns the newest `limit` rows; `since_id=N>0` returns the OLDEST `limit`
rows with id > N (listed newest first), so a poller never skips notifications.

## Web dashboard

`GET /` serves the single-page dashboard (static HTML/JS, no external CDN).  The dashboard asks for
the API token and keeps it in `sessionStorage`.
