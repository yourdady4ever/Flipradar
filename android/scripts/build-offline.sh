#!/usr/bin/env bash
# Builds a signed DEBUG APK of the FLIP RADAR Android client WITHOUT Gradle / the Android Gradle Plugin,
# using only the Android SDK command-line tools + kotlinc + a JDK.  Works fully offline (no Google
# Maven needed) because the app depends only on the Android framework and the Kotlin stdlib.
#
#   aapt2 compile/link -> javac (R.java) + kotlinc -> d8 -> zip -> zipalign -> apksigner
#
# Usage:  android/scripts/build-offline.sh [--clean]
# Env:    ANDROID_HOME   SDK root                      (default /opt/android-sdk)
#         KOTLINC        kotlinc executable            (default /opt/kotlinc/bin/kotlinc)
#         KOTLIN_STDLIB  kotlin-stdlib.jar to bundle   (default <kotlinc>/../lib/kotlin-stdlib.jar)
#         BUILD_TOOLS    build-tools version           (default: newest of 36.0.0/35.0.0/... installed)
#         PLATFORM       platform dir name             (default android-36)
#         KEYSTORE       signing keystore              (default android/build/debug.keystore, created on demand)
# Output: android/build/outputs/flipradar-debug.apk
set -euo pipefail

VERSION_CODE=1          # keep in sync with app/build.gradle.kts and AppInfo.kt
VERSION_NAME="0.1.0"
PACKAGE="com.flipradar.app"
MIN_SDK=26
TARGET_SDK=36

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APP="$ROOT/app"
BUILD="$ROOT/build/offline"
OUT_DIR="$ROOT/build/outputs"
OUT_APK="$OUT_DIR/flipradar-debug.apk"

ANDROID_HOME="${ANDROID_HOME:-${ANDROID_SDK_ROOT:-/opt/android-sdk}}"
KOTLINC="${KOTLINC:-/opt/kotlinc/bin/kotlinc}"
KOTLIN_HOME="$(cd "$(dirname "$(readlink -f "$KOTLINC")")/.." && pwd)"
KOTLIN_STDLIB="${KOTLIN_STDLIB:-$KOTLIN_HOME/lib/kotlin-stdlib.jar}"
PLATFORM="${PLATFORM:-android-36}"
ANDROID_JAR="$ANDROID_HOME/platforms/$PLATFORM/android.jar"
KEYSTORE="${KEYSTORE:-$ROOT/build/debug.keystore}"

log() { printf '\n==> %s\n' "$*"; }
die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }
# Runs a command, hides the JVM's "Picked up JAVA_TOOL_OPTIONS" noise, preserves the exit status.
run() {
  local rc=0 logf
  logf="$(mktemp)"
  "$@" >"$logf" 2>&1 || rc=$?
  grep -v '^Picked up JAVA_TOOL_OPTIONS' "$logf" || true
  rm -f "$logf"
  return $rc
}

if [ -z "${BUILD_TOOLS:-}" ]; then
  for v in 36.0.0 36.1.0 35.0.0 37.0.0 34.0.0; do
    if [ -x "$ANDROID_HOME/build-tools/$v/aapt2" ]; then BUILD_TOOLS="$v"; break; fi
  done
fi
BT="$ANDROID_HOME/build-tools/${BUILD_TOOLS:-missing}"

[ -f "$ANDROID_JAR" ] || die "android.jar not found at $ANDROID_JAR (set ANDROID_HOME / PLATFORM)"
[ -x "$BT/aapt2" ] || die "build-tools not found under $ANDROID_HOME/build-tools (set BUILD_TOOLS)"
[ -f "$BT/lib/d8.jar" ] || die "d8.jar not found in $BT/lib"
[ -x "$KOTLINC" ] || die "kotlinc not found at $KOTLINC (set KOTLINC)"
[ -f "$KOTLIN_STDLIB" ] || die "kotlin-stdlib.jar not found at $KOTLIN_STDLIB (set KOTLIN_STDLIB)"
for tool in java javac keytool zip unzip; do command -v "$tool" >/dev/null || die "$tool not found on PATH"; done

