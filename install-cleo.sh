#!/usr/bin/env bash
# install-cleo.sh — instantiate Cleo into a project.
#
#   ./install-cleo.sh <target-dir> [--name "Project Name"] [--partner-from <instance-dir>] [--hq] [--public] [--force]
#
# Installs the parts of Cleo that TRAVEL — doctrine, workflows, personality, memory engine —
# and creates an EMPTY project brain. Memory about other projects never comes along.
#
# Idempotent: re-running upgrades .cleo/ and the skills, and never clobbers project content
# (MISSION/MAP/DECISIONS/CLAUDE.md are written only if absent, unless --force).
#
# Every install is a SPOKE of the HQ layer (see HQ-LAYER.md): it emits a status beacon to
# ~/.cleo/spokes/ at every night run and links the shared partner store ~/.cleo/partner/.
# --hq marks this instance as the hub that renders the portfolio map.
# .cleo/ (doctrine, engine, VERSION, DORMANT) is local-only in EVERY instance: gitignored, and
# untracked (files kept on disk) if an older install tracked it. Commits the installer makes use
# the repo's own git identity, or its registered account's (GIT-GUARD.md); the vault never pushes.
# --public is for repos that are (or will be) published, e.g. portfolio projects: all of Cleo's
# machinery stays local-only. The bootstrap lives in .cleo/CLAUDE-BOOTSTRAP.md, imported by the
# first line of CLAUDE.md; hooks go to .claude/settings.local.json; the rituals, the core roster
# and ops/PARTNER-TASKS.md + ops/AUTHORITY.md are gitignored too. A public clone sees only the project's docs.
# Public is sticky: it is detected from CLAUDE.md's first line (committed, so it survives a clone)
# and from .cleo/VERSION. To go back to private, remove the import line and the `visibility:` line,
# then re-run. Converting a repo whose history already holds Cleo files prints a warning: untracking
# does not rewrite history, so publish from a fresh branch or scrub it first.

set -euo pipefail

BLUEPRINT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TARGET=""; NAME=""; PARTNER_FROM=""; FORCE=0; ROLE="spoke"; VISIBILITY="private"

while [ $# -gt 0 ]; do
  case "$1" in
    --name)         NAME="$2"; shift 2 ;;
    --partner-from) PARTNER_FROM="$2"; shift 2 ;;
    --force)        FORCE=1; shift ;;
    --hq)           ROLE="hq"; shift ;;
    --public)       VISIBILITY="public"; shift ;;
    -h|--help)      awk 'NR>1 && /^#/ {sub(/^# ?/, ""); print; next} NR>1 {exit}' "$0"; exit 0 ;;
    *)              TARGET="$1"; shift ;;
  esac
done

[ -n "$TARGET" ] || { echo "usage: install-cleo.sh <target-dir> [--name N] [--partner-from D] [--hq] [--public] [--force]" >&2; exit 2; }
mkdir -p "$TARGET"
TARGET="$(cd "$TARGET" && pwd)"
[ "$TARGET" != "$BLUEPRINT" ] || { echo "refusing to install the blueprint into itself" >&2; exit 2; }
[ -n "$NAME" ] || NAME="$(basename "$TARGET")"

TODAY="$(date +%F)"
VERSION="$(git -C "$BLUEPRINT" rev-parse --short HEAD 2>/dev/null || echo unversioned)"
ENCODED="$(printf '%s' "$TARGET" | tr '/_' '--')"
MEMDIR="$HOME/.claude/projects/$ENCODED/memory"

echo "Installing Cleo -> $TARGET"
echo "  name=$NAME  blueprint=$VERSION  memory=$MEMDIR"
echo

# --------------------------------------------------------------- 1. doctrine + engine (always refreshed)
mkdir -p "$TARGET/.cleo/blueprint" "$TARGET/.cleo/engine"
for f in CLEO-IDENTITY.md OPERATING-MODEL.md MEMORY-SYSTEM.md DOCS-SYSTEM.md HQ-LAYER.md ENGINEERING-STANDARDS.md AGENT-ROSTER.md GIT-GUARD.md README.md; do
  [ -f "$BLUEPRINT/$f" ] && cp "$BLUEPRINT/$f" "$TARGET/.cleo/blueprint/"
done
cp -r "$BLUEPRINT/templates" "$TARGET/.cleo/blueprint/"
cp "$BLUEPRINT"/engine/memctl.py "$BLUEPRINT"/engine/gitguard.py "$BLUEPRINT"/engine/hook.sh \
   "$BLUEPRINT"/engine/backup_vault.sh "$BLUEPRINT"/engine/LIBRARIAN.md "$TARGET/.cleo/engine/" 2>/dev/null || true
