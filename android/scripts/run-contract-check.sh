#!/usr/bin/env bash
# Runs the app's real ApiClient/ApiModels (pure Kotlin) on the JVM against a LIVE backend.
# Usage: scripts/run-contract-check.sh <baseUrl> <token>      (creates and deletes test data)
# Env:   KOTLINC (default /opt/kotlinc/bin/kotlinc), M2 (dir with json-20250517.jar, default /opt/m2)
set -euo pipefail
cd "$(dirname "$0")/.."
KOTLINC="${KOTLINC:-/opt/kotlinc/bin/kotlinc}"
M2="${M2:-/opt/m2}"
JSON_JAR="$M2/json-20250517.jar"
[ $# -ge 2 ] || { echo "usage: $0 <baseUrl> <token>" >&2; exit 2; }
[ -f "$JSON_JAR" ] || { echo "org.json jar not found at $JSON_JAR" >&2; exit 2; }
OUT=build/contract-check
rm -rf "$OUT" && mkdir -p "$OUT"
mapfile -t PURE < <(find app/src/main/java -name '*.kt' -print0 | xargs -0 grep -L -E '^import (android\.|com\.flipradar\.app\.R$)' | sort)
"$KOTLINC" -jvm-target 17 -nowarn -classpath "$JSON_JAR" -d "$OUT/classes" "${PURE[@]}" app/src/contractCheck/java/com/flipradar/app/ContractCheck.kt 2>&1 | grep -v '^warning:' || true
KLIB="$(dirname "$(dirname "$(readlink -f "$KOTLINC")")")/lib/kotlin-stdlib.jar"
# --add-opens: ApiClient PATCH fallback on the desktop JDK (Android supports PATCH natively).
java --add-opens java.base/java.net=ALL-UNNAMED -cp "$OUT/classes:$JSON_JAR:$KLIB" com.flipradar.app.ContractCheckKt "$1" "$2"