if [ "${1:-}" = "--clean" ]; then rm -rf "$BUILD"; fi
rm -rf "$BUILD/res" "$BUILD/gen" "$BUILD/classes" "$BUILD/dex" "$BUILD"/*.apk "$BUILD/AndroidManifest.xml"
mkdir -p "$BUILD/res" "$BUILD/gen" "$BUILD/classes" "$BUILD/dex" "$OUT_DIR"

echo "SDK:          $ANDROID_HOME (build-tools $BUILD_TOOLS, $PLATFORM)"
echo "kotlinc:      $KOTLINC"
echo "kotlin-stdlib $KOTLIN_STDLIB"

# ------------------------------------------------------------------------------------------------
log "1/7 aapt2 compile resources"
"$BT/aapt2" compile --dir "$APP/src/main/res" -o "$BUILD/res/compiled.zip"

# ------------------------------------------------------------------------------------------------
log "2/7 aapt2 link (manifest + resources -> base.apk, R.java)"
# The source manifest has no package attribute (AGP takes it from `namespace`); inject it into a copy.
sed -E "0,/<manifest /s//<manifest package=\"$PACKAGE\" /" "$APP/src/main/AndroidManifest.xml" > "$BUILD/AndroidManifest.xml"
grep -q "package=\"$PACKAGE\"" "$BUILD/AndroidManifest.xml" || die "failed to inject package into manifest"
"$BT/aapt2" link \
  -I "$ANDROID_JAR" \
  --manifest "$BUILD/AndroidManifest.xml" \
  --min-sdk-version "$MIN_SDK" \
  --target-sdk-version "$TARGET_SDK" \
  --version-code "$VERSION_CODE" \
  --version-name "$VERSION_NAME" \
  --debug-mode \
  --auto-add-overlay \
  --java "$BUILD/gen" \
  -o "$BUILD/base.apk" \
  "$BUILD/res/compiled.zip"
R_JAVA="$BUILD/gen/${PACKAGE//./\/}/R.java"
[ -f "$R_JAVA" ] || die "R.java was not generated"

# ------------------------------------------------------------------------------------------------
log "3/7 kotlinc (Kotlin sources + R.java on the source path)"
mapfile -t KT_SOURCES < <(find "$APP/src/main/java" -name '*.kt' | sort)
[ "${#KT_SOURCES[@]}" -gt 0 ] || die "no Kotlin sources found"
echo "${#KT_SOURCES[@]} Kotlin files"
# -no-jdk: compile against android.jar's java.* (not the host JDK) so non-Android APIs fail at build time.
run "$KOTLINC" \
  -jvm-target 17 \
  -no-jdk \
  -no-reflect \
  -nowarn \
  -classpath "$ANDROID_JAR" \
  -d "$BUILD/classes" \
  "${KT_SOURCES[@]}" "$R_JAVA" || die "Kotlin compilation failed"
[ -f "$BUILD/classes/com/flipradar/app/ui/MainActivity.class" ] || die "Kotlin compilation produced no classes"

log "3b/7 javac R.java"
run javac --release 17 -nowarn -encoding UTF-8 -d "$BUILD/classes" "$R_JAVA" || die "javac R.java failed"

# ------------------------------------------------------------------------------------------------
log "4/7 d8 (classes + kotlin-stdlib -> classes.dex)"
run jar cf "$BUILD/app-classes.jar" -C "$BUILD/classes" . || die "jar failed"
run java -cp "$BT/lib/d8.jar" com.android.tools.r8.D8 \
  --release \
  --min-api "$MIN_SDK" \
  --lib "$ANDROID_JAR" \
  --output "$BUILD/dex" \
  "$BUILD/app-classes.jar" "$KOTLIN_STDLIB" || die "d8 failed"
ls "$BUILD"/dex/classes*.dex >/dev/null 2>&1 || die "d8 produced no dex files"

# ------------------------------------------------------------------------------------------------
log "5/7 package dex into APK"
cp "$BUILD/base.apk" "$BUILD/unaligned.apk"
(cd "$BUILD/dex" && zip -q -X "$BUILD/unaligned.apk" classes*.dex)

# ------------------------------------------------------------------------------------------------
log "6/7 zipalign"
"$BT/zipalign" -p -f 4 "$BUILD/unaligned.apk" "$BUILD/aligned.apk"
"$BT/zipalign" -c -p 4 "$BUILD/aligned.apk"

# ------------------------------------------------------------------------------------------------
log "7/7 apksigner (debug key)"
if [ ! -f "$KEYSTORE" ]; then
  echo "Creating debug keystore at $KEYSTORE (never commit it)"
  mkdir -p "$(dirname "$KEYSTORE")"
  run keytool -genkeypair \
    -keystore "$KEYSTORE" -storetype PKCS12 \
    -storepass android -keypass android \
    -alias androiddebugkey -keyalg RSA -keysize 2048 -validity 10000 \
    -dname "CN=FlipRadar Debug,O=FlipRadar,C=MX" || die "keytool failed"
fi
rm -f "$OUT_APK"
run "$BT/apksigner" sign \
  --ks "$KEYSTORE" --ks-pass pass:android --key-pass pass:android --ks-key-alias androiddebugkey \
  --out "$OUT_APK" "$BUILD/aligned.apk" || die "apksigner sign failed"
run "$BT/apksigner" verify "$OUT_APK" || die "apksigner verify failed"

log "Done"
ls -l "$OUT_APK"
"$BT/aapt2" dump badging "$OUT_APK" | grep -E "^(package|minSdkVersion|sdkVersion|targetSdkVersion|application-label:|launchable-activity)" || true
echo
echo "Install on a device/emulator:  adb install -r \"$OUT_APK\""