mkdir -p "$TARGET/.cleo/engine/tests" && cp "$BLUEPRINT"/engine/tests/*.py "$TARGET/.cleo/engine/tests/" 2>/dev/null || true
chmod +x "$TARGET/.cleo/engine/hook.sh" "$TARGET/.cleo/engine/backup_vault.sh" 2>/dev/null || true
if [ "$ROLE" = spoke ] && grep -qs '^role: hq' "$TARGET/.cleo/VERSION"; then ROLE="hq"; fi   # re-install keeps hq
IMPORT_LINE="@.cleo/CLAUDE-BOOTSTRAP.md"
if [ "$VISIBILITY" = private ] && grep -qs '^visibility: public' "$TARGET/.cleo/VERSION"; then VISIBILITY="public"; fi   # ...and public
first_line_is_import(){ [ -f "$1" ] && head -1 "$1" | tr -d '\r' | sed '1s/^\xEF\xBB\xBF//' | grep -qxF "$IMPORT_LINE"; }
if [ "$VISIBILITY" = private ] && first_line_is_import "$TARGET/CLAUDE.md"; then
  VISIBILITY="public"   # .cleo/VERSION is never committed; CLAUDE.md's import line is the committed marker
fi
printf 'name: %s\ninstalled: %s\nblueprint: %s\npath: %s\nrole: %s\nvisibility: %s\n' "$NAME" "$TODAY" "$VERSION" "$TARGET" "$ROLE" "$VISIBILITY" > "$TARGET/.cleo/VERSION"
BOOT="$TARGET/.cleo/CLAUDE-BOOTSTRAP.md"
sum_of(){ [ -e "$1" ] && md5sum < "$1" | cut -d' ' -f1 || echo absent; }
declare -A PRE_SUM=()
for f in CLAUDE.md .gitignore .claude/settings.json; do PRE_SUM[$f]="$(sum_of "$TARGET/$f")"; done
if [ "$VISIBILITY" = public ]; then BOOTFILE="$BOOT"; HOOKFILE="$TARGET/.claude/settings.local.json"; else BOOTFILE="$TARGET/CLAUDE.md"; HOOKFILE="$TARGET/.claude/settings.json"; fi
echo "  ✓ .cleo/blueprint + .cleo/engine"

# --------------------------------------------------------------- 2. workflows + personality (always refreshed)
mkdir -p "$TARGET/.claude/skills" "$TARGET/.claude/output-styles"
cp -r "$BLUEPRINT/templates/repo/.claude/skills/wake" "$TARGET/.claude/skills/"
cp -r "$BLUEPRINT/templates/repo/.claude/skills/sleep" "$TARGET/.claude/skills/"
cp "$BLUEPRINT/templates/repo/.claude/output-styles/cleo-concise.md" "$TARGET/.claude/output-styles/"
mkdir -p "$TARGET/.claude/agents"
cp "$BLUEPRINT/templates/repo/.claude/agents/"*.md "$TARGET/.claude/agents/"   # core roster; project agents untouched
echo "  ✓ /wake + /sleep skills, Cleo Concise voice, core agent roster ($(ls "$BLUEPRINT/templates/repo/.claude/agents/" | wc -l) agents)"

# settings.json: merge if one already exists, so a project's own settings survive.
SRC_SETTINGS="$BLUEPRINT/templates/repo/.claude/settings.json"
DST_SETTINGS="$HOOKFILE"
if [ "$VISIBILITY" = public ] && [ -f "$TARGET/.claude/settings.json" ]; then
  # Public repo: Cleo's hooks must not live in the committed settings.json. Move them out.
  python3 - "$TARGET/.claude/settings.json" <<'PY'
import json, os, sys
CLEO = (".cleo/engine/hook.sh", ".cleo/engine/backup_vault.sh")
path = sys.argv[1]; d = json.load(open(path)); before = json.dumps(d, sort_keys=True)
is_cleo = lambda h: isinstance(h, dict) and any(c in str(h.get("command", "")) for c in CLEO)
if d.get("outputStyle") == "Cleo Concise": d.pop("outputStyle")
hooks = d.get("hooks", {})
for ev in list(hooks):
    kept = []
    for e in hooks[ev]:
        if isinstance(e, dict) and isinstance(e.get("hooks"), list):
            inner = [h for h in e["hooks"] if not is_cleo(h)]   # drop only Cleo's own commands
            if not inner: continue
            e["hooks"] = inner
        kept.append(e)
    if kept: hooks[ev] = kept
    else: hooks.pop(ev)
if "hooks" in d and not d["hooks"]: d.pop("hooks")
if json.dumps(d, sort_keys=True) == before: sys.exit(0)
if d:
    tmp = path + ".tmp"; open(tmp, "w").write(json.dumps(d, indent=2) + "\n"); os.replace(tmp, path)
    print("  ✓ Cleo hooks moved out of the committed settings.json (project settings kept)")
else:
    os.remove(path); print("  ✓ settings.json held only Cleo hooks — removed (they live in settings.local.json)")
PY
fi
if [ -f "$DST_SETTINGS" ]; then
  python3 - "$SRC_SETTINGS" "$DST_SETTINGS" <<'PY'
import json, sys
src, dst = json.load(open(sys.argv[1])), json.load(open(sys.argv[2]))
dst.setdefault("outputStyle", src["outputStyle"])
hooks = dst.setdefault("hooks", {})
for event, entries in src["hooks"].items():
    have = json.dumps(hooks.get(event, []))
    for e in entries:
        if ".cleo/engine/hook.sh" not in have:          # don't double-register Cleo's hooks
            hooks.setdefault(event, []).append(e)
import os
tmp = sys.argv[2] + ".tmp"; open(tmp, "w").write(json.dumps(dst, indent=2) + "\n"); os.replace(tmp, sys.argv[2])
print(f"  ✓ {sys.argv[2].rsplit('/', 1)[-1]} merged (existing settings preserved)")
PY
else
  cp "$SRC_SETTINGS" "$DST_SETTINGS"; echo "  ✓ $(basename "$DST_SETTINGS") (SessionStart/SessionEnd hooks)"
fi

# --------------------------------------------------------------- 3. repo docs (never clobber real content)
for f in MISSION.md MAP.md DECISIONS.md; do
  if [ -f "$TARGET/$f" ] && [ "$FORCE" = 0 ]; then
    echo "  · $f exists — kept (use --force to replace)"
  else
    cp "$BLUEPRINT/templates/repo/$f" "$TARGET/$f"
    echo "  ✓ $f"
  fi
done
# CLAUDE.md is special: it must carry the auto-wake bootstrap or Cleo never wakes here. An
# existing file (a project's own conventions) is KEPT, but merged under the bootstrap:
#   - symlink (e.g. CLAUDE.md -> AGENTS.md): replaced by a real file that points at the target
#   - regular file without the Cleo stamp: bootstrap on top, original content appended below
#   - already stamped: left alone (re-install / upgrade)
C="$TARGET/CLAUDE.md"
if [ "$VISIBILITY" = public ]; then
  # Bootstrap goes to a local-only file; CLAUDE.md keeps only the project's conventions plus an
  # import line that does nothing in a public clone (the imported file is gitignored).
  cp "$BLUEPRINT/templates/repo/CLAUDE.md" "$BOOT"
  # Every bootstrap template this blueprint ever shipped, so an instance installed from an older
  # blueprint is still recognised exactly. Vendored copies without git history use the current one.
  TPL_DIR="$(mktemp -d)"; cp "$BLUEPRINT/templates/repo/CLAUDE.md" "$TPL_DIR/0.md"; n=1
  for h in $(git -C "$BLUEPRINT" log --format=%h -- templates/repo/CLAUDE.md 2>/dev/null); do
    git -C "$BLUEPRINT" show "$h:./templates/repo/CLAUDE.md" > "$TPL_DIR/$n.md" 2>/dev/null && n=$((n+1))
  done
  python3 - "$C" "$IMPORT_LINE" "$TPL_DIR" "$TARGET/.cleo/CLAUDE.md.pre-public" <<'PY'
import os, re, shutil, sys
from pathlib import Path
path, imp, tpl_dir, backup = sys.argv[1:5]
note = "<!-- Local agent bootstrap (gitignored). In a public clone this import resolves to nothing. -->"
STAMP = re.compile(r"[ \t]*<!-- CLEO-INSTANCE:.*?-->[ \t]*\n?", re.S)
templates = [STAMP.sub("", t.read_text().replace("\r\n", "\n")).strip("\n") for t in sorted(Path(tpl_dir).glob("*.md"))]
templates = [t for t in dict.fromkeys(templates) if t]
def top_trim(text):                     # drop our own import/note lines and blank lines at the top
    lines = text.splitlines(keepends=True)
    while lines and (lines[0].strip() in (imp, note) or not lines[0].strip()):
        lines.pop(0)
    return "".join(lines)
orig, body, warn = None, "", None
if os.path.islink(path):
    target = os.readlink(path); os.remove(path)
    body = f"# CLAUDE.md\n\nThis repo keeps its working conventions in `{target}`. Read it before touching the code.\n"
elif os.path.isfile(path):
    orig = open(path, newline="").read()
    body = orig.lstrip("\ufeff").replace("\r\n", "\n")
    if "CLEO-INSTANCE" in body or "run /wake AUTOMATICALLY" in body:   # inline bootstrap from a private install
        unstamped = STAMP.sub("", body)
        hit = next(((k, t) for t in templates if (k := unstamped.find(t)) != -1), None)
        if hit is None:
            warn = ("  ✗ CLAUDE.md holds an inline Cleo bootstrap that matches no template this blueprint ever\n"
                    "    shipped. CLAUDE.md was NOT changed and nothing was staged. Remove the bootstrap by hand\n"
                    "    (keep the project's conventions), then re-run with --public.")
            print(warn); sys.exit(0)
        k, t = hit   # remove exactly that template text; keep everything before and after it
        rest = unstamped[:k] + unstamped[k + len(t):]
        rest = re.sub(r"(\A|\n)---[ \t]*\n+## Project conventions \(pre-existing, kept verbatim\)[ \t]*\n+", "\\1", rest, count=1)
        body = top_trim(rest)
    body = top_trim(body)
new = f"{imp}\n{note}\n\n{body}" if body.strip() else f"{imp}\n{note}\n"
if orig is not None and new != orig:
    shutil.copyfile(path, backup)
    print(f"  ✓ backup of the previous CLAUDE.md: {os.path.relpath(backup, os.path.dirname(path))}")
if orig is None or new != orig:
    tmp = path + ".tmp"; open(tmp, "w").write(new); os.replace(tmp, path)
PY
  rm -rf "$TPL_DIR"
  first_line_is_import "$C" && echo "  ✓ CLAUDE.md imports the local-only bootstrap (.cleo/CLAUDE-BOOTSTRAP.md); project conventions kept"
elif [ -L "$C" ]; then
  LINK_TARGET="$(readlink "$C")"
  rm "$C"
  { cat "$BLUEPRINT/templates/repo/CLAUDE.md"
    printf '\n## Project conventions (pre-existing)\n\nThis repo keeps its own working conventions in `%s`. Read it before touching\nthe code; it is as binding as this file.\n' "$LINK_TARGET"
  } > "$C"
  echo "  ✓ CLAUDE.md (was a symlink to $LINK_TARGET — bootstrap written, conventions referenced)"
elif [ -f "$C" ] && ! grep -q "CLEO-INSTANCE" "$C" && [ "$FORCE" = 0 ]; then
  ORIG="$(cat "$C")"
  { cat "$BLUEPRINT/templates/repo/CLAUDE.md"
    printf '\n---\n\n## Project conventions (pre-existing, kept verbatim)\n\n'
    printf '%s\n' "$ORIG"
  } > "$C"
  echo "  ✓ CLAUDE.md (existing conventions kept below the Cleo bootstrap)"
elif [ -f "$C" ] && [ "$FORCE" = 0 ]; then
  echo "  · CLAUDE.md already stamped — kept"
else
  cp "$BLUEPRINT/templates/repo/CLAUDE.md" "$C"
  echo "  ✓ CLAUDE.md"
fi
mkdir -p "$TARGET/ops"
[ -f "$TARGET/ops/PARTNER-TASKS.md" ] || cp "$BLUEPRINT/templates/repo/ops/PARTNER-TASKS.md" "$TARGET/ops/"
[ -f "$TARGET/ops/AUTHORITY.md" ] || cp "$BLUEPRINT/templates/repo/ops/AUTHORITY.md" "$TARGET/ops/"

# Stamp the instance so a copy can always say what it is.
python3 - "$BOOTFILE" "$NAME" "$TODAY" "$VERSION" <<'PY'
import sys, re
path, name, today, ver = sys.argv[1:5]
s = open(path).read()
stamp = f"<!-- CLEO-INSTANCE: {name} · installed {today} · blueprint {ver} -->"
s2 = re.sub(r"<!-- CLEO-INSTANCE:.*?-->", stamp, s, count=1, flags=re.S)
if s2 == s and stamp not in s:
    s2 = s.rstrip() + "\n\n" + stamp + "\n"
open(path, "w").write(s2)
PY

# --------------------------------------------------------------- 4. the vault: a FRESH, EMPTY project brain
mkdir -p "$TARGET/private"/{memory,sessions,commitments,index,archive,raw/transcripts}
# Seeded notes carry real frontmatter: a fresh instance must lint CLEAN, or the /wake
# HEALTH gate cries wolf from day one and Cleo learns to ignore it.
[ -f "$TARGET/private/GOAL-STATE.md" ] || cat > "$TARGET/private/GOAL-STATE.md" <<GOAL
---
name: goal-state
type: goal-state
description: "Current direction for $NAME — phase, commitments, do-not list. Read first at every wake."
updated: $TODAY
pinned: true
fires_when: always
---
# GOAL-STATE — $NAME (regenerated at /sleep by Cleo only; <=40 lines)

**Phase:** not yet set — Cleo fills this at the first /sleep.

## Commitments
_none yet_

## Do not
_none yet_

## Last thread
Fresh instance installed $TODAY. No history yet.
GOAL
[ -f "$TARGET/private/SESSION-BACKLOG.md" ] || cat > "$TARGET/private/SESSION-BACKLOG.md" <<BACKLOG
---
name: session-backlog
type: reference
description: "Queue of planned work for $NAME, in current priority order"
updated: $TODAY
fires_when: "topic:backlog topic:next topic:plan"
---
# Session Backlog — $NAME

_empty — Cleo fills this as work is identified._
BACKLOG

# The vault is its own git repo and must never enter the project repo.
if [ ! -d "$TARGET/private/.git" ]; then
  git -C "$TARGET/private" init -q
  git -C "$TARGET/private" add -A >/dev/null 2>&1 || true
  git -C "$TARGET/private" -c user.email=cleo@local -c user.name=Cleo commit -qm "Vault initialised for $NAME" >/dev/null 2>&1 || true
fi
touch "$TARGET/.gitignore"
grep -qx "private/" "$TARGET/.gitignore" || printf 'private/\n' >> "$TARGET/.gitignore"
echo "  ✓ private/ vault (own git repo, gitignored from the project)"
# Private memory is never pushed: the vault's pre-push refuses everything (GIT-GUARD.md).
python3 "$TARGET/.cleo/engine/gitguard.py" install --never-push "$TARGET/private" 2>&1 | sed 's/^ */  /' || true

