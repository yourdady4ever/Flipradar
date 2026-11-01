# FLIP RADAR API documentation

> Generated from the OpenAPI schema by `backend/scripts/gen_api_docs.py`. Do not edit the endpoint
> reference by hand; edit the code and regenerate it. Interactive docs (Swagger UI): `http://<server>/api/docs`; OpenAPI JSON: `/api/v1/openapi.json`.

## Basics

* Base URL: `http://<host>:<port>`. Every endpoint except `GET /health`, `GET /` (dashboard) and
  `/static/*` lives under **`/api/v1`**.
* **Authentication:** send `Authorization: Bearer <token>`. Create tokens with
  `python -m flipradar create-token --name <device>`. A missing or invalid token returns `401`.
* **Money** is always an integer number of **centavos** (`*_cents`): `1234567` = $12,345.67 MXN.
  **Percentages** are integer **basis points** (`*_bp`): `1600` = 16 %. Datetimes are ISO-8601 UTC.
* **Errors:** `{"detail": "message"}`. Validation errors (`422`) return a list of field errors.
  `404` = unknown id, `409` = conflict (for example, selling more units than you have in stock),
  `429` = rate limited (see the `Retry-After` header).
* **Pagination:** `limit` (max 200) and `offset`. Paged responses are
  `{"items": [...], "total": N, "limit": L, "offset": O}`.

## Typical workflows

```bash
TOKEN=fr_...; API=http://127.0.0.1:8000/api/v1; H="Authorization: Bearer $TOKEN"

# 1. Collect listings now (only sources you configured are used; others are reported as skipped)
curl -s -X POST $API/collect -H "$H" -H 'Content-Type: application/json' \
     -d '{"query": "nintendo switch oled", "limit": 50}'

# 2. Best opportunities first (suspicious listings excluded by default)
curl -s "$API/opportunities?sort=score&limit=20" -H "$H"

# 3. Full explanation, cost breakdown, comparables and risk flags
curl -s $API/opportunities/1 -H "$H"

# 4. Profit calculator: buy at $4,500, sell at $6,200 on Mercado Libre Clásica
curl -s -X POST $API/calculator/profit -H "$H" -H 'Content-Type: application/json' \
     -d '{"buy_price_cents": 450000, "sale_price_cents": 620000, "fee_profile_key": "ml_clasica", "target_roi_bp": 2000}'

# 5. Bought it -> inventory, then record the sale
curl -s -X POST $API/opportunities/1/buy -H "$H" -H 'Content-Type: application/json' -d '{"quantity": 1}'
curl -s -X POST $API/inventory/1/sales -H "$H" -H 'Content-Type: application/json' \
     -d '{"quantity": 1, "unit_price_cents": 620000, "fee_profile_key": "ml_clasica"}'

# 6. Analytics
curl -s "$API/analytics/summary?days=30" -H "$H"
```

## CSV import format

Upload as multipart field `file` to `POST /api/v1/import/csv` (optional query `source=<key>`).
A header row is required. Columns are case-insensitive:

| Column | Required | Notes |
|---|---|---|
| `title` | yes | Listing title as shown |
| `price` | yes | Pesos, e.g. `12999.00` or `$12,999` |
| `currency` | no | Default `MXN` |
| `url`, `condition`, `shipping`, `brand`, `model`, `gtin`, `seller`, `location` | no | `condition`: new/used/refurbished/for_parts or Spanish equivalents |
| `source` | no | Default `csv_import` |
| `external_id` | no | Default: hash of url/title |
| `sold_quantity`, `available_quantity` | no | Integers, if you observed them |
| `observed_at` | no | ISO date/datetime |

Limits: 5 MB and 20,000 rows per file. Invalid rows are rejected individually with a reason.


## Endpoint reference

### alerts

| Method | Path | Summary | Request body | Response |
|---|---|---|---|---|
| GET | `/api/v1/alerts` | List Rules |  | 200: list[AlertRuleOut] |
| POST | `/api/v1/alerts` | Create Rule | AlertRuleIn | 201: AlertRuleOut |
| DELETE | `/api/v1/alerts/{rule_id}` | Delete Rule |  | 200: Message |
| PATCH | `/api/v1/alerts/{rule_id}` | Update Rule | AlertRuleUpdate | 200: AlertRuleOut |

### analysis

| Method | Path | Summary | Request body | Response |
|---|---|---|---|---|
| POST | `/api/v1/analyze/recompute` | Recompute |  | 200: RecomputeResult |

### analytics

| Method | Path | Summary | Request body | Response |
|---|---|---|---|---|
| GET | `/api/v1/analytics/summary`<br>query: `days` | Summary |  | 200: AnalyticsSummaryOut |
| GET | `/api/v1/analytics/timeseries`<br>query: `days`, `bucket` | Timeseries |  | 200: TimeseriesOut |

### auth

| Method | Path | Summary | Request body | Response |
|---|---|---|---|---|
| GET | `/api/v1/auth/check` | Check |  | 200: AuthCheckOut |

