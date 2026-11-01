# TODO / backlog

Priority: **P1** = needed for confident real-world use, **P2** = valuable, **P3** = nice to have.

## P1: verify with real accounts and networks (the build sandbox could not reach these hosts)

- [ ] **Mercado Libre live test.** Create a DevCenter app, set `FLIPRADAR_ML_CLIENT_ID`/`SECRET`, run
  `python -m flipradar collect "iphone 15" --source mercadolibre`, and confirm:
  - `/products/search` and `/products/{id}/items` return 200 for your app (not a PolicyAgent 403);
  - the response field names match `collectors/mercadolibre.py`;
  - `client_credentials` works or you need `authorization_code` (then set `FLIPRADAR_ML_REFRESH_TOKEN`).
- [ ] **eBay Browse live test** with your production keyset: field names, `deliveryCountry:MX` filter,
  shipping options with `X-EBAY-C-ENDUSERCTX`.
- [ ] **Banxico SIE live test** (`python -m flipradar fx-refresh` with `FLIPRADAR_BANXICO_TOKEN`).
- [ ] **Re-verify the fee parameters** marked low confidence in `docs/DATA_SOURCES.md`, against your own
  Mercado Libre seller account (commission per category, the per-unit "costo de envíos" tiers),
  Amazon MX and Mercado Pago. Adjust the built-in profiles in the dashboard.
- [ ] **Run the Android app on a real device:** setup, every screen, background notifications
  (Doze), and the dark theme.
- [x] **Confirm the GitHub Actions `android-gradle` job is green** (AGP 8.13 / Kotlin 2.2.20 / Gradle 8.14.3).
  Done: green since CI run 10. Re-check it after dependency upgrades.

## P2: product improvements

- [ ] **Recover matching recall safely.** Listings that omit storage, PS5/Xbox edition or the screen size
  of multi-size lines are deliberately not clustered (precision first). Add user-confirmed defaults per
  product family (for example "PS5 Slim 1TB without 'digital' = standard edition") and a "needs review"
  queue in the dashboard to assign such listings by hand.
- [ ] **Category name from Mercado Libre.** Pass `category_name` from the catalog API (domain name) into
  `RawListing`, so the normalizer's accessory-category guard can fire.
- [ ] **`formal_import_required` in `ImportCostOut`.** Expose this flag, make duties nullable when they
  are not estimated, and disable the dashboard's "Use duties" button in that case.
- [ ] **`sqlite_autoincrement` on `notifications`,** so ids are never reused after deletes. The
  Android poller no longer depends on this.
- [ ] **ML fee lookup in analysis.** When a token is configured, use `/sites/MLM/listing_prices`
  (already implemented in `MercadoLibreCollector.get_listing_fee`) to replace the profile's
  commission with the exact per-category rate. Cache the rate per category.
- [ ] **Fee profile per category or weight.** Consoles and laptops weigh more than 0.3 kg; map
  categories to `ml_clasica_1_2kg` or new heavier tiers.
- [ ] **Item refresh for tracked listings,** to detect price drops and sold-out states. This is limited
  by ML's 2026 third-party `/items` restrictions; `/products/{id}/items` re-polling works.
- [ ] **Mercado Libre `authorization_code` helper:** a `flipradar ml-exchange-code CODE` CLI (ML
  requires an HTTPS redirect URI, so this needs a manual code-paste flow).
- [ ] **PROFECO QQP importer** (CC-BY-4.0, RAR5 yearly archives; needs `libarchive`) to provide
  reference retail prices for new appliances and electronics.
- [ ] **Analytics in the local timezone** (America/Mexico_City). Daily buckets are currently UTC.
- [ ] **Trusted-proxy support** for the rate limiter (`X-Forwarded-For`) when running behind Caddy/nginx.
- [ ] **Alembic migrations** before the first schema change on a deployed database (see `CONTINUATION.md`).
- [ ] **Store `analyze_listing` notes** (normalization explanations) on listings and show them in the UI.
- [ ] **Android:** product images, an offline cache of the last opportunity list, and fee-profile
  editing in the app.
- [ ] **Sold-price evidence from your own sales:** feed recorded sales back as `sold` comparables for
  the same product. This is the strongest legitimate demand and price signal available.

## P3: optional integrations (each needs your own account; some are paid)

- [ ] Amazon SP-API connector, for users with a Professional seller account ($75–600 MXN/month).
- [ ] Amazon Creators API (requires an Associates account with 10 sales in 30 days).
- [ ] AliExpress affiliate API (`ship_to_country=MX`). Verify the signing details first.
- [ ] Envia.com / Skydropx shipping quotes (labels are prepaid; quotes are free).
- [ ] Keepa (paid) for Amazon price history.
- [ ] Frankfurter v2 endpoint (`/v2/rates?base=USD&quotes=MXN`); v1 is frozen but still served.
- [ ] Release signing and Play Store or F-Droid packaging for the Android app.
