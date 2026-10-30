#!/usr/bin/env bash
# Vault backup — LOCAL ONLY. Copies private/ (incl. its .git) as a tar.gz to a Windows folder
# that is NOT under OneDrive/Desktop/Documents (no sync client). Each instance writes into its
# own subfolder $DEST/<slug>/ and keeps the last 8 snapshots there. Fails loudly (stderr +
# non-zero exit) and never leaves a partial or empty archive behind.
# Layout assumed: <instance>/.cleo/engine/backup_vault.sh  ->  ROOT = two levels up.
# Usage: backup_vault.sh [--if-older-than DAYS]   (default: always run)
set -u
fail(){ echo "backup: $*" >&2; exit 1; }
ROOT="$(cd "$(dirname "$0")/../.." && pwd)" || fail "cannot resolve the instance root"
DEST="${CLEO_BACKUP_DIR:-/mnt/c/CleoBackups/vault}"
KEEP=8
older=0
[ "${1:-}" = "--if-older-than" ] && older="${2:-7}"
[ -d "$ROOT/private" ] || fail "no vault at $ROOT/private — nothing backed up"

# Instance slug: the FIRST unindented name: line of .cleo/VERSION, else the folder name; lowercase,
# non-alnum -> '-'. memctl.py's backup_dir mirrors this rule (the wake pack's BACKUP line): keep in sync.
# ROOT comes from `pwd` (logical path); memctl uses the resolved root — they differ only under a symlink.
slugify(){ printf '%s' "$1" | tr '[:upper:]' '[:lower:]' | tr -cs 'a-z0-9' '-' | sed 's/^-*//; s/-*$//'; }
slug=$(slugify "$(sed -n 's/^name:[[:space:]]*//p' "$ROOT/.cleo/VERSION" 2>/dev/null | head -1)")
[ -n "$slug" ] || slug=$(slugify "$(basename "$ROOT")")
[ -n "$slug" ] || fail "cannot derive an instance name for $ROOT"
SUB="$DEST/$slug"
mkdir -p "$SUB" || fail "cannot create $SUB"
# A SIGKILLed run cannot fire its EXIT trap: sweep temp files older than a day (never a live run's).
find "$SUB" -maxdepth 1 -name '.vault-*' -mmin +1440 -delete

# The age gate looks only at this instance's snapshots (legacy flat files in $DEST are ignored).
if [ "$older" -gt 0 ]; then
  last=$(ls -1t "$SUB"/vault-*.tar.gz 2>/dev/null | head -1)
  if [ -n "$last" ] && [ -z "$(find "$last" -mtime +"$older" 2>/dev/null)" ]; then
    echo "backup: last $slug snapshot newer than $older days — skipped"; exit 0; fi
fi

# Serialise with the memory engine: `night` runs as a parallel SessionEnd hook and holds
# private/.git/night.lock while it writes the vault. Wait for it so tar never reads a vault
# mid-write; if it stays busy, fail (the gate stays open, so the next session retries).
if [ -d "$ROOT/private/.git" ] && command -v flock >/dev/null 2>&1; then
  exec 9>>"$ROOT/private/.git/night.lock" || fail "cannot open $ROOT/private/.git/night.lock"
  flock -w "${CLEO_BACKUP_LOCK_WAIT:-90}" 9 || fail "vault busy (night.lock held) — no snapshot written"
fi

# Atomic write: tar into a hidden temp file, verify it, then rename it into place.
# The EXIT trap removes the temp file on every failure path (HUP/INT/TERM included).
stamp=$(date +%Y-%m-%d_%H%M)
out="$SUB/vault-$stamp.tar.gz"
tmp=$(mktemp "$SUB/.vault-$stamp.XXXXXX") || fail "cannot create a temp file in $SUB"
trap 'rm -f "$tmp"' EXIT
trap 'exit 1' HUP INT TERM
tar -C "$ROOT" -czf "$tmp" private || fail "tar failed for $ROOT/private — no snapshot written"
[ -s "$tmp" ] || fail "archive is empty — no snapshot written"
tar -tzf "$tmp" private/ >/dev/null 2>&1 || fail "archive does not list private/ — no snapshot written"
mv -f "$tmp" "$out" || fail "cannot move the archive to $out"
echo "backup: wrote $out ($(du -h "$out" | cut -f1))"

# Rotation only after a successful write, and only inside this instance's subfolder.
ls -1t "$SUB"/vault-*.tar.gz | tail -n +$((KEEP+1)) | while IFS= read -r old; do rm -f -- "$old"; done
exit 0