### calculator

| Method | Path | Summary | Request body | Response |
|---|---|---|---|---|
| POST | `/api/v1/calculator/import-costs` | Import Costs | ImportCostIn | 200: ImportCostOut |
| POST | `/api/v1/calculator/profit` | Profit | CalculatorIn | 200: CalculatorOut |

### collection

| Method | Path | Summary | Request body | Response |
|---|---|---|---|---|
| POST | `/api/v1/check-url` | Check Url | UrlCheckIn | 200: UrlCheckOut |
| POST | `/api/v1/collect` | Collect | CollectRequest | 200: CollectResult |
| GET | `/api/v1/runs`<br>query: `limit` | List Runs |  | 200: list[CollectionRunOut] |

### fee profiles

| Method | Path | Summary | Request body | Response |
|---|---|---|---|---|
| GET | `/api/v1/fee-profiles` | List Profiles |  | 200: list[FeeProfileOut] |
| POST | `/api/v1/fee-profiles` | Create Profile | FeeProfileIn | 201: FeeProfileOut |
| DELETE | `/api/v1/fee-profiles/{key}` | Delete Profile |  | 200: Message |
| PUT | `/api/v1/fee-profiles/{key}` | Replace Profile | FeeProfileIn | 200: FeeProfileOut |

### fx

| Method | Path | Summary | Request body | Response |
|---|---|---|---|---|
| GET | `/api/v1/fx/latest` | Latest |  | 200: FxOut |
| POST | `/api/v1/fx/refresh` | Refresh |  | 200: FxOut |

### import

| Method | Path | Summary | Request body | Response |
|---|---|---|---|---|
| POST | `/api/v1/import/csv`<br>query: `source` | Import Csv | multipart/form-data | 200: ImportResult |

### inventory

| Method | Path | Summary | Request body | Response |
|---|---|---|---|---|
| GET | `/api/v1/inventory`<br>query: `status`, `q`, `limit`, `offset` | List Inventory |  | 200: Page_InventoryOut_ |
| POST | `/api/v1/inventory` | Create Item | InventoryIn | 201: InventoryOut |
| DELETE | `/api/v1/inventory/{item_id}` | Delete Item |  | 200: Message |
| GET | `/api/v1/inventory/{item_id}` | Get Item |  | 200: InventoryOut |
| PATCH | `/api/v1/inventory/{item_id}` | Update Item | InventoryUpdate | 200: InventoryOut |
| POST | `/api/v1/inventory/{item_id}/sales` | Record Sale | SaleIn | 201: SaleOut |

### listings

| Method | Path | Summary | Request body | Response |
|---|---|---|---|---|
| GET | `/api/v1/listings`<br>query: `q`, `source`, `product_id`, `suspicious`, `status`, `include_demo`, `limit`, `offset` | List Listings |  | 200: Page_ListingOut_ |
| GET | `/api/v1/listings/{listing_id}` | Get Listing |  | 200: ListingDetailOut |

### meta

| Method | Path | Summary | Request body | Response |
|---|---|---|---|---|
| GET | `/health` | Health |  | 200: HealthOut |

### notifications

| Method | Path | Summary | Request body | Response |
|---|---|---|---|---|
| GET | `/api/v1/notifications`<br>query: `unread_only`, `since_id`, `limit` | List Notifications |  | 200: list[NotificationOut] |
| POST | `/api/v1/notifications/read-all` | Read All |  | 200: Message |
| POST | `/api/v1/notifications/{notification_id}/read` | Read One |  | 200: Message |

### opportunities

| Method | Path | Summary | Request body | Response |
|---|---|---|---|---|
| GET | `/api/v1/opportunities`<br>query: `status`, `q`, `min_score`, `min_profit_cents`, `min_roi_bp`, `include_suspicious`, `include_demo`, `source`, `sort`, `limit`, `offset` | List Opportunities |  | 200: Page_OpportunityOut_ |
| GET | `/api/v1/opportunities/{opportunity_id}` | Get Opportunity |  | 200: OpportunityDetailOut |
| PATCH | `/api/v1/opportunities/{opportunity_id}` | Update Opportunity | OpportunityUpdate | 200: OpportunityOut |
| POST | `/api/v1/opportunities/{opportunity_id}/ai-summary` | Ai Summary |  | 200: AiSummaryOut |
| POST | `/api/v1/opportunities/{opportunity_id}/buy` | Buy Opportunity | OpportunityBuyIn | 201: InventoryOut |

### products

| Method | Path | Summary | Request body | Response |
|---|---|---|---|---|
| GET | `/api/v1/products`<br>query: `q`, `category`, `include_demo`, `limit`, `offset` | List Products |  | 200: Page_ProductOut_ |
| GET | `/api/v1/products/{product_id}` | Get Product |  | 200: ProductDetailOut |

### sales

