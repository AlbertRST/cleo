#!/usr/bin/env bash
# Vault backup — LOCAL ONLY. Copies private/ (incl. its .git) as a tar.gz to a Windows folder
# that is NOT under OneDrive/Desktop/Documents (no sync client). Keeps the last 8 snapshots.
# Usage: backup_vault.sh [--if-older-than DAYS]   (default: always run)
set -u
ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"
DEST="${CLEO_BACKUP_DIR:-/mnt/c/CleoBackups/vault}"
KEEP=8
older=0
[ "${1:-}" = "--if-older-than" ] && older="${2:-7}"
mkdir -p "$DEST" || { echo "backup: cannot create $DEST" >&2; exit 1; }
if [ "$older" -gt 0 ]; then
  last=$(ls -1t "$DEST"/vault-*.tar.gz 2>/dev/null | head -1)
  if [ -n "$last" ] && [ -z "$(find "$last" -mtime +"$older" 2>/dev/null)" ]; then
    echo "backup: last snapshot newer than $older days — skipped"; exit 0; fi
fi
stamp=$(date +%Y-%m-%d_%H%M)
out="$DEST/vault-$stamp.tar.gz"
tar -C "$ROOT" -czf "$out" private && echo "backup: wrote $out ($(du -h "$out" | cut -f1))"
ls -1t "$DEST"/vault-*.tar.gz | tail -n +$((KEEP+1)) | xargs -r rm -f
