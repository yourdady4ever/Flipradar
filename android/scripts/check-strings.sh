#!/usr/bin/env bash
# Verifies string resources:
#   1. every R.string.X / @string/X referenced by the app exists in values/strings.xml
#   2. values-es/strings.xml has exactly the same keys as values/strings.xml (app_name may be omitted)
#   3. positional format arguments (%1$s, %2$d, ...) match between English and Spanish
# Usage: scripts/check-strings.sh   (exit code 1 on any problem)
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MAIN="$ROOT/app/src/main"
EN="$MAIN/res/values/strings.xml"
ES="$MAIN/res/values-es/strings.xml"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
status=0

keys() { grep -oE '<string name="[a-zA-Z0-9_]+"' "$1" | sed -E 's/<string name="([^"]+)"/\1/' | sort -u; }

{ grep -rhoE '[^.]R\.string\.[a-zA-Z0-9_]+' "$MAIN/java" | sed -E 's/.*R\.string\.//' || true
  grep -rhoE '@string/[a-zA-Z0-9_]+' "$MAIN/AndroidManifest.xml" "$MAIN/res" | sed 's#@string/##' || true
} | sort -u > "$TMP/used"
keys "$EN" > "$TMP/en"
keys "$ES" > "$TMP/es"

missing=$(comm -23 "$TMP/used" "$TMP/en")
if [ -n "$missing" ]; then echo "Referenced but not defined in values/strings.xml:"; echo "$missing"; status=1; fi
unused=$(comm -13 "$TMP/used" "$TMP/en")
if [ -n "$unused" ]; then echo "Note: defined but unused (not an error):"; echo "$unused"; fi
only_en=$(comm -23 "$TMP/en" "$TMP/es" | grep -v '^app_name$' || true)
if [ -n "$only_en" ]; then echo "Missing Spanish translation:"; echo "$only_en"; status=1; fi
only_es=$(comm -13 "$TMP/en" "$TMP/es")
if [ -n "$only_es" ]; then echo "Spanish key without English default:"; echo "$only_es"; status=1; fi

fmt() { grep -E "<string name=\"$2\"" "$1" | grep -oE '%[0-9]+\$[sd]' | sort | tr '\n' ' ' || true; }
while read -r k; do
  [ "$k" = "app_name" ] && continue
  if [ "$(fmt "$EN" "$k")" != "$(fmt "$ES" "$k")" ]; then echo "Format args differ for $k"; status=1; fi
done < "$TMP/es"

[ $status -eq 0 ] && echo "strings OK: $(wc -l < "$TMP/en") keys, $(wc -l < "$TMP/used") referenced"
exit $status