| Method | Path | Summary | Request body | Response |
|---|---|---|---|---|
| GET | `/api/v1/sales`<br>query: `inventory_item_id`, `limit`, `offset` | List Sales |  | 200: Page_SaleOut_ |
| DELETE | `/api/v1/sales/{sale_id}` | Delete Sale |  | 200: Message |

### searches

| Method | Path | Summary | Request body | Response |
|---|---|---|---|---|
| GET | `/api/v1/searches` | List Searches |  | 200: list[TrackedSearchOut] |
| POST | `/api/v1/searches` | Create Search | TrackedSearchIn | 201: TrackedSearchOut |
| DELETE | `/api/v1/searches/{search_id}` | Delete Search |  | 200: Message |
| PATCH | `/api/v1/searches/{search_id}` | Update Search | TrackedSearchUpdate | 200: TrackedSearchOut |
| POST | `/api/v1/searches/{search_id}/run` | Run Search |  | 200: CollectResult |

### settings

| Method | Path | Summary | Request body | Response |
|---|---|---|---|---|
| GET | `/api/v1/settings` | Get App Settings |  | 200: SettingsOut |
| PUT | `/api/v1/settings` | Update App Settings | SettingsUpdate | 200: SettingsOut |

### sources

| Method | Path | Summary | Request body | Response |
|---|---|---|---|---|
| GET | `/api/v1/sources` | List Sources |  | 200: list[SourceOut] |
| PATCH | `/api/v1/sources/{key}` | Update Source | SourceUpdate | 200: SourceOut |
| POST | `/api/v1/sources/{key}/test` | Test Source |  | 200: SourceTestOut |

## Schema reference

### AiSummaryOut

| Field | Type | Required |
|---|---|---|
| `opportunity_id` | integer | yes |
| `provider` | string | yes |
| `summary` | string | null | yes |
| `validated` | boolean | yes |
| `message` | string |  |

### AlertRuleIn

| Field | Type | Required |
|---|---|---|
| `name` | string | yes |
| `keywords` | string | null |  |
| `brand` | string | null |  |
| `category` | string | null |  |
| `min_profit_cents` | integer | null |  |
| `min_roi_bp` | integer | null |  |
| `min_score` | number | null |  |
| `max_buy_cents` | integer | null |  |
| `min_confidence` | "low" | "medium" | "high" | null |  |
| `exclude_suspicious` | boolean |  |
| `channels` | list["in_app" | "ntfy" | "telegram" | "webhook"] |  |
| `enabled` | boolean |  |

### AlertRuleOut

| Field | Type | Required |
|---|---|---|
| `name` | string | yes |
| `keywords` | string | null |  |
| `brand` | string | null |  |
| `category` | string | null |  |
| `min_profit_cents` | integer | null |  |
| `min_roi_bp` | integer | null |  |
| `min_score` | number | null |  |
| `max_buy_cents` | integer | null |  |
| `min_confidence` | "low" | "medium" | "high" | null |  |
| `exclude_suspicious` | boolean |  |
| `channels` | list["in_app" | "ntfy" | "telegram" | "webhook"] |  |
| `enabled` | boolean |  |
| `id` | integer | yes |
| `created_at` | string | yes |

### AlertRuleUpdate

| Field | Type | Required |
|---|---|---|
| `name` | string | null |  |
| `keywords` | string | null |  |
| `brand` | string | null |  |
| `category` | string | null |  |
| `min_profit_cents` | integer | null |  |
| `min_roi_bp` | integer | null |  |
| `min_score` | number | null |  |
| `max_buy_cents` | integer | null |  |
| `min_confidence` | "low" | "medium" | "high" | null |  |
| `exclude_suspicious` | boolean | null |  |
| `channels` | list["in_app" | "ntfy" | "telegram" | "webhook"] | null |  |
| `enabled` | boolean | null |  |

### AnalyticsSummaryOut

| Field | Type | Required |
|---|---|---|
| `period_days` | integer | yes |
| `revenue_cents` | integer | yes |
| `realized_profit_cents` | integer | yes |
| `units_sold` | integer | yes |
| `sales_count` | integer | yes |
| `avg_roi_bp` | integer | null | yes |
| `avg_days_to_sell` | number | null | yes |
| `inventory_units` | integer | yes |
| `inventory_cost_cents` | integer | yes |
| `inventory_target_value_cents` | integer | null | yes |
| `aging` | dict[str, integer] | yes |
| `open_opportunities` | integer | yes |
| `avg_open_opportunity_score` | number | null | yes |
| `potential_profit_open_cents` | integer | yes |
| `opportunities_by_source` | dict[str, integer] | yes |
| `top_products_by_profit` | list[object] | yes |
| `listings_tracked` | integer | yes |
| `products_tracked` | integer | yes |
| `last_collection_at` | string | null | yes |

### AuthCheckOut

| Field | Type | Required |
|---|---|---|
| `ok` | boolean | yes |
| `token_name` | string | null |  |
| `auth_required` | boolean | yes |

