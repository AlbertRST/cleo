#!/usr/bin/env bash
# rename-instance.sh — move a Cleo instance to a new directory WITHOUT losing its mind.
#
#   ./rename-instance.sh <old-dir> <new-dir> [--name "New Instance Name"]
#
# Claude Code keys an instance's memory AND session transcripts to the ABSOLUTE path
# (~/.claude/projects/<encoded-path>/). Renaming the folder without moving that store
# orphans the brain — instant amnesia. This script moves both together and updates the
# instance's stamp.
#
# ⚠️  Run this from OUTSIDE any live session in the instance. A running session keeps
# writing its transcript under the OLD path until it ends; renaming mid-session means
# that session's transcript is never ingested. Close the session first.

set -euo pipefail

OLD=""; NEW=""; NAME=""
while [ $# -gt 0 ]; do
  case "$1" in
    --name) NAME="$2"; shift 2 ;;
    -h|--help) sed -n '2,15p' "$0"; exit 0 ;;
    *) if [ -z "$OLD" ]; then OLD="$1"; elif [ -z "$NEW" ]; then NEW="$1"; else echo "unexpected arg: $1" >&2; exit 2; fi; shift ;;
  esac
done
[ -n "$OLD" ] && [ -n "$NEW" ] || { echo "usage: rename-instance.sh <old-dir> <new-dir> [--name N]" >&2; exit 2; }
[ -d "$OLD" ] || { echo "no such directory: $OLD" >&2; exit 2; }
[ ! -e "$NEW" ] || { echo "target already exists: $NEW" >&2; exit 2; }
OLD="$(readlink -f "$OLD")"
NEW="$(readlink -f -m "$NEW")"
[ "$OLD" != "$NEW" ] || { echo "old and new are the same path" >&2; exit 2; }
[ -f "$OLD/CLAUDE.md" ] && grep -q "CLEO-INSTANCE" "$OLD/CLAUDE.md" \
  || echo "warning: $OLD does not look like a stamped Cleo instance — continuing anyway" >&2

enc(){ printf '%s' "$1" | tr '/_' '--'; }
P="$HOME/.claude/projects"
OLDSTORE="$P/$(enc "$OLD")"
NEWSTORE="$P/$(enc "$NEW")"

echo "instance : $OLD -> $NEW"
echo "store    : $OLDSTORE -> $NEWSTORE"
echo

# 1. Move the instance directory.
mv "$OLD" "$NEW"
echo "  ✓ instance directory moved"

# 2. Move the brain (memory + transcripts) with it.
if [ -d "$OLDSTORE" ]; then
  if [ ! -e "$NEWSTORE" ]; then
    mv "$OLDSTORE" "$NEWSTORE"
    echo "  ✓ memory + transcript store moved"
  else
    # Target store exists (e.g. a session already ran in the new path): merge, never clobber.
    mkdir -p "$NEWSTORE/memory"
    n=0
    if [ -d "$OLDSTORE/memory" ]; then
      for f in "$OLDSTORE/memory"/*; do
        [ -e "$f" ] || continue
        [ -e "$NEWSTORE/memory/$(basename "$f")" ] || { mv "$f" "$NEWSTORE/memory/"; n=$((n+1)); }
      done
    fi
    for f in "$OLDSTORE"/*.jsonl; do
      [ -e "$f" ] || continue
      [ -e "$NEWSTORE/$(basename "$f")" ] || mv "$f" "$NEWSTORE/"
    done
    echo "  ✓ merged into existing store ($n memory files moved; nothing clobbered)"
    echo "  · leftovers (if any) remain in $OLDSTORE — review before deleting"
  fi
else
  echo "  · no store at $OLDSTORE (nothing to move)"
fi

# 3. Update the stamp and index headers.
if [ -n "$NAME" ]; then
  python3 - "$NEW" "$NAME" "$NEWSTORE" <<'PY'
import re, sys
from pathlib import Path
new, name, store = sys.argv[1], sys.argv[2], sys.argv[3]
c = Path(new) / "CLAUDE.md"
if c.exists():
    s = c.read_text(encoding="utf-8")
    s2 = re.sub(r"(<!-- CLEO-INSTANCE: )[^·]+( ·)", rf"\g<1>{name}\g<2>", s, count=1)
    if s2 != s: c.write_text(s2, encoding="utf-8"); print(f"  ✓ CLAUDE.md stamp -> {name}")
m = Path(store) / "memory" / "MEMORY.md"
if m.exists():
    s = m.read_text(encoding="utf-8")
    s2 = re.sub(r"^# Memory Index — .*$", f"# Memory Index — {name}", s, count=1, flags=re.M)
    if s2 != s: m.write_text(s2, encoding="utf-8"); print("  ✓ MEMORY.md index header renamed")
PY
fi

# 3b. Re-stamp the install path (memctl relocate reads it to self-heal) and drop the beacon
#     keyed to the old path; the next night run writes a fresh one under the new key.
V="$NEW/.cleo/VERSION"
if [ -f "$V" ]; then
  if grep -q '^path: ' "$V"; then sed -i "s|^path: .*|path: $NEW|" "$V"; else echo "path: $NEW" >> "$V"; fi
  echo "  ✓ .cleo/VERSION path -> $NEW"
fi
BUS="${CLEO_HOME:-$HOME/.cleo}"
rm -f "$BUS/spokes/$(enc "$OLD").json" 2>/dev/null || true
( cd "$NEW" && python3 .cleo/engine/memctl.py beacon >/dev/null 2>&1 ) && echo "  ✓ beacon re-emitted under the new path" || true

# 4. Verify the brain still answers from the new location.
echo
if ( cd "$NEW" && python3 .cleo/engine/memctl.py health 2>/dev/null | head -1 ); then
  echo "  ✓ engine answers from the new path"
else
  echo "  · could not verify engine (no .cleo/engine or no HEALTH yet)"
fi

cat <<DONE

Done. Open the next session in $NEW — Cleo wakes there with everything she had.
At that first wake she should update GOAL-STATE and any notes that mention the old path.
DONE