# --------------------------------------------------------------- 4b. project scaffold (ENGINEERING-STANDARDS.md)
# Every project is born with git, venv/, .env and a real .gitignore. Idempotent: only what is
# missing gets created; an existing .gitignore is extended, never rewritten.
if ! git -C "$TARGET" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  git -C "$TARGET" init -q && echo "  ✓ git initialised"
fi
for pat in 'venv/' '.env' '.env.*' '!.env.example' '*.db' '*.sqlite' '*.sqlite3' '__pycache__/' '*.pyc' \
           '.pytest_cache/' '.mypy_cache/' '.ruff_cache/' '*.log' '.DS_Store' '.idea/' '.vscode/*' '!.vscode/settings.json' '.claude/settings.local.json' '*.pdf' '*.PDF' '*.doc' '*.docx' '*.xls' '*.xlsx' '*.ppt' '*.pptx' '*.zip'; do
  grep -qxF -- "$pat" "$TARGET/.gitignore" || printf '%s\n' "$pat" >> "$TARGET/.gitignore"
done
echo "  ✓ .gitignore covers venv, .env, databases, caches"
# .cleo/ is Cleo's machinery and local-only in every instance, private or public (GIT-GUARD.md), as
# ~/.gitignore_global has it machine-wide. An older install may have re-included and tracked it.
if grep -qxF -e '!.cleo/' -e '!/.cleo/' "$TARGET/.gitignore"; then
  grep -vxF -e '!.cleo/' -e '!/.cleo/' "$TARGET/.gitignore" > "$TARGET/.gitignore.tmp" || true
  mv "$TARGET/.gitignore.tmp" "$TARGET/.gitignore"