### Body_import_csv_api_v1_import_csv_post

| Field | Type | Required |
|---|---|---|
| `file` | string | yes |

### CalculatorIn

| Field | Type | Required |
|---|---|---|
| `buy_price_cents` | integer | yes |
| `sale_price_cents` | integer | yes |
| `fee_profile_key` | string | null |  |
| `fee_profile` | FeeProfileIn | null |  |
| `buy_shipping_cents` | integer |  |
| `import_costs_cents` | integer |  |
| `other_buy_costs_cents` | integer |  |
| `other_sale_costs_cents` | integer |  |
| `seller_shipping_override_cents` | integer | null |  |
| `sale_price_includes_iva` | boolean |  |
| `treat_withholdings_as_cost` | boolean |  |
| `target_roi_bp` | integer | null |  |

### CalculatorOut

| Field | Type | Required |
|---|---|---|
| `result` | ProfitResultOut | yes |
| `break_even_sale_cents` | integer | null | yes |
| `max_buy_for_target_cents` | integer | null | yes |
| `target_roi_bp` | integer | null | yes |

### CollectRequest

| Field | Type | Required |
|---|---|---|
| `query` | string | yes |
| `source_keys` | list[string] |  |
| `limit` | integer |  |
| `analyze` | boolean |  |

### CollectResult

| Field | Type | Required |
|---|---|---|
| `runs` | list[CollectionRunOut] | yes |
| `opportunities_created` | integer |  |
| `opportunities_updated` | integer |  |
| `notifications_created` | integer |  |

### CollectionRunOut

| Field | Type | Required |
|---|---|---|
| `id` | integer | yes |
| `source_key` | string | null |  |
| `query` | string | null | yes |
| `started_at` | string | yes |
| `finished_at` | string | null | yes |
| `status` | string | yes |
| `items_fetched` | integer | yes |
| `items_new` | integer | yes |
| `items_updated` | integer | yes |
| `error` | string | null | yes |

### CostLineOut

| Field | Type | Required |
|---|---|---|
| `key` | string | yes |
| `label` | string | yes |
| `amount_cents` | integer | yes |
| `formula` | string | yes |

### DemandOut

| Field | Type | Required |
|---|---|---|
| `level` | string | yes |
| `confidence` | string | null | yes |
| `signals` | list[object] |  |
| `note` | string |  |

### FeeProfileIn

| Field | Type | Required |
|---|---|---|
| `key` | string | yes |
| `name` | string | yes |
| `channel` | string |  |
| `commission_bp` | integer |  |
| `fixed_fee_tiers` | list[FixedFeeTierIO] |  |
| `payment_fee_bp` | integer |  |
| `payment_fixed_cents` | integer |  |
| `iva_on_fees` | boolean |  |
| `iva_rate_bp` | integer |  |
| `seller_shipping_cents` | integer |  |
| `free_shipping_threshold_cents` | integer | null |  |
| `isr_withholding_bp` | integer |  |
| `iva_withholding_bp` | integer |  |
| `source_url` | string | null |  |
| `as_of` | string | null |  |
| `notes` | string | null |  |

### FeeProfileOut

| Field | Type | Required |
|---|---|---|
| `key` | string | yes |
| `name` | string | yes |
| `channel` | string |  |
| `commission_bp` | integer |  |
| `fixed_fee_tiers` | list[FixedFeeTierIO] |  |
| `payment_fee_bp` | integer |  |
| `payment_fixed_cents` | integer |  |
| `iva_on_fees` | boolean |  |
| `iva_rate_bp` | integer |  |
| `seller_shipping_cents` | integer |  |
| `free_shipping_threshold_cents` | integer | null |  |
| `isr_withholding_bp` | integer |  |
| `iva_withholding_bp` | integer |  |
| `source_url` | string | null |  |
| `as_of` | string | null |  |
| `notes` | string | null |  |
| `is_builtin` | boolean |  |
| `updated_at` | string | null |  |

### FixedFeeTierIO

| Field | Type | Required |
|---|---|---|
| `max_price_cents` | integer | null |  |
| `fee_cents` | integer | yes |

### FxOut

| Field | Type | Required |
|---|---|---|
| `base` | string | yes |
| `quote` | string | yes |
| `rate` | string | yes |
| `rate_date` | string | yes |
| `source` | string | yes |
| `attribution` | string | null |  |

### HTTPValidationError

| Field | Type | Required |
|---|---|---|
| `detail` | list[ValidationError] |  |

### HealthOut

| Field | Type | Required |
|---|---|---|
| `status` | "ok" | "degraded" | yes |
| `version` | string | yes |
| `api_version` | string |  |
| `database` | string | yes |
| `time` | string | yes |
| `auth_required` | boolean |  |

### ImportCostIn

| Field | Type | Required |
|---|---|---|
| `item_value_usd_cents` | integer | yes |
| `shipping_usd_cents` | integer |  |
| `rule_key` | string |  |
| `usd_mxn_rate` | string | null |  |

