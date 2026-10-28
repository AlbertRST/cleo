#!/usr/bin/env bash
# Claude Code hook wrapper for memctl. Never exits non-zero — a failing hook must never
# block a session. Errors go to <vault>/index/hook-errors.log.
#
# Layout assumed: <project>/.cleo/engine/hook.sh  ->  ROOT = two levels up.
# Usage: hook.sh wakepack --ingest-missing --print   |   hook.sh night --quick
# If Claude Code pipes hook JSON on stdin, session_id is passed as --exclude-sid to
# wakepack so the just-started session is not reported as an un-rendered transcript.

set -u
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
VAULT="${MEMCTL_VAULT:-$ROOT/private}"
LOG="$VAULT/index/hook-errors.log"
mkdir -p "$VAULT/index" 2>/dev/null

cd "$ROOT" || { echo "hook.sh: cannot cd to $ROOT" >>"$LOG"; exit 0; }

# Prefer the project venv, else whatever python is on PATH.
if [ -f venv/bin/activate ]; then
  # shellcheck disable=SC1091
  source venv/bin/activate
elif [ -f .venv/bin/activate ]; then
  # shellcheck disable=SC1091
  source .venv/bin/activate
fi
PY="$(command -v python3 || command -v python)" || { echo "hook.sh: no python" >>"$LOG"; exit 0; }
# A venv without PyYAML must not take the engine down: fall back to the system interpreter.
"$PY" -c 'import yaml' 2>/dev/null || PY="$(ls /usr/bin/python3 /usr/local/bin/python3 2>/dev/null | head -1)"
[ -n "$PY" ] || { echo "hook.sh: no python with PyYAML" >>"$LOG"; exit 0; }

EXTRA=()
if [ "${1:-}" = "wakepack" ] && [ ! -t 0 ]; then
  # Hook JSON (if any) arrives on stdin; never block if nothing is piped.
  STDIN="$(timeout 1 cat 2>/dev/null || true)"
  if [ -n "$STDIN" ]; then
    SID="$(printf '%s' "$STDIN" | "$PY" -c 'import json,sys
try: print(json.load(sys.stdin).get("session_id",""))
except Exception: pass' 2>/dev/null || true)"
    [ -n "$SID" ] && EXTRA=(--exclude-sid "$SID")
  fi
fi

ERR="$(mktemp)"
if "$PY" .cleo/engine/memctl.py "$@" "${EXTRA[@]}" 2>"$ERR"; then
  rm -f "$ERR"
  exit 0
fi
{
  echo "$(date -Is) hook.sh $* failed:"
  cat "$ERR"
} >>"$LOG"
rm -f "$ERR"
# Degraded but useful: serve the last good pack rather than nothing.
if [ "${1:-}" = "wakepack" ] && [ -f "$VAULT/index/WAKE-PACK.md" ]; then
  cat "$VAULT/index/WAKE-PACK.md"
fi
exit 0
