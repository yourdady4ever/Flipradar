#!/usr/bin/env python3
"""Offline substitute for Android lint's NewApi check.

Lists every framework (android.*, java.*, javax.*) method/field referenced by the app's own classes in
classes.dex whose API level is higher than minSdk, using the SDK's api-versions.xml.  Each reported
usage must be guarded by a Build.VERSION.SDK_INT check in the source - review the list after changes.

Usage: scripts/check-api-levels.py [dex] [--min-sdk 26]
       (run scripts/build-offline.sh first; defaults to build/offline/dex/classes.dex)
Env:   ANDROID_HOME (default /opt/android-sdk), BUILD_TOOLS (default 36.0.0), PLATFORM (default android-36)
"""
import argparse
import os
import re
import subprocess
import sys
import xml.etree.ElementTree as ET

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SDK = os.environ.get("ANDROID_HOME", "/opt/android-sdk")
BT = os.environ.get("BUILD_TOOLS", "36.0.0")
PLATFORM = os.environ.get("PLATFORM", "android-36")
APP_PREFIX = "com/flipradar/app/"

# Usages reviewed and known to be behind an SDK_INT guard ("Class.member" prefix match).
GUARDED = {
    "android/view/WindowInsets": "BaseActivity.applyInsets (SDK_INT >= R)",
    "android/view/WindowInsets$Type": "BaseActivity.applyInsets (SDK_INT >= R)",
    "android/graphics/Insets": "BaseActivity.applyInsets (SDK_INT >= R)",
}


def load_api(path):
    classes = {}
    for c in ET.parse(path).getroot().iter("class"):
        since = int(c.get("since", "1"))
        supers = [e.get("name") for e in c.findall("extends")] + [e.get("name") for e in c.findall("implements")]
        members = {m.get("name"): int(m.get("since", since)) for m in c if m.tag in ("method", "field")}
        classes[c.get("name")] = (since, supers, members)
    return classes


def lookup(classes, cls, member, seen=None):
    seen = seen if seen is not None else set()
    if cls in seen or cls not in classes:
        return None
    seen.add(cls)
    since, supers, members = classes[cls]
    if member in members:
        return members[member]
    for s in supers:
        r = lookup(classes, s, member, seen)
        if r is not None:
            return r
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("dex", nargs="?", default=os.path.join(ROOT, "build/offline/dex/classes.dex"))
    ap.add_argument("--min-sdk", type=int, default=26)
    args = ap.parse_args()
    dexdump = os.path.join(SDK, "build-tools", BT, "dexdump")
    api_xml = os.path.join(SDK, "platforms", PLATFORM, "data", "api-versions.xml")
    for p in (args.dex, dexdump, api_xml):
        if not os.path.exists(p):
            sys.exit(f"missing: {p}")
    classes = load_api(api_xml)
    dump = subprocess.run([dexdump, "-d", args.dex], capture_output=True, text=True, check=True).stdout
    current, issues = None, {}
    for line in dump.splitlines():
        m = re.match(r"\s+Class descriptor\s+: 'L([^;]+);'", line)
        if m:
            current = m.group(1)
            continue
        if not current or not current.startswith(APP_PREFIX):
            continue
        m = re.search(r"(invoke-\S+|[si]get\S*|[si]put\S*) .*?, L([^;]+);\.([^:]+):(\S+)", line)
        if not m:
            continue
        kind, cls, name, sig = m.groups()
        if not cls.startswith(("android/", "java/", "javax/")):
            continue
        member = name + sig if kind.startswith("invoke") else name
        level = max(lookup(classes, cls, member) or 1, classes.get(cls, (1,))[0])
        if level > args.min_sdk:
            issues.setdefault((cls, member, level), set()).add(current.split("$")[0])
    unreviewed = 0
    for (cls, member, level), users in sorted(issues.items(), key=lambda x: (-x[0][2], x[0][0])):
        note = GUARDED.get(cls)
        if note is None:
            unreviewed += 1
        tag = f"guarded: {note}" if note else "UNREVIEWED"
        print(f"API {level:>2}  {cls}.{member}  <- {', '.join(sorted(users))}  [{tag}]")
    print(f"{len(issues)} usage(s) above minSdk {args.min_sdk}; {unreviewed} unreviewed")
    return 1 if unreviewed else 0


if __name__ == "__main__":
    sys.exit(main())
