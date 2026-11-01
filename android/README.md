# FLIP RADAR – Android client

Native Android app for **your own** FLIP RADAR backend (self-hosted resale-arbitrage tool for the Mexican
market). The app is a thin, safe client: every price, fee, profit and score is computed by the server with
deterministic formulas (`backend/flipradar`); the app only displays and edits data through the HTTP API
(`docs/API_CONTRACT.md`, models in `backend/flipradar/schemas.py`).

- Package / applicationId: `com.flipradar.app` · minSdk 26 (Android 8.0) · target/compile SDK 36
- Language: Kotlin 2.2 · **no AndroidX, no Compose, no third-party libraries** — only Android framework APIs
  (`android.*`, `java.*`, platform `org.json`) plus the Kotlin stdlib. This is deliberate: the app can be
  built completely offline with the SDK command-line tools (see below), and there is no dependency
  supply chain to audit.
- Languages: English (default) and Spanish (`values-es`), selected from the device language.

## Screens

| Screen | What it does | API |
|---|---|---|
| Setup | Server URL + API token, poll interval, **Test connection**, warning for `http://` to public hosts | `GET /health`, `GET /api/v1/auth/check` |
| Main (hub) | Connection status, navigation, unread-notification badge, asks for notification permission (API 33+) | `GET /health`, `GET /notifications?unread_only=true` |
| Opportunities | Paged list (Load more), filters: search, min score, min profit, sort (score/profit/ROI/recent), status, include suspicious; recompute | `GET /opportunities`, `POST /analyze/recompute` |
| Opportunity detail | Buy listing, expected sale price and its derivation (median/P25–P75/comparables), full cost table (acquisition + selling lines with formulas), net profit/ROI/margin, score components, risk flags (coloured by severity), demand signals, server explanation, comparables, facts; actions: Open listing, Watch, Dismiss, Mark as bought, AI summary (shows *validated* flag) | `GET/PATCH /opportunities/{id}`, `POST …/buy`, `POST …/ai-summary` |
| Calculator | Buy price, shipping, import, other costs, sale price, fee profile, IVA/withholding switches, target ROI → breakdown, break-even, max buy price | `GET /fee-profiles`, `POST /calculator/profit` |
| Inventory | List with status filter + paging, add item dialog | `GET/POST /inventory` |
| Inventory detail | Details, change status/target/channel, record sale (fee profile or manual fees), sales list, delete sale/item | `GET/PATCH/DELETE /inventory/{id}`, `POST /inventory/{id}/sales`, `DELETE /sales/{id}` |
| Dashboard | Revenue, realized profit, units, avg ROI, avg days to sell, capital in inventory, aging buckets, open opportunities, potential profit, by source, top products, tracking stats, **weekly profit bar chart** (Canvas, losses drawn below the axis) | `GET /analytics/summary`, `GET /analytics/timeseries` |
| Searches & sources | Tracked searches (add, run now with per-source result counts, enable, delete); sources with configured/enabled status, enable toggle, test, docs link | `/searches…`, `/sources…` |
| Alerts | Alert rules (name, keywords, min profit, min ROI %, min score, max buy, min confidence, exclude suspicious), enable toggle, delete | `/alerts…` |
| Notifications | List (unread only toggle), open related opportunity (marks read), mark all read | `/notifications…` |

Background notifications: `NotificationPollJob` (JobScheduler, periodic ≥ 15 min, requires network,
persisted across reboots, re-scheduled by `BootReceiver`) calls
`GET /api/v1/notifications?unread_only=true&since_id=0&limit=50` (the newest page) and posts the unread
notifications created after the last one already shown (up to 5 Android notifications on channel
`opportunities`, plus a summary); tapping one opens the opportunity and marks it read. "Already shown"
is a cursor of id + server `created_at` (`NotificationCursor`), because the server reuses ids after its
newest rows are deleted (e.g. clear-demo) or its database is reset; changing the server URL resets it.
A failed poll (e.g. a LAN-only server while the phone is away) simply waits for the next period; it never
asks JobScheduler for exponential backoff.

