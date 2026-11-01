#!/usr/bin/env bash
# Compiles the pure-Kotlin (android.*-free) app logic together with the JVM unit tests using kotlinc
# and runs them with JUnit 4 - no Gradle / Android Gradle Plugin needed.
#
# "Pure" main sources are detected automatically: every .kt file under app/src/main/java that does not
# import android.* or the generated R class (Money, ServerUrl, Formats, Json, ApiModels, ApiClient, ...).
#
# Usage:  android/scripts/run-unit-tests.sh
# Env:    KOTLINC (default /opt/kotlinc/bin/kotlinc)
#         M2      directory with junit-4.13.2.jar, hamcrest-core-1.3.jar, json-20250517.jar (default /opt/m2)
#         JUNIT_JAR / HAMCREST_JAR / JSON_JAR to point at the jars individually.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MAIN="$ROOT/app/src/main/java"
TEST="$ROOT/app/src/test/java"
OUT="$ROOT/build/unit-tests"
KOTLINC="${KOTLINC:-/opt/kotlinc/bin/kotlinc}"
KOTLIN_HOME="$(cd "$(dirname "$(readlink -f "$KOTLINC")")/.." && pwd)"
KOTLIN_STDLIB="${KOTLIN_STDLIB:-$KOTLIN_HOME/lib/kotlin-stdlib.jar}"
M2="${M2:-/opt/m2}"
JUNIT_JAR="${JUNIT_JAR:-$M2/junit-4.13.2.jar}"
HAMCREST_JAR="${HAMCREST_JAR:-$M2/hamcrest-core-1.3.jar}"
JSON_JAR="${JSON_JAR:-$M2/json-20250517.jar}"

die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }
run() {
  local rc=0 logf
  logf="$(mktemp)"
  "$@" >"$logf" 2>&1 || rc=$?
  grep -v '^Picked up JAVA_TOOL_OPTIONS' "$logf" || true
  rm -f "$logf"
  return $rc
}

for f in "$KOTLINC" ; do [ -x "$f" ] || die "kotlinc not found at $f (set KOTLINC)"; done
for f in "$KOTLIN_STDLIB" "$JUNIT_JAR" "$HAMCREST_JAR" "$JSON_JAR"; do [ -f "$f" ] || die "missing jar: $f"; done

rm -rf "$OUT"
mkdir -p "$OUT/classes"

mapfile -t PURE < <(find "$MAIN" -name '*.kt' -print0 | xargs -0 grep -L -E '^import (android\.|com\.flipradar\.app\.R$)' | sort)
mapfile -t TESTS < <(find "$TEST" -name '*.kt' | sort)
[ "${#TESTS[@]}" -gt 0 ] || die "no test sources found under $TEST"

echo "Pure main sources (${#PURE[@]}):"
for f in "${PURE[@]}"; do echo "  ${f#$ROOT/}"; done
echo "Test sources (${#TESTS[@]}):"
for f in "${TESTS[@]}"; do echo "  ${f#$ROOT/}"; done

CP="$JUNIT_JAR:$HAMCREST_JAR:$JSON_JAR"
echo
echo "==> kotlinc"
run "$KOTLINC" -jvm-target 17 -no-reflect -classpath "$CP" -d "$OUT/classes" "${PURE[@]}" "${TESTS[@]}" \
  || die "test compilation failed"

# Test classes: every top-level `class XxxTest` in the test sources, qualified with its package.
CLASSES=()
for f in "${TESTS[@]}"; do
  pkg="$(sed -n -E 's/^package[[:space:]]+([a-zA-Z0-9_.]+).*/\1/p' "$f" | head -1)"
  while read -r cls; do
    [ -n "$cls" ] && CLASSES+=("${pkg:+$pkg.}$cls")
  done < <(sed -n -E 's/^class[[:space:]]+([A-Za-z0-9_]+Test)\b.*/\1/p' "$f")
done
[ "${#CLASSES[@]}" -gt 0 ] || die "no test classes found"

echo
echo "==> JUnit: ${CLASSES[*]}"
# --add-opens: lets ApiClient's PATCH fallback work on the desktop JDK (Android supports PATCH natively).
run java --add-opens java.base/java.net=ALL-UNNAMED \
  -cp "$OUT/classes:$KOTLIN_STDLIB:$CP" org.junit.runner.JUnitCore "${CLASSES[@]}"