### ImportCostOut

| Field | Type | Required |
|---|---|---|
| `rule_key` | string | yes |
| `rule_name` | string | yes |
| `customs_value_usd_cents` | integer | yes |
| `usd_mxn_rate` | string | yes |
| `fx_source` | string | yes |
| `duty_rate_bp` | integer | yes |
| `duties_mxn_cents` | integer | yes |
| `customs_value_mxn_cents` | integer | yes |
| `notes` | list[string] | yes |
| `source_url` | string | null | yes |
| `as_of` | string | null | yes |

### ImportResult

| Field | Type | Required |
|---|---|---|
| `rows_total` | integer | yes |
| `rows_imported` | integer | yes |
| `rows_updated` | integer | yes |
| `rows_rejected` | integer | yes |
| `errors` | list[string] |  |

### InventoryIn

| Field | Type | Required |
|---|---|---|
| `title` | string | yes |
| `sku` | string | null |  |
| `product_id` | integer | null |  |
| `opportunity_id` | integer | null |  |
| `condition` | "new" | "used" | "refurbished" | "for_parts" | "unknown" |  |
| `quantity` | integer |  |
| `unit_cost_cents` | integer | yes |
| `extra_costs_cents` | integer |  |
| `purchase_source` | string | null |  |
| `purchase_url` | string | null |  |
| `purchased_at` | string | null |  |
| `status` | "purchased" | "in_transit" | "in_stock" | "listed" |  |
| `target_sale_cents` | integer | null |  |
| `listed_channel` | string | null |  |
| `location` | string | null |  |
| `notes` | string | null |  |

### InventoryOut

| Field | Type | Required |
|---|---|---|
| `id` | integer | yes |
| `title` | string | yes |
| `sku` | string | null | yes |
| `product_id` | integer | null | yes |
| `opportunity_id` | integer | null | yes |
| `condition` | string | yes |
| `quantity` | integer | yes |
| `quantity_available` | integer | yes |
| `unit_cost_cents` | integer | yes |
| `extra_costs_cents` | integer | yes |
| `total_cost_cents` | integer |  |
| `purchase_source` | string | null | yes |
| `purchase_url` | string | null | yes |
| `purchased_at` | string | yes |
| `status` | string | yes |
| `target_sale_cents` | integer | null | yes |
| `listed_channel` | string | null | yes |
| `location` | string | null | yes |
| `notes` | string | null | yes |
| `days_held` | integer |  |
| `realized_profit_cents` | integer |  |
| `sales` | list[SaleOut] |  |
| `created_at` | string | yes |
| `updated_at` | string | yes |

### InventoryUpdate

| Field | Type | Required |
|---|---|---|
| `title` | string | null |  |
| `sku` | string | null |  |
| `condition` | "new" | "used" | "refurbished" | "for_parts" | "unknown" | null |  |
| `unit_cost_cents` | integer | null |  |
| `extra_costs_cents` | integer | null |  |
| `status` | "purchased" | "in_transit" | "in_stock" | "listed" | "returned" | "written_off" | null |  |
| `target_sale_cents` | integer | null |  |
| `listed_channel` | string | null |  |
| `location` | string | null |  |
| `notes` | string | null |  |

### ListingDetailOut

| Field | Type | Required |
|---|---|---|
| `id` | integer | yes |
| `source_key` | string | yes |
| `external_id` | string | yes |
| `url` | string | null | yes |
| `title` | string | yes |
| `currency` | string | yes |
| `price_cents` | integer | yes |
| `price_mxn_cents` | integer | null | yes |
| `shipping_cents` | integer | null | yes |
| `free_shipping` | boolean | null | yes |
| `condition` | string | yes |
| `brand` | string | null | yes |
| `model` | string | null | yes |
| `category` | string | null | yes |
| `gtin` | string | null | yes |
| `seller_name` | string | null | yes |
| `location` | string | null | yes |
| `image_url` | string | null | yes |
| `available_quantity` | integer | null | yes |
| `sold_quantity` | integer | null | yes |
| `status` | string | yes |
| `product_id` | integer | null | yes |
| `match_confidence` | number | null | yes |
| `match_method` | string | null | yes |
| `is_accessory` | boolean | yes |
| `is_bundle` | boolean | yes |
| `is_suspicious` | boolean | yes |
| `risk_flags` | list[RiskFlagOut] |  |
| `is_demo` | boolean | yes |
| `first_seen_at` | string | yes |
| `last_seen_at` | string | yes |
| `description` | string | null |  |
| `attributes` | object |  |
| `seller` | object |  |
| `observations` | list[PriceObservationOut] |  |

### ListingOut