## Build

### A. Android Studio / Gradle (needs internet access to Google Maven)

```bash
cd android
./gradlew assembleDebug            # -> app/build/outputs/apk/debug/app-debug.apk
./gradlew testDebugUnitTest        # JVM unit tests
```

Uses AGP 8.13.0, Kotlin Android plugin 2.2.20, Gradle wrapper 8.14.3, JDK 17+. Or open the `android/`
folder in Android Studio and run the `app` configuration. (The Gradle build could not be executed in the
environment where this app was written because Google's Maven repository was blocked there; the files are
standard and were kept conventional. If AGP complains, first try updating the plugin versions in
`build.gradle.kts`.)

### B. Offline build without Gradle (`scripts/build-offline.sh`)

Builds a signed **debug** APK using only the SDK tools (`aapt2`, `d8`, `zipalign`, `apksigner`), `kotlinc`
and a JDK — no AGP, no Google Maven:

```bash
android/scripts/build-offline.sh            # incremental; add --clean to wipe build/offline first
# -> android/build/outputs/flipradar-debug.apk
```

Steps: `aapt2 compile` → `aapt2 link` (package injected into a temp manifest copy, min 26 / target 36,
version from the script, `--debug-mode`) → `kotlinc` (Kotlin + generated `R.java` on the source path,
`-no-jdk` against `android.jar` so non-Android Java APIs fail at compile time) → `javac R.java` → `d8`
(`--min-api 26`, app classes + kotlin-stdlib) → zip → `zipalign -p 4` → `apksigner` with a debug keystore
generated on first run at `android/build/debug.keystore` (git-ignored; **never commit keystores**).

Environment variables: `ANDROID_HOME` (default `/opt/android-sdk`), `KOTLINC` (default
`/opt/kotlinc/bin/kotlinc`), `KOTLIN_STDLIB`, `BUILD_TOOLS` (default: newest of 36.0.0/36.1.0/35.0.0…),
`PLATFORM` (default `android-36`), `KEYSTORE`.

Keep `VERSION_CODE`/`VERSION_NAME` in the script in sync with `app/build.gradle.kts` and `AppInfo.kt`.

Verify an APK:

```bash
$ANDROID_HOME/build-tools/36.0.0/aapt2 dump badging android/build/outputs/flipradar-debug.apk
$ANDROID_HOME/build-tools/36.0.0/apksigner verify --print-certs android/build/outputs/flipradar-debug.apk
```

### Tests and checks (offline)

```bash
android/scripts/run-unit-tests.sh      # kotlinc + JUnit 4: Money, ServerUrl, Formats, ApiModels, ApiClient (local HTTP server)
android/scripts/check-strings.sh       # every R.string exists; en/es key + format-argument parity
android/scripts/check-api-levels.py    # NewApi-style check of classes.dex vs api-versions.xml (run after the build)
```

`run-unit-tests.sh` compiles every main source file that does not import `android.*`/`R` (the pure logic
layer) together with `app/src/test/java/**` and runs JUnit. Jars default to `/opt/m2`
(`junit-4.13.2.jar`, `hamcrest-core-1.3.jar`, `json-20250517.jar`); override with `M2` or
`JUNIT_JAR`/`HAMCREST_JAR`/`JSON_JAR`.

## Install

```bash
adb install -r android/build/outputs/flipradar-debug.apk     # or app/build/outputs/apk/debug/app-debug.apk
```

Or copy the APK to the phone and open it (allow "Install unknown apps" for your file manager/browser).
The debug key differs between machines: uninstall first if you switch between Gradle and offline builds.

For a release build, create your own keystore (`keytool -genkeypair -keystore release.jks …`, keep it
out of git) and sign with `apksigner sign --ks release.jks …` or configure `signingConfigs` in Gradle.

## Configure

1. Start the backend (see the repository README). It listens on `127.0.0.1` by default; set
   `FLIPRADAR_HOST=0.0.0.0` so phones on your LAN can reach it. Create an API token on the server with
   `flipradar create-token` (auth is required unless `FLIPRADAR_REQUIRE_AUTH=false`).