fi
grep -qxF -- '.cleo/' "$TARGET/.gitignore" || printf '.cleo/\n' >> "$TARGET/.gitignore"
UNTRACK_CLEO=""
if [ -n "$(git -C "$TARGET" ls-files -- .cleo 2>/dev/null)" ]; then
  git -C "$TARGET" rm -r -q --cached -- .cleo && UNTRACK_CLEO=1
fi
echo "  ✓ .cleo/ local-only (gitignored${UNTRACK_CLEO:+; untracked now, files kept on disk})"
if [ "$VISIBILITY" = public ]; then
  CLEO_PATHS=('.cleo/' '.claude/skills/wake/' '.claude/skills/sleep/' '.claude/output-styles/cleo-concise.md' 'ops/PARTNER-TASKS.md' 'ops/AUTHORITY.md')
  for a in "$BLUEPRINT"/templates/repo/.claude/agents/*.md; do CLEO_PATHS+=(".claude/agents/$(basename "$a")"); done
  for pat in "${CLEO_PATHS[@]}"; do
    grep -qxF -- "$pat" "$TARGET/.gitignore" || printf '%s\n' "$pat" >> "$TARGET/.gitignore"
    # already tracked from an earlier private install? untrack (the file itself stays on disk)
    git -C "$TARGET" rm -r -q --cached --ignore-unmatch -- "${pat%/}" >/dev/null 2>&1 || true
  done
  echo "  ✓ public repo: Cleo's machinery gitignored (local-only)"
  if git -C "$TARGET" rev-parse -q --verify HEAD >/dev/null 2>&1; then
    # Stage the whole conversion together, so a commit can't ship half of it.
    # Stage only files this run changed AND that were already tracked; never stage an untracked
    # settings.json (it may hold personal permissions) or a CLAUDE.md still carrying the bootstrap.
    for f in CLAUDE.md .gitignore .claude/settings.json; do
      [ "$(sum_of "$TARGET/$f")" != "${PRE_SUM[$f]}" ] || continue
      git -C "$TARGET" ls-files --error-unmatch -- "$f" >/dev/null 2>&1 || continue
      if [ "$f" = CLAUDE.md ] && grep -q 'run /wake AUTOMATICALLY' "$TARGET/CLAUDE.md" 2>/dev/null; then continue; fi
      git -C "$TARGET" add -A -- "$f" >/dev/null 2>&1 || true
    done
    if ! git -C "$TARGET" diff --cached --quiet 2>/dev/null; then
      echo "  ✓ public conversion staged: review with 'git diff --cached' and commit it as one change"
    fi
    # Untracking does not rewrite history: say so loudly if Cleo files were ever committed.
    PLAIN=("${CLEO_PATHS[@]%/}"); LEAK=""
    [ -n "$(git -C "$TARGET" log --all --format=%h -1 -- "${PLAIN[@]}" .claude/settings.local.json 2>/dev/null)" ] && LEAK=1
    [ -n "$(git -C "$TARGET" log --all --format=%h -1 -G'\.cleo/engine/hook\.sh' -- .claude/settings.json 2>/dev/null)" ] && LEAK=1
    [ -n "$(git -C "$TARGET" log --all --format=%h -1 -G'run /wake AUTOMATICALLY' -- CLAUDE.md 2>/dev/null)" ] && LEAK=1
    if [ -n "$LEAK" ]; then
      echo "  ! WARNING: this repo's git history already contains Cleo files (machinery, hooks or the inline"
      echo "    bootstrap). Untracking does not remove them from history. Before publishing, push from a"
      echo "    fresh orphan branch or squashed history, or scrub with git filter-repo."
    fi
  fi
fi
if [ ! -f "$TARGET/.env" ]; then
  printf '# Secrets and local configuration. Never committed (see .gitignore). Copy to .env.example without values to document keys.\n' > "$TARGET/.env"
  echo "  ✓ .env created (empty)"
fi
if [ ! -d "$TARGET/venv" ]; then
  if python3 -m venv "$TARGET/venv" 2>/dev/null; then
    if "$TARGET/venv/bin/pip" install -q pyyaml pytest >/dev/null 2>&1; then
      echo "  ✓ venv/ created with pyyaml + pytest"
    else
      # Offline or no pip: fall back to a venv that can see the system packages so the engine still runs.
      rm -rf "$TARGET/venv" && python3 -m venv --system-site-packages "$TARGET/venv" \
        && echo "  ✓ venv/ created (system-site-packages fallback — pip install failed; add pyyaml + pytest when online)"
    fi
  else
    echo "  · could not create venv/ (python3 -m venv unavailable) — create it by hand"
  fi
fi
# Commits here are the repo's own: its configured identity, or its registered account's (GIT-GUARD.md).
python3 "$TARGET/.cleo/engine/gitguard.py" identity --repo "$TARGET" 2>&1 | sed 's/^ */  /' || true
if ! git -C "$TARGET" rev-parse HEAD >/dev/null 2>&1; then
  if git -C "$TARGET" add -A >/dev/null 2>&1 && git -C "$TARGET" commit -qm "Cleo installed: doctrine, rituals, engine, project scaffold" >/dev/null 2>&1; then
    echo "  ✓ initial commit"
  else
    echo "  ✗ initial commit failed (no git identity, or a hook refused it) — fix that, then: git -C \"$TARGET\" commit"
  fi