| Field | Type | Required |
|---|---|---|
| `id` | integer | yes |
| `source_key` | string | yes |
| `external_id` | string | yes |
| `url` | string | null | yes |
| `title` | string | yes |
| `currency` | string | yes |
| `price_cents` | integer | yes |
| `price_mxn_cents` | integer | null | yes |
| `shipping_cents` | integer | null | yes |
| `free_shipping` | boolean | null | yes |
| `condition` | string | yes |
| `brand` | string | null | yes |
| `model` | string | null | yes |
| `category` | string | null | yes |
| `gtin` | string | null | yes |
| `seller_name` | string | null | yes |
| `location` | string | null | yes |
| `image_url` | string | null | yes |
| `available_quantity` | integer | null | yes |
| `sold_quantity` | integer | null | yes |
| `status` | string | yes |
| `product_id` | integer | null | yes |
| `match_confidence` | number | null | yes |
| `match_method` | string | null | yes |
| `is_accessory` | boolean | yes |
| `is_bundle` | boolean | yes |
| `is_suspicious` | boolean | yes |
| `risk_flags` | list[RiskFlagOut] |  |
| `is_demo` | boolean | yes |
| `first_seen_at` | string | yes |
| `last_seen_at` | string | yes |

### Message

| Field | Type | Required |
|---|---|---|
| `ok` | boolean |  |
| `message` | string |  |

### NotificationOut

| Field | Type | Required |
|---|---|---|
| `id` | integer | yes |
| `rule_id` | integer | null | yes |
| `opportunity_id` | integer | null | yes |
| `kind` | string | yes |
| `title` | string | yes |
| `body` | string | yes |
| `created_at` | string | yes |
| `read_at` | string | null | yes |

### OpportunityBuyIn

| Field | Type | Required |
|---|---|---|
| `quantity` | integer |  |
| `unit_cost_cents` | integer | null |  |
| `extra_costs_cents` | integer | null |  |
| `notes` | string | null |  |

### OpportunityDetailOut

| Field | Type | Required |
|---|---|---|
| `id` | integer | yes |
| `product_id` | integer | yes |
| `product_title` | string | yes |
| `product_key` | string | yes |
| `category` | string | null | yes |
| `buy_listing_id` | integer | yes |
| `buy_title` | string | yes |
| `buy_url` | string | null | yes |
| `buy_source_key` | string | yes |
| `buy_condition` | string | yes |
| `image_url` | string | null | yes |
| `fee_profile_key` | string | yes |
| `buy_total_cents` | integer | yes |
| `expected_sale_cents` | integer | yes |
| `net_profit_cents` | integer | yes |
| `roi_bp` | integer | yes |
| `margin_bp` | integer | yes |
| `score` | number | yes |
| `confidence` | string | yes |
| `comparable_count` | integer | yes |
| `risk_level` | "none" | "info" | "warning" | "critical" | yes |
| `demand_level` | string | yes |
| `status` | string | yes |
| `is_demo` | boolean | yes |
| `created_at` | string | yes |
| `updated_at` | string | yes |
| `price_stats` | PriceStatsOut | yes |
| `cost_breakdown` | ProfitResultOut | yes |
| `score_components` | list[ScoreComponentOut] | yes |
| `risk_flags` | list[RiskFlagOut] | yes |
| `demand` | DemandOut | yes |
| `explanation` | string | yes |
| `facts` | object | yes |
| `ai_summary` | string | null | yes |
| `comparables` | list[ListingOut] |  |

### OpportunityOut

| Field | Type | Required |
|---|---|---|
| `id` | integer | yes |
| `product_id` | integer | yes |
| `product_title` | string | yes |
| `product_key` | string | yes |
| `category` | string | null | yes |
| `buy_listing_id` | integer | yes |
| `buy_title` | string | yes |
| `buy_url` | string | null | yes |
| `buy_source_key` | string | yes |
| `buy_condition` | string | yes |
| `image_url` | string | null | yes |
| `fee_profile_key` | string | yes |
| `buy_total_cents` | integer | yes |
| `expected_sale_cents` | integer | yes |
| `net_profit_cents` | integer | yes |
| `roi_bp` | integer | yes |
| `margin_bp` | integer | yes |
| `score` | number | yes |
| `confidence` | string | yes |
| `comparable_count` | integer | yes |
| `risk_level` | "none" | "info" | "warning" | "critical" | yes |
| `demand_level` | string | yes |
| `status` | string | yes |
| `is_demo` | boolean | yes |
| `created_at` | string | yes |
| `updated_at` | string | yes |

### OpportunityUpdate

| Field | Type | Required |
|---|---|---|
| `status` | "new" | "seen" | "watching" | "dismissed" | "bought" | "expired" | yes |

### Page_InventoryOut_

| Field | Type | Required |
|---|---|---|
| `items` | list[InventoryOut] | yes |
| `total` | integer | yes |
| `limit` | integer | yes |
| `offset` | integer | yes |

### Page_ListingOut_

| Field | Type | Required |
|---|---|---|
| `items` | list[ListingOut] | yes |
| `total` | integer | yes |
| `limit` | integer | yes |
| `offset` | integer | yes |