2. Open the app → **Server settings**:
   - URL: e.g. `http://192.168.1.20:8000` (LAN IP of the computer running the backend). From the Android
     emulator, the host machine is `http://10.0.2.2:8000`.
     Without a scheme, `http://` is assumed for private/LAN hosts and `https://` otherwise.
   - Token: paste it (leave blank later to keep the saved one; "Forget saved token" removes it). The saved
     token belongs to the saved server URL: it is not sent when you test another URL, and saving a
     different URL removes it, so paste the token of the new server.
   - Background check interval (Off, 15 min … 12 h).
3. **Test connection** (checks `/health`, API version and the token), then **Save**.

## Security notes

- The API token is encrypted with an AES-256/GCM key stored in the **Android Keystore**; only ciphertext
  + IV are in SharedPreferences. It is never logged, never put in URLs or error messages, and only sent in
  the `Authorization: Bearer` header — never to `/health`.
- HTTP redirects are **not followed**, so the token can't be forwarded to another host.
- The saved token is bound to the server URL it was saved with: changing the URL in Server settings
  removes it, and "Test connection" never sends it to a different URL.
- Cleartext HTTP is allowed (`res/xml/network_security_config.xml`) because self-hosted servers usually run
  on a LAN without a certificate. The Setup screen warns when an `http://` URL points to a public host.
  For remote access use HTTPS (reverse proxy) or a VPN (WireGuard/Tailscale). User-installed CAs are
  trusted so a private-CA certificate for your own server works.
- Backups and device-to-device transfer of app data are disabled (`allowBackup=false`,
  `data_extraction_rules.xml`).
- Opening listings only launches `http(s)` URLs via `ACTION_VIEW`.

## Code layout

```
app/src/main/java/com/flipradar/app/
  AppInfo.kt, FlipRadarApp.kt          version constants; Application (notification channel)
  data/ApiClient.kt                    HttpURLConnection client, bearer auth, timeouts, FastAPI error details
  data/ApiModels.kt, data/Json.kt      data classes + lenient parsers mirroring schemas.py; request bodies
  data/Settings.kt                     SharedPreferences + Keystore-encrypted token
  util/Money.kt                        centavos/basis-point formatting & exact parsing (no floating point)
  util/ServerUrl.kt, util/Formats.kt   URL validation/LAN detection; dates/scores display
  util/Async.kt                        Executors + main-thread Handler (no coroutines library)
  ui/*                                 Activities (programmatic framework views), adapters, BarChartView
  notify/*                             JobScheduler poll job, scheduler, notifier, boot receiver
app/src/test/java/…                    JVM unit tests (JUnit 4)
scripts/                               offline build, unit tests, string + API-level checks
```

When `backend/flipradar/schemas.py` changes, update `data/ApiModels.kt` (parsers + `toJson`) and
`ApiModelsTest`, then run `scripts/run-unit-tests.sh`.

## Known limitations

- Not yet exercised on a physical device/emulator in the build environment (no emulator available there);
  verified by compilation (zero warnings), APK inspection (`aapt2 dump badging`, `apksigner verify`),
  manifest-vs-dex component check, the API-level check and 46 JVM unit tests (including real HTTP calls
  against a local server). There are no instrumented/UI tests yet.
- The Gradle/AGP build was not executed in that environment (Google Maven blocked); the offline script is
  the verified build path.
- Product images are not downloaded/displayed (keeps the app dependency-free); open the listing instead.
- No offline cache: screens need the server to be reachable.
- Background polling uses JobScheduler: Android may delay it (Doze/app standby); minimum period 15 min.
  For instant push, configure the server's ntfy/Telegram channels instead.
- Fee-profile editing, CSV import, URL check and server settings are available in the web dashboard, not
  in the app.
- Money is always displayed in MXN with es-MX grouping (`$12,345.67`), as returned by the server.
- Debug builds only out of the box; release signing must be set up by the user.