elif [ -n "$UNTRACK_CLEO" ] && [ "$VISIBILITY" = private ]; then
  # Commit only Cleo's own change: never with the partner's unfinished .gitignore edits or staged work.
  head_sum(){ if git -C "$TARGET" cat-file -e "HEAD:$1" 2>/dev/null; then git -C "$TARGET" show "HEAD:$1" | md5sum | cut -d' ' -f1; else echo absent; fi; }
  if [ "${PRE_SUM[.gitignore]}" = "$(head_sum .gitignore)" ] \
     && [ -z "$(git -C "$TARGET" diff --cached --name-only -- . ':(exclude).cleo')" ] \
     && git -C "$TARGET" add -- .gitignore \
     && git -C "$TARGET" commit -qm "Untrack .cleo/: Cleo's machinery is local-only in every instance (GIT-GUARD.md)" >/dev/null 2>&1; then
    echo "  ✓ committed: .cleo/ untracked"
  else
    echo "  · .cleo/ untracking is staged, not committed (.gitignore had your own edits, other changes are staged,"
    echo "    or a hook refused): review .gitignore and commit it together with the staged removal"
  fi
fi

# --------------------------------------------------------------- 5. seed ONLY the memories that travel
mkdir -p "$MEMDIR"
for m in cleo-identity self-improve-memory-and-docs orchestrator-owns-agent-process-lifecycle; do
  [ -f "$MEMDIR/$m.md" ] || cp "$BLUEPRINT/templates/memory/$m.md" "$MEMDIR/" 2>/dev/null || true