### Page_OpportunityOut_

| Field | Type | Required |
|---|---|---|
| `items` | list[OpportunityOut] | yes |
| `total` | integer | yes |
| `limit` | integer | yes |
| `offset` | integer | yes |

### Page_ProductOut_

| Field | Type | Required |
|---|---|---|
| `items` | list[ProductOut] | yes |
| `total` | integer | yes |
| `limit` | integer | yes |
| `offset` | integer | yes |

### Page_SaleOut_

| Field | Type | Required |
|---|---|---|
| `items` | list[SaleOut] | yes |
| `total` | integer | yes |
| `limit` | integer | yes |
| `offset` | integer | yes |

### PriceObservationOut

| Field | Type | Required |
|---|---|---|
| `observed_at` | string | yes |
| `price_cents` | integer | yes |
| `currency` | string | yes |
| `price_mxn_cents` | integer | null | yes |
| `available_quantity` | integer | null | yes |
| `sold_quantity` | integer | null | yes |
| `status` | string | yes |

### PriceStatsOut

| Field | Type | Required |
|---|---|---|
| `n` | integer | yes |
| `median_cents` | integer | null | yes |
| `p25_cents` | integer | null | yes |
| `p75_cents` | integer | null | yes |
| `min_cents` | integer | null | yes |
| `max_cents` | integer | null | yes |
| `mad_cents` | integer | null | yes |
| `iqr_cents` | integer | null | yes |
| `dispersion` | number | null | yes |
| `source_count` | integer | yes |
| `seller_count` | integer | yes |
| `excluded` | dict[str, integer] |  |
| `comparable_ids` | list[integer] |  |
| `window_days` | integer | null |  |

### ProductDetailOut

| Field | Type | Required |
|---|---|---|
| `id` | integer | yes |
| `canonical_key` | string | yes |
| `title` | string | yes |
| `brand` | string | null | yes |
| `model` | string | null | yes |
| `category` | string | null | yes |
| `gtin` | string | null | yes |
| `attributes` | object |  |
| `listing_count` | integer |  |
| `min_price_mxn_cents` | integer | null |  |
| `median_price_mxn_cents` | integer | null |  |
| `updated_at` | string | yes |
| `stats_by_condition` | dict[str, PriceStatsOut] |  |
| `listings` | list[ListingOut] |  |

### ProductOut

| Field | Type | Required |
|---|---|---|
| `id` | integer | yes |
| `canonical_key` | string | yes |
| `title` | string | yes |
| `brand` | string | null | yes |
| `model` | string | null | yes |
| `category` | string | null | yes |
| `gtin` | string | null | yes |
| `attributes` | object |  |
| `listing_count` | integer |  |
| `min_price_mxn_cents` | integer | null |  |
| `median_price_mxn_cents` | integer | null |  |
| `updated_at` | string | yes |

### ProfitResultOut

| Field | Type | Required |
|---|---|---|
| `sale_price_cents` | integer | yes |
| `acquisition_lines` | list[CostLineOut] | yes |
| `selling_lines` | list[CostLineOut] | yes |
| `total_acquisition_cents` | integer | yes |
| `total_selling_costs_cents` | integer | yes |
| `net_proceeds_cents` | integer | yes |
| `net_profit_cents` | integer | yes |
| `roi_bp` | integer | null | yes |
| `margin_bp` | integer | null | yes |
| `fee_profile_key` | string | yes |
| `assumptions` | list[string] |  |

### RecomputeResult

| Field | Type | Required |
|---|---|---|
| `products_analyzed` | integer | yes |
| `opportunities_created` | integer | yes |
| `opportunities_updated` | integer | yes |
| `opportunities_expired` | integer | yes |
| `notifications_created` | integer | yes |

### RiskFlagOut

| Field | Type | Required |
|---|---|---|
| `code` | string | yes |
| `severity` | string | yes |
| `message` | string | yes |
| `evidence` | object |  |

### SaleIn

| Field | Type | Required |
|---|---|---|
| `quantity` | integer |  |
| `unit_price_cents` | integer | yes |
| `channel` | string | null |  |
| `fee_profile_key` | string | null |  |
| `fees_cents` | integer | null |  |
| `shipping_cents` | integer |  |
| `taxes_withheld_cents` | integer | null |  |
| `other_costs_cents` | integer |  |
| `sold_at` | string | null |  |
| `notes` | string | null |  |

### SaleOut

| Field | Type | Required |
|---|---|---|
| `id` | integer | yes |
| `inventory_item_id` | integer | yes |
| `quantity` | integer | yes |
| `unit_price_cents` | integer | yes |
| `channel` | string | null | yes |
| `fee_profile_key` | string | null | yes |
| `fees_cents` | integer | yes |
| `shipping_cents` | integer | yes |
| `taxes_withheld_cents` | integer | yes |
| `other_costs_cents` | integer | yes |
| `cost_basis_cents` | integer | yes |
| `net_proceeds_cents` | integer | yes |
| `profit_cents` | integer | yes |
| `sold_at` | string | yes |
| `notes` | string | null | yes |

### ScoreComponentOut

| Field | Type | Required |
|---|---|---|
| `key` | string | yes |
| `label` | string | yes |
| `raw_value` | number | null | yes |
| `points` | number | yes |
| `max_points` | number | yes |
| `explanation` | string | yes |

### SettingsOut

| Field | Type | Required |
|---|---|---|
| `min_comparables` | integer | yes |
| `comparable_max_age_days` | integer | yes |
| `default_fee_profile_key` | string | yes |
| `default_fee_profile_key_used` | string | yes |
| `target_roi_bp` | integer | yes |
| `min_profit_cents` | integer | yes |
| `min_roi_bp` | integer | yes |
| `pricing_strategy` | "competitive" | "market" | yes |
| `unknown_shipping_assumption_cents` | integer | null | yes |
| `buy_max_age_days` | integer |  |
| `fx_usd_mxn_manual` | string | null | yes |
| `include_demo_data` | boolean | yes |
| `ai_provider` | string | yes |
| `scheduler_enabled` | boolean | yes |

### SettingsUpdate

| Field | Type | Required |
|---|---|---|
| `min_comparables` | integer | null |  |
| `comparable_max_age_days` | integer | null |  |
| `default_fee_profile_key` | string | null |  |
| `default_fee_profile_key_used` | string | null |  |
| `target_roi_bp` | integer | null |  |
| `min_profit_cents` | integer | null |  |
| `min_roi_bp` | integer | null |  |
| `pricing_strategy` | "competitive" | "market" | null |  |
| `unknown_shipping_assumption_cents` | integer | null |  |
| `buy_max_age_days` | integer | null |  |
| `fx_usd_mxn_manual` | string | null |  |
| `include_demo_data` | boolean | null |  |

### SourceOut

| Field | Type | Required |
|---|---|---|
| `key` | string | yes |
| `name` | string | yes |
| `kind` | string | yes |
| `country` | string | yes |
| `currency` | string | yes |
| `enabled` | boolean | yes |
| `default_fee_profile_key` | string | null | yes |
| `configured` | boolean |  |
| `requires_credentials` | boolean |  |
| `capabilities` | list[string] |  |
| `docs_url` | string | null |  |
| `notes` | string |  |
| `last_run_at` | string | null |  |
| `last_status` | string | null |  |
| `last_error` | string | null |  |

### SourceTestOut

| Field | Type | Required |
|---|---|---|
| `key` | string | yes |
| `configured` | boolean | yes |
| `ok` | boolean | null | yes |
| `message` | string | yes |
| `details` | object |  |

### SourceUpdate

| Field | Type | Required |
|---|---|---|
| `enabled` | boolean | null |  |
| `default_fee_profile_key` | string | null |  |

### TimeseriesOut

| Field | Type | Required |
|---|---|---|
| `bucket` | "day" | "week" | "month" | yes |
| `points` | list[TimeseriesPoint] | yes |

### TimeseriesPoint

| Field | Type | Required |
|---|---|---|
| `period` | string | yes |
| `revenue_cents` | integer | yes |
| `profit_cents` | integer | yes |
| `units` | integer | yes |
| `purchases_cents` | integer | yes |

### TrackedSearchIn

| Field | Type | Required |
|---|---|---|
| `query` | string | yes |
| `category_hint` | string | null |  |
| `source_keys` | list[string] |  |
| `max_results` | integer |  |
| `interval_minutes` | integer |  |
| `enabled` | boolean |  |

### TrackedSearchOut

| Field | Type | Required |
|---|---|---|
| `id` | integer | yes |
| `query` | string | yes |
| `category_hint` | string | null | yes |
| `source_keys` | list[string] | yes |
| `max_results` | integer | yes |
| `interval_minutes` | integer | yes |
| `enabled` | boolean | yes |
| `last_run_at` | string | null | yes |
| `created_at` | string | yes |

### TrackedSearchUpdate

| Field | Type | Required |
|---|---|---|
| `query` | string | null |  |
| `category_hint` | string | null |  |
| `source_keys` | list[string] | null |  |
| `max_results` | integer | null |  |
| `interval_minutes` | integer | null |  |
| `enabled` | boolean | null |  |

### UrlCheckIn

| Field | Type | Required |
|---|---|---|
| `url` | string | yes |
| `save` | boolean |  |

### UrlCheckOut

| Field | Type | Required |
|---|---|---|
| `url` | string | yes |
| `allowed_by_robots` | boolean | yes |
| `found` | boolean | yes |
| `listing` | ListingOut | null |  |
| `extracted` | object |  |
| `message` | string |  |

### ValidationError

| Field | Type | Required |
|---|---|---|
| `loc` | list[string | integer] | yes |
| `msg` | string | yes |
| `type` | string | yes |
| `input` | object |  |
| `ctx` | object |  |