done
cp -n "$BLUEPRINT/templates/memory/_TEMPLATE.md" "$MEMDIR/" 2>/dev/null || true

if [ -n "$PARTNER_FROM" ]; then
  # The PARTNER travels between projects; their PROJECTS do not.
  #
  # 🔑 `type:` alone does NOT tell you what is portable. A note typed `feedback` can still be
  # pure project matter ("Cilio is read-only", "he reloads PA by hand"). Auto-copying by type
  # is exactly how a fresh brain gets contaminated with another project's facts. So candidates
  # are STAGED for review, never installed silently — Cleo promotes them at the first wake.
  SRC_ENC="$(printf '%s' "$(cd "$PARTNER_FROM" && pwd)" | tr '/_' '--')"
  SRC_MEM="$HOME/.claude/projects/$SRC_ENC/memory"
  SRC_NAME="$(basename "$PARTNER_FROM")"
  mkdir -p "$MEMDIR/_carry"
  n=0
  for f in "$SRC_MEM"/*.md; do
    [ -f "$f" ] || continue
    b="$(basename "$f")"
    case "$b" in MEMORY.md|_TEMPLATE.md|project_*|project-*) continue ;; esac
    grep -qE '^[[:space:]]*type:[[:space:]]*(user|feedback)' "$f" 2>/dev/null || continue
    cp -n "$f" "$MEMDIR/_carry/" && n=$((n+1))
  done
  python3 - "$MEMDIR" "$SRC_NAME" "$n" <<'PYCARRY'
import re, sys
from pathlib import Path
d, src, n = Path(sys.argv[1]), sys.argv[2], sys.argv[3]
rows = []
for f in sorted((d / "_carry").glob("*.md")):
    t = f.read_text(encoding="utf-8", errors="replace")
    m = re.search(r"^description:\s*[\"']?(.+?)[\"']?\s*$", t, re.M)
    rows.append(f"- `{f.name}` — {m.group(1).strip() if m else 'no description'}")
(d / "CARRY-CANDIDATES.md").write_text(
    f"# Carry candidates from `{src}` — REVIEW BEFORE PROMOTING\n\n"
    f"{n} `user`/`feedback` notes were staged in `_carry/`. They are NOT active memory:\n"
    "nothing here is indexed or loaded until you move it up one directory.\n\n"
    "**At the first /wake, judge each one:** does it describe *the partner and how they want\n"
    "Cleo to work* (portable — promote), or *the other project* (leave it; it belongs there)?\n"
    "A note typed `feedback` can still be pure project matter. Delete this file when done.\n\n"
    + "\n".join(rows) + "\n", encoding="utf-8")
print(f"  ✓ {n} candidate partner memories STAGED in _carry/ for review (none active yet)")
PYCARRY
else
  # Seed the blank partner template ONLY when no shared partner store exists yet; otherwise
  # `memctl partner link` (below) symlinks the real partner model and a template would conflict.
  if [ ! -f "${CLEO_HOME:-$HOME/.cleo}/partner/partner-role-and-preferences.md" ]; then
    [ -f "$MEMDIR/partner-role-and-preferences.md" ] || \
      cp "$BLUEPRINT/templates/memory/partner-role-and-preferences.md" "$MEMDIR/" 2>/dev/null || true
  fi
fi

# Rebuild the index from whatever actually landed — never ship a stale index.
python3 - "$MEMDIR" "$NAME" <<'PY'
import re, sys
from pathlib import Path
d, name = Path(sys.argv[1]), sys.argv[2]
rows = []
for f in sorted(d.glob("*.md")):
    if f.name in ("MEMORY.md", "_TEMPLATE.md", "CARRY-CANDIDATES.md"):
        continue
    t = f.read_text(encoding="utf-8", errors="replace")
    m = re.search(r"^description:\s*[\"']?(.+?)[\"']?\s*$", t, re.M)
    title = (re.search(r"^name:\s*(.+)$", t, re.M) or [None, f.stem])[1].strip()
    rows.append(f"- [{title}]({f.name}) — {m.group(1).strip() if m else 'no description'}")
(d / "MEMORY.md").write_text(
    f"# Memory Index — {name}\n\n"
    "Identity and working doctrine carried from the blueprint. Project facts start empty\n"
    "and are written by Cleo as this project produces them.\n\n" + "\n".join(rows) + "\n",
    encoding="utf-8")
print(f"  ✓ memory seeded: {len(rows)} portable memories, 0 project memories")
PY

# Link the shared partner model (one file per fact, symlinked from ~/.cleo/partner/ so every
# instance reads and corrects the SAME partner notes). No store yet -> nothing happens.
( cd "$TARGET" && python3 .cleo/engine/memctl.py partner link 2>/dev/null | sed 's/^/  /' ) || true

# --------------------------------------------------------------- 6. prime the brain
# Establish a HEALTH baseline. Without this a brand-new instance reports STALE at its very
# first wake, and a gate that cries wolf on day one is a gate Cleo learns to ignore by day three.
( cd "$TARGET" && python3 .cleo/engine/memctl.py night --quick >/dev/null 2>&1 ) \
  && echo "  ✓ memory primed — HEALTH baseline established, beacon emitted to ~/.cleo/spokes/" \
  || echo "  · could not prime HEALTH (harmless; first /sleep will do it)"

# --------------------------------------------------------------- 7. verify
echo
echo "Verifying:"
FAIL=0
chk(){ if eval "$2" >/dev/null 2>&1; then echo "  ✓ $1"; else echo "  ✗ $1"; FAIL=1; fi; }
chk "CLAUDE.md present and stamped"      "grep -q 'CLEO-INSTANCE' '$BOOTFILE'"
chk "auto-wake rule present"             "grep -q 'run /wake AUTOMATICALLY' '$BOOTFILE'"
chk "/wake + /sleep installed"           "[ -f '$TARGET/.claude/skills/wake/SKILL.md' ] && [ -f '$TARGET/.claude/skills/sleep/SKILL.md' ]"
chk "hooks wired"                        "grep -q 'hook.sh' '$HOOKFILE'"
chk "engine runs"                        "cd '$TARGET' && python3 .cleo/engine/memctl.py --help"
chk "vault present + gitignored"         "[ -d '$TARGET/private/.git' ] && grep -qx 'private/' '$TARGET/.gitignore'"
chk "vault refuses every push (gitguard)" "python3 '$TARGET/.cleo/engine/gitguard.py' check --repo '$TARGET/private'"
chk ".cleo/ local-only (ignored, untracked)" "git -C '$TARGET' check-ignore -q .cleo/VERSION && [ -z \"\$(git -C '$TARGET' ls-files -- .cleo)\" ]"
chk "memory index built"                 "[ -s '$MEMDIR/MEMORY.md' ]"
chk "no foreign project memory"          "! ls '$MEMDIR'/project_* >/dev/null 2>&1"
chk "vault lints clean"                  "cd '$TARGET' && python3 .cleo/engine/memctl.py lint --quick"
chk "HEALTH reports ok"                  "cd '$TARGET' && python3 .cleo/engine/memctl.py health | grep -q '^ok'"
chk "VERSION stamped with path + role"   "grep -q '^path: ' '$TARGET/.cleo/VERSION' && grep -q '^role: ' '$TARGET/.cleo/VERSION'"
chk "project scaffold: git + venv + .env + .gitignore" "git -C '$TARGET' rev-parse HEAD && [ -d '$TARGET/venv' ] && [ -f '$TARGET/.env' ] && grep -qx 'venv/' '$TARGET/.gitignore' && grep -qx '.env' '$TARGET/.gitignore'"
chk "engine runs from venv"              "cd '$TARGET' && venv/bin/python .cleo/engine/memctl.py --help"
chk "core agent roster installed"        "[ -f '$TARGET/.claude/agents/builder.md' ] && [ -f '$TARGET/.claude/agents/reviewer.md' ]"
if [ "$VISIBILITY" = public ]; then
chk "public: CLAUDE.md imports bootstrap" "first_line_is_import '$TARGET/CLAUDE.md'"
chk "public: no inline bootstrap in CLAUDE.md" "! grep -q 'run /wake AUTOMATICALLY' '$TARGET/CLAUDE.md' && ! git -C '$TARGET' grep -q --cached -F 'run /wake AUTOMATICALLY' -- CLAUDE.md"
chk "public: Cleo machinery untracked"   "[ -z \"\$(git -C '$TARGET' ls-files -- .cleo .claude/skills/wake .claude/skills/sleep .claude/settings.local.json .claude/output-styles/cleo-concise.md ops/PARTNER-TASKS.md ops/AUTHORITY.md \$(cd '$BLUEPRINT/templates/repo' && ls .claude/agents/*.md))\" ]"
chk "public: no Cleo hooks in the index"  "! git -C '$TARGET' grep -q --cached -F '.cleo/engine/hook.sh' -- .claude/settings.json"
fi
chk "beacon emitted"                     "[ -f \"\${CLEO_HOME:-$HOME/.cleo}/spokes/$ENCODED.json\" ]"


echo
if [ "$FAIL" = 0 ]; then
  cat <<DONE
Cleo is installed in $TARGET.

Next: open a Claude Code session there. She will run /wake on her own — you do not
need to tell her who she is. Fill in MISSION.md when you know what this project is for.
This instance is enrolled as a $ROLE: HQ sees it in the portfolio map from its next wake.
To pause reporting without uninstalling: touch $TARGET/.cleo/DORMANT
DONE
  if [ -n "$(git -C "$TARGET" remote 2>/dev/null)" ]; then
    GG="${CLEO_HOME:-$HOME/.cleo}/bin/gitguard.py"
    echo
    echo "This project has a remote. Bind it to its GitHub account (an SSH host-alias URL) before pushing (GIT-GUARD.md):"
    for r in $(git -C "$TARGET" remote); do
      printf '  python3 %q register --repo %q --account <account> --remote %q --visibility %s\n' \
        "$GG" "$TARGET" "$(git -C "$TARGET" remote get-url --push "$r")" "$VISIBILITY"
    done
  fi
else
  echo "Install finished with failures above — fix before relying on this instance." >&2
  exit 1
fi
