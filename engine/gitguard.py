#!/usr/bin/env python3
"""gitguard — make a wrong-identity, wrong-remote or private-memory push impossible by accident.

Single-file CLI, Python 3.9+ standard library only: it runs inside repos that have no venv.
Spec: GIT-GUARD.md.

Subcommands: account, register, identity, install, check, status, and `hook` (the entry point
the pre-commit / pre-push shims call). The registry is local-only: $CLEO_HOME/git-identities.json
(default ~/.cleo/). The guard blocks; it never rewrites history and never prints commands that do.
A guardrail, not a security boundary: `--no-verify` skips hooks by git's design.
"""
from __future__ import annotations

import argparse
import fnmatch
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
from itertools import chain
from pathlib import Path

# --------------------------------------------------------------------------- constants
HOOKS = ("pre-commit", "pre-push")
SHIM_MARK = "# gitguard-shim"
LOCAL_DIR = "local"  # a foreign hook moves to <hooks>/local/<hook>: same basename for dispatchers
VISIBILITIES = ("public", "private")
NOREPLY = "@users.noreply.github.com"
SECRET_GLOBS = ("*.pem", "*.key", "id_rsa*", "id_ed25519*", "*.pgpass")
FIRES_RE = re.compile(r"^\s*fires_when\s*:")
SOURCES_RE = re.compile(r"^\s*sources\s*:(.*)$")
PROVENANCE_RE = re.compile(r"\b(?:raw|pending):|private/sessions/")
IDENT_EMAIL_RE = re.compile(r"<([^>]*)>")
SSH_URL_RE = re.compile(r"^ssh://(?:[^@/]+@)?(\[[^\]/]+\]|[^:/]+)(?::\d+)?/")
SCP_RE = re.compile(r"^(?:[^@/:]+@)?([^:/]+):")
REPO_PATH_RE = re.compile(r"(?i)^(?:[a-z+]+://(?:[^@/]+@)?[^/]+/|(?:[^@/]+@)?[^:/]+:)(.+?)(?:\.git)?/?$")
C_ESCAPES = {"a": 7, "b": 8, "t": 9, "n": 10, "v": 11, "f": 12, "r": 13, '"': 34, "\\": 92}
# Every `git log`: root commits always diffed, no signature lines. LOG_DIFF adds merges as combined
# diffs (what a merge itself introduces); LOG_FORMAT prints no diff at all.
GIT_LOG = ("log", "--root", "--no-show-signature", "--no-color")
LOG_DIFF = (*GIT_LOG, "--diff-merges=dense-combined")
LOG_FORMAT = (*GIT_LOG, "--no-patch")
DIFF_ARGS = ("--no-ext-diff", "--no-textconv", "--no-renames", "--no-relative", "--diff-filter=d",
             "--src-prefix=a/", "--dst-prefix=b/")
PATCH_ARGS = ("-U0", "--text", *DIFF_ARGS)  # --text: binary-looking files (*.csv -diff, UTF-16) are read too
BLOB_LIMIT = 1 << 20  # vault notes are small; larger blobs are not read for frontmatter
MAX_BLOCKS = 10
MAX_SHAS = 5
DENIED_PATH = "<a path whose name is denylisted>"
HISTORY_FIX = ("history rewrite needed — back up affected files first; rewrite only these commits "
               "(git filter-repo); never --no-verify; or ask Cleo")

Block = tuple[str, str]       # (what was blocked and why, the fix)
Check = tuple[bool, str, str]  # (passed, short label, detail — with the fix when it failed)
Remotes = dict[str, tuple[list[str], list[str]]]  # name -> (fetch URLs, push URLs)

SHIM_HEAD = """#!/bin/sh
{mark} {hook} — written by `gitguard.py install`; re-run install instead of editing.
# Runs the guard, then this repo's own hook ({local}/{hook} next to this file) if it has one.
DEFAULT_BUS={bus}
SRC={src}
export CLEO_HOME="${{CLEO_HOME:-$DEFAULT_BUS}}"
GG="$CLEO_HOME/bin/gitguard.py"
LOCAL="$(dirname "$0")/{local}/{hook}"
if ! python3 -c 'import sys; sys.exit(sys.version_info < (3, 9))' 2>/dev/null; then
  echo "gitguard: BLOCKED {hook} — the guard needs python3 >= 3.9 (found: $(python3 -V 2>&1))" >&2
  echo "  fix: install Python 3.9+ as python3 (the guard needs only its standard library)" >&2
  exit 1
fi
if [ ! -f "$GG" ]; then
  echo "gitguard: BLOCKED {hook} — the guard is missing: $GG" >&2
  echo "  fix: python3 $SRC install --repo \\"$(git rev-parse --show-toplevel)\\"" >&2
  exit 1
fi
"""
SHIM_BODY = {
    "pre-commit": """python3 "$GG" hook pre-commit || exit $?
[ -x "$LOCAL" ] || exit 0
exec "$LOCAL" "$@"
""",
    "pre-push": """IN="$(mktemp)" || exit 1
trap 'rm -f "$IN"' EXIT
cat > "$IN" || exit 1
python3 "$GG" hook pre-push "$@" < "$IN" || exit $?
[ -x "$LOCAL" ] || exit 0
"$LOCAL" "$@" < "$IN"
""",
}
NEVER_PUSH_SHIM = """#!/bin/sh
{mark} never-push — written by `gitguard.py install --never-push`; re-run install instead of editing.
# Private Cleo memory never leaves this machine: every push is refused, no Python needed.
echo "gitguard: BLOCKED push of $(pwd) — private Cleo memory (a vault or the bus) never leaves this machine" >&2
if [ "$1" = "$2" ]; then
  echo "  fix: do not push it" >&2
else
  echo "  fix: do not push it; drop the remote: git -C \\"$(pwd)\\" remote remove $1" >&2
fi
exit 1
"""


class GuardError(RuntimeError):
    """A failure whose message is meant for the person at the terminal."""


# --------------------------------------------------------------------------- locations
def bus_root() -> Path:
    """Home-level bus shared by every Cleo instance on this machine (CLEO_HOME overrides)."""
    override = os.environ.get("CLEO_HOME")
    return Path(override).expanduser() if override else Path.home() / ".cleo"


def registry_path() -> Path:
    return bus_root() / "git-identities.json"


def denylist_path() -> Path:
    return bus_root() / "guard-denylist.txt"


def machine_copy() -> Path:
    """The copy every shim calls, so hooks never depend on any project's .cleo/."""
    return bus_root() / "bin" / "gitguard.py"


def q(value: object) -> str:
    return shlex.quote(str(value))


def gg_cmd() -> str:
    return f"python3 {q(machine_copy())}"


def short(shas: list[str]) -> str:
    listed = ", ".join(s[:12] for s in shas[:MAX_SHAS])
    return listed + (f" (+{len(shas) - MAX_SHAS} more)" if len(shas) > MAX_SHAS else "")


# --------------------------------------------------------------------------- registry
def load_registry() -> dict:
    path = registry_path()
    data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    for key, empty in (("accounts", {}), ("repos", {}), ("never_push", [])):
        data.setdefault(key, empty)
    return data


def save_registry(data: dict) -> None:
    path = registry_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def load_denylist() -> list[tuple[int, str]]:
    """(line number, lower-cased literal) per term; blank lines and `#` lines are skipped."""
    path = denylist_path()
    if not path.exists():
        return []
    terms = []
    for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        term = line.strip()
        if term and not term.startswith("#"):
            terms.append((n, term.lower()))
    return terms


def version_visibility(key: Path) -> str:
    """The `visibility:` an instance records in its own .cleo/VERSION ("" when there is none)."""
    try:
        m = re.search(r"^visibility:\s*(\S+)", (key / ".cleo" / "VERSION").read_text(encoding="utf-8"), re.M)
    except OSError:
        return ""
    return m.group(1) if m else ""


def is_public(key: Path, entry: dict) -> bool:
    """The stricter of the registry and the instance's .cleo/VERSION."""
    return entry.get("visibility") == "public" or version_visibility(key) == "public"


def noreply_problem(key: Path, entry: dict, acct: dict) -> str | None:
    if is_public(key, entry) and not acct["email"].lower().endswith(NOREPLY):
        return (f"{key} is public, so it commits with its account's GitHub noreply email (<id>+<login>{NOREPLY}); "
                f"account {entry['account']} has {acct['email']}")
    return None


def noreply_fix(name: str, acct: dict) -> str:
    return f"{gg_cmd()} account --name {q(name)} --email '<id>+<login>{NOREPLY}' --ssh-host {q(acct['ssh_host'])}"


# --------------------------------------------------------------------------- git
def run_git(repo: Path, *args: str, input: str | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, input=input,
                          encoding="utf-8", errors="replace")


def git(repo: Path, *args: str, input: str | None = None) -> str:
    proc = run_git(repo, *args, input=input)
    if proc.returncode != 0:
        raise GuardError(f"git {' '.join(args)} failed in {repo}: {proc.stderr.strip()}")
    return proc.stdout


def git_lines(repo: Path, *args: str):
    """Stream a git command's output line by line, split on \\n only (a lone \\r stays in its line)."""
    with tempfile.TemporaryFile() as err:
        proc = subprocess.Popen(["git", "-C", str(repo), *args], stdout=subprocess.PIPE, stderr=err)
        with proc.stdout:
            for raw in proc.stdout:
                yield raw.decode("utf-8", "replace").rstrip("\n")
        if proc.wait() != 0:
            err.seek(0)
            raise GuardError(f"git {' '.join(args)} failed in {repo}: {err.read().decode('utf-8', 'replace').strip()}")


def common_dir(repo: Path) -> Path:
    return Path(git(repo, "rev-parse", "--path-format=absolute", "--git-common-dir").strip()).resolve()


def repo_key(path: Path) -> Path:
    """A repo's registry key: its main worktree, so linked worktrees share one entry."""
    common = common_dir(path)
    if common.name == ".git":
        return common.parent
    top = run_git(path, "rev-parse", "--show-toplevel")
    return Path(top.stdout.strip()).resolve() if top.returncode == 0 else common


def logical_path(start: str | None, key: Path) -> Path | None:
    """The un-resolved path that reaches key from start (a symlinked private/ resolves elsewhere)."""
    if not start:
        return None
    path = Path(os.path.abspath(os.path.expanduser(start)))
    for candidate in (path, *path.parents):
        try:
            if os.path.samefile(candidate, key):
                return candidate
        except OSError:
            continue
    return None


def hooks_dir(repo: Path) -> Path:
    """Where git runs this repo's hooks from: core.hooksPath if set, else <common dir>/hooks."""
    return Path(git(repo, "rev-parse", "--path-format=absolute", "--git-path", "hooks").strip())


def remote_urls(repo: Path) -> Remotes:
    return {name: (git(repo, "remote", "get-url", "--all", name).splitlines(),
                   git(repo, "remote", "get-url", "--push", "--all", name).splitlines())
            for name in git(repo, "remote").split()}


def all_are(urls: list[str], registered: str) -> bool:
    return bool(urls) and all(norm_url(u) == norm_url(registered) for u in urls)


def pushes_to(remotes: Remotes, registered: str) -> list[str]:
    """Remotes whose every push URL is the registered remote: `git push <name>` goes there."""
    return [name for name, (_, push) in remotes.items() if all_are(push, registered)]


def tracks(remotes: Remotes, registered: str) -> list[str]:
    """Remotes that ARE the registered remote: every fetch and push URL is it, so their tracking refs
    (filled from the fetch URL) show what the registered remote already holds."""
    return [name for name, (fetch, push) in remotes.items() if all_are(fetch, registered) and all_are(push, registered)]


def local_only_reason(key: Path, reg: dict, alias: Path | None = None) -> str | None:
    """Why a repo must never be pushed (never_push list, a Cleo vault, the bus), or None."""
    if key in {Path(p).resolve() for p in reg["never_push"]}:
        return "it is on the never_push list"
    if key == bus_root().resolve():
        return "it is the Cleo bus"
    for path in (key, alias):
        if path is not None and path.name == "private" and (path.parent / ".cleo" / "VERSION").is_file():
            return "it is a Cleo vault"
    return None


def norm_url(url: str) -> str:
    """Compare remotes by meaning: ssh://u@h/p == u@h:p, and a trailing / or .git is ignored."""
    u = url.strip().rstrip("/")
    u = u[:-4] if u.endswith(".git") else u
    m = re.match(r"ssh://([^/]+)/(.+)$", u)
    return f"{m.group(1)}:{m.group(2)}" if m else u


def ssh_host(url: str) -> str | None:
    """The host an SSH remote connects to (scp form or ssh://); None for every other form."""
    m = SSH_URL_RE.match(url)
    if m:
        return m.group(1)
    if "://" in url or "::" in url:
        return None
    m = SCP_RE.match(url)
    return m.group(1) if m else None


def remote_problem(url: str, acct: dict) -> str | None:
    """Why url is not a push URL for acct. Allowed: scp form or ssh:// through the account's alias,
    and absolute local paths or file:// (local mirrors, tests); every other scheme or transport is refused."""
    if url.startswith(("/", "file://")):
        return None
    host = ssh_host(url)
    if host is None:
        return f"{url} is not an SSH URL: pushes go over the account's SSH host alias {acct['ssh_host']} only"
    if host != acct["ssh_host"]:
        return f"{url} connects through {host}, but this account pushes via {acct['ssh_host']}"
    return None


def ssh_url_for(url: str, acct: dict) -> str:
    """The same repo over the account's alias: https://github.com/o/r -> git@<alias>:o/r.git."""
    m = REPO_PATH_RE.match(url)
    return f"git@{acct['ssh_host']}:{m.group(1) if m else '<owner>/<repo>'}.git"


# --------------------------------------------------------------------------- what is refused
def forbidden(path: str, registered: bool) -> tuple[str, str, bool] | None:
    """(what to untrack, why, whether allow_paths may waive it) when a repo path is private memory
    or a secret, else None. A top-level private/ or .cleo/ (an instance's own) is never waived."""
    parts = path.split("/")
    for i, part in enumerate(parts[:-1]):
        if part == "private":
            return "/".join(parts[: i + 1]) + "/", "is a Cleo vault: private memory never enters a repo", i > 0
        if registered and part == ".cleo":
            return "/".join(parts[: i + 1]) + "/", "is Cleo's machinery: local-only in every instance", i > 0
    name = parts[-1].lower()
    if name.startswith(".env") and name != ".env.example":
        return path, "is an env file: secrets live only in the untracked .env", True
    if any(fnmatch.fnmatchcase(name, glob) for glob in SECRET_GLOBS):
        return path, "looks like a key or credential file", True
    return None


def forbidden_hits(changes: list[tuple[str, str]], registered: bool, allow: list[str]) -> dict[str, tuple[str, list[str]]]:
    """target -> (why, commits) over (commit, path) pairs; a vault dir is reported once."""
    found: dict[str, tuple[str, list[str]]] = {}
    for sha, path in changes:
        hit = forbidden(path, registered)
        if hit and not (hit[2] and any(fnmatch.fnmatchcase(path, glob) for glob in allow)):
            shas = found.setdefault(hit[0], (hit[1], []))[1]
            if sha not in shas:
                shas.append(sha)
    return found


def frontmatter(text: str) -> list[str]:
    """The YAML frontmatter lines at the top of a file (first line `---`), or [] when it has none."""
    lines = text.lstrip("﻿").splitlines()
    if not lines or lines[0].rstrip() != "---":
        return []
    for i in range(1, len(lines)):
        if lines[i].rstrip() in ("---", "..."):
            return lines[1:i]
    return []


def has_vault_markers(lines: list[str]) -> bool:
    """A `fires_when:` line plus a `sources:` naming raw:, pending: or private/sessions/ provenance."""
    fires = provenance = in_sources = False
    for line in lines:
        fires = fires or bool(FIRES_RE.match(line))
        m = SOURCES_RE.match(line)
        if m:
            in_sources = True
            provenance = provenance or bool(PROVENANCE_RE.search(m.group(1)))
        elif in_sources and line[:1] in (" ", "\t", "-"):
            provenance = provenance or bool(PROVENANCE_RE.search(line))
        else:
            in_sources = False
    return fires and provenance


def reads_as_vault_note(text: str) -> bool:
    return has_vault_markers(frontmatter(text))


def decode_text(data: bytes) -> str:
    """Blob bytes as text: UTF-16 (a BOM, or NUL-interleaved ASCII) decoded as such, the rest as UTF-8."""
    if data[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return data.decode("utf-16", "replace")
    head = data[:512]
    if len(head) >= 4 and head.count(0) * 3 >= len(head):
        return data.decode("utf-16-le" if head[1] == 0 else "utf-16-be", "replace")
    return data.decode("utf-8", "replace")


def read_blobs(repo: Path, specs: list[str]):
    """Yield (spec, text) for each small blob named by a `<rev>:<path>` spec, one blob in memory at a
    time; missing, large or non-blob ones are skipped."""
    specs = [s for s in dict.fromkeys(specs) if "\n" not in s]
    if not specs:
        return
    listing = git(repo, "cat-file", "--batch-check=%(objectname) %(objecttype) %(objectsize)",
                  input="\n".join(specs) + "\n").splitlines()
    wanted: dict[str, list[str]] = {}
    for spec, line in zip(specs, listing):
        parts = line.split()
        if len(parts) == 3 and parts[1] == "blob" and int(parts[2]) <= BLOB_LIMIT:
            wanted.setdefault(parts[0], []).append(spec)
    if not wanted:
        return
    proc = subprocess.Popen(["git", "-C", str(repo), "cat-file", "--batch"], stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    try:
        for sha, names in wanted.items():
            proc.stdin.write(sha.encode() + b"\n")
            proc.stdin.flush()
            header = proc.stdout.readline().split()
            if len(header) != 3:
                raise GuardError(f"git cat-file --batch gave no blob for {sha} in {repo}")
            text = decode_text(proc.stdout.read(int(header[2])))
            proc.stdout.read(1)
            for spec in names:
                yield spec, text
    finally:
        proc.stdin.close()
        proc.stdout.close()
        proc.wait()


def unquote(path: str) -> str:
    """Undo git's C-style quoting of a diff header path ("we\\"ird" -> we"ird)."""
    if len(path) < 2 or not path[0] == path[-1] == '"':
        return path
    out, s, i = bytearray(), path[1:-1], 0
    while i < len(s):
        if s[i] == "\\" and re.fullmatch(r"[0-7]{3}", s[i + 1:i + 4]):
            out.append(int(s[i + 1:i + 4], 8))
            i += 4
        elif s[i] == "\\" and s[i + 1:i + 2] in C_ESCAPES:
            out.append(C_ESCAPES[s[i + 1]])
            i += 2
        else:
            out += s[i].encode()
            i += 1
    return out.decode("utf-8", "replace")


def added_lines(lines):
    """Yield (commit, path, text) for every added line of a unified or combined (merge) diff, read as
    a stream of lines. Commit headers are `\\x1e<sha>` lines (--format=%x1e%H); a plain diff has None."""
    sha, path, cols = None, None, 0
    for line in lines:
        if line.startswith("\x1e"):
            sha, path, cols = line[1:].strip(), None, 0
        elif line.startswith(("diff --git ", "diff --cc ", "diff --combined ")):
            path, cols = None, 0
        elif cols == 0 and line.startswith("+++ "):
            target = unquote(line[4:].rstrip("\t"))
            path = target[2:] if target.startswith("b/") else None
        elif line.startswith("@@"):
            cols = len(line) - len(line.lstrip("@")) - 1  # one prefix column per parent
        elif cols and path and "+" in line[:cols] and "-" not in line[:cols]:
            yield sha, path, line[cols:]


def denylist_hits(items, terms: list[tuple[int, str]]) -> dict[tuple[str, int], list[str]]:
    """(where, denylist line) -> commits, over (commit, where, text) items. NULs are dropped first, so
    ASCII text in a UTF-16 file matches too."""
    hits: dict[tuple[str, int], list[str]] = {}
    for sha, where, text in items:
        low = text.replace("\x00", "").lower()
        for n, term in terms:
            if term in low:
                shas = hits.setdefault((where, n), [])
                if sha and sha not in shas:
                    shas.append(sha)
    return hits


def shown(path: str, terms: list[tuple[int, str]]) -> str:
    """A path as printed: one whose own name holds a denylisted term is never echoed."""
    low = path.lower()
    return DENIED_PATH if any(term in low for _, term in terms) else path


def ident_email(repo: Path, var: str) -> str:
    proc = run_git(repo, "var", var)
    m = IDENT_EMAIL_RE.search(proc.stdout) if proc.returncode == 0 else None
    return m.group(1) if m else ""


def identity_fix(repo: Path, acct: dict) -> str:
    return f"git -C {q(repo)} config user.email {q(acct['email'])} && git -C {q(repo)} config user.name {q(acct['user_name'])}"


# --------------------------------------------------------------------------- pre-commit
def pre_commit_blocks(repo: Path) -> list[Block]:
    key, reg = repo_key(repo), load_registry()
    if local_only_reason(key, reg, logical_path(os.environ.get("PWD"), key)):
        return []  # its pre-push refuses everything, so commits there stay free
    entry = reg["repos"].get(str(key))
    staged = [p for p in git(repo, "diff", "--cached", "--name-only", "-z", *DIFF_ARGS).split("\0") if p]
    terms = load_denylist() if entry and is_public(key, entry) else []
    hits = forbidden_hits([("", p) for p in staged], bool(entry), entry.get("allow_paths", []) if entry else [])
    blocks = [(f"commit: {shown(target, terms)} {why}", f"git -C {q(repo)} rm -r -q --cached -- {q(shown(target, terms))}")
              for target, (why, _) in hits.items()]
    if not entry:
        return blocks
    blocks += commit_identity_blocks(repo, key, entry, reg)
    for spec, text in read_blobs(repo, [f":0:{p}" for p in staged]):
        if reads_as_vault_note(text):
            path = shown(spec[3:], terms)
            blocks.append((f"commit: {path} reads as a Cleo vault note (frontmatter with fires_when: and sources) "
                           "— private memory", f"git -C {q(repo)} rm -q --cached -- {q(path)}"))
    if terms:
        for _, n in denylist_hits(((None, "", p) for p in staged), terms):
            blocks.append((f"commit: a staged file name has a denylisted term (line {n} of {denylist_path()})",
                           f"rename it (git mv) so no name holds that term; list them: git -C {q(repo)} diff "
                           "--cached --name-only"))
        lines = git_lines(repo, "-c", "core.quotePath=false", "diff", "--cached", "--no-color", *PATCH_ARGS)
        for (path, n), _ in denylist_hits(((None, p, t) for _, p, t in added_lines(lines)), terms).items():
            path = shown(path, terms)
            blocks.append((f"commit: {path} adds a denylisted term (line {n} of {denylist_path()})",
                           f"edit {q(path)} to remove it, then: git -C {q(repo)} add -- {q(path)}"))
    return blocks


def commit_identity_blocks(repo: Path, key: Path, entry: dict, reg: dict) -> list[Block]:
    acct = reg["accounts"].get(entry["account"])
    if acct is None:
        return [(f"commit: {key} is registered to account {entry['account']}, missing from {registry_path()}",
                 f"{gg_cmd()} account --name {q(entry['account'])} --email <noreply email> --ssh-host <host alias>")]
    problem = noreply_problem(key, entry, acct)
    if problem:
        return [(f"commit: {problem}", noreply_fix(entry["account"], acct))]
    want = acct["email"]
    bad = sorted({e for e in (ident_email(repo, "GIT_AUTHOR_IDENT"), ident_email(repo, "GIT_COMMITTER_IDENT"))
                  if e.lower() != want.lower()})
    if not bad:
        return []
    configured = run_git(repo, "config", "--get", "user.email").stdout.strip()
    fix = (identity_fix(repo, acct) if configured.lower() != want.lower()
           else "drop the --author option / unset GIT_AUTHOR_EMAIL and GIT_COMMITTER_EMAIL")
    return [(f"commit as {', '.join(e or '<no email>' for e in bad)}: {key} commits as account "
             f"{entry['account']} <{want}>", fix)]


# --------------------------------------------------------------------------- pre-push
def is_zero(sha: str) -> bool:
    return set(sha) == {"0"}


def push_range(repo: Path, local_sha: str, remote_sha: str, excluded: list[str]) -> list[str]:
    """rev-list arguments for what a ref update sends: remote..local, or for a new ref (or a remote
    tip git has not fetched) every commit not already on the registered remote's tracking refs."""
    if not is_zero(remote_sha) and run_git(repo, "cat-file", "-e", f"{remote_sha}^{{commit}}").returncode == 0:
        return [local_sha, f"^{remote_sha}"]
    return [local_sha, "--not", *excluded] if excluded else [local_sha]


def range_changes(repo: Path, rng: list[str]) -> list[tuple[str, str]]:
    """(commit, path) for every path a commit in the range adds or changes; merges included."""
    out = git(repo, *LOG_DIFF, "--format=%x1e%H", "--name-only", "-z", *DIFF_ARGS, *rng)
    pairs = []
    for chunk in out.split("\x1e")[1:]:
        sha, *paths = chunk.split("\0")
        pairs += [(sha.strip(), p.strip("\n")) for p in paths if p.strip("\n")]
    return pairs


def range_messages(repo: Path, rng: list[str], local_sha: str) -> list[tuple[str, str, str]]:
    """(sha, "commit" | "tag", message) for every commit in the range and an annotated tag being pushed."""
    out = git(repo, *LOG_FORMAT, "--format=%x1e%H%x1f%B", *rng)
    messages = [(sha.strip(), "commit", body) for sha, body in
                (chunk.split("\x1f", 1) for chunk in out.split("\x1e")[1:])]
    sha = local_sha
    while git(repo, "cat-file", "-t", sha).strip() == "tag":
        head, _, body = git(repo, "cat-file", "tag", sha).partition("\n\n")
        messages.append((sha, "tag", body))
        sha = re.search(r"^object (\S+)", head, re.M).group(1)
    return messages


def ref_blocks(repo: Path, key: Path, entry: dict, acct: dict, ref: str, local_sha: str, remote_sha: str,
               excluded: list[str]) -> list[Block]:
    rng = push_range(repo, local_sha, remote_sha, excluded)
    terms = load_denylist() if is_public(key, entry) else []
    want = acct["email"]
    bad: dict[str, list[str]] = {}
    for line in git(repo, *LOG_FORMAT, "--format=%H%x1f%ae%x1f%ce", *rng).splitlines():
        sha, author, committer = line.split("\x1f")
        for email in dict.fromkeys((author, committer)):
            if email.lower() != want.lower():
                bad.setdefault(email, []).append(sha)
    blocks = [(f"push {ref}: commits by {email or '<no email>'}, not {want}: {short(shas)}", HISTORY_FIX)
              for email, shas in bad.items()]
    changes = range_changes(repo, rng)
    for target, (why, shas) in forbidden_hits(changes, True, entry.get("allow_paths", [])).items():
        blocks.append((f"push {ref}: {shown(target, terms)} {why} — in {short(shas)}", HISTORY_FIX))
    notes: dict[str, list[str]] = {}
    for spec, text in read_blobs(repo, [f"{sha}:{path}" for sha, path in changes]):
        if reads_as_vault_note(text):
            sha, path = spec.split(":", 1)
            notes.setdefault(path, []).append(sha)
    blocks += [(f"push {ref}: {shown(path, terms)} reads as a Cleo vault note — private memory — in {short(shas)}",
                HISTORY_FIX) for path, shas in notes.items()]
    messages = range_messages(repo, rng, local_sha)
    blocks += [(f"push {ref}: the {kind} message of {sha[:12]} reads as a Cleo vault note — private memory",
                HISTORY_FIX) for sha, kind, msg in messages if has_vault_markers(msg.splitlines())]
    if terms:
        lines = git_lines(repo, "-c", "core.quotePath=false", *LOG_DIFF, "-p", "--format=%x1e%H", *PATCH_ARGS, *rng)
        items = chain(((sha, shown(path, terms), text) for sha, path, text in added_lines(lines)),
                      ((sha, f"a {kind} message", msg) for sha, kind, msg in messages),
                      ((sha, "a file name", path) for sha, path in changes))
        for (where, n), shas in denylist_hits(items, terms).items():
            blocks.append((f"push {ref}: {where} has a denylisted term (line {n} of {denylist_path()}) — in "
                           f"{short(shas)}", HISTORY_FIX))
    return blocks


def pre_push_blocks(repo: Path, remote: str, url: str, updates: list[str]) -> list[Block]:
    key, reg = repo_key(repo), load_registry()
    remotes = remote_urls(repo)
    reason = local_only_reason(key, reg, logical_path(os.environ.get("PWD"), key))
    if reason:
        return [(f"push of {key}: {reason}; private memory never leaves this machine",
                 f"do not push it; drop the remote: git -C {q(repo)} remote remove {q(remote)}" if remote in remotes
                 else "do not push it")]
    entry = reg["repos"].get(str(key))
    if entry is None:
        names = "|".join(sorted(reg["accounts"])) or "name"
        return [(f"push of {key}: the repo is not registered, so its account and remote are unknown (fail closed)",
                 f"{gg_cmd()} register --repo {q(key)} --account <{names}> --remote <ssh url> --visibility <public|private>")]
    acct = reg["accounts"].get(entry["account"])
    if acct is None:
        return [(f"push of {key}: its account {entry['account']} is missing from {registry_path()}",
                 f"{gg_cmd()} account --name {q(entry['account'])} --email <noreply email> --ssh-host <host alias>")]
    problem = remote_problem(entry["remote"], acct)
    if problem:
        return [(f"push of {key}: its registered remote is wrong: {problem}", f"{gg_cmd()} register --repo {q(key)} "
                 f"--account {q(entry['account'])} --remote {q(ssh_url_for(entry['remote'], acct))} "
                 f"--visibility {entry['visibility']}")]
    problem = noreply_problem(key, entry, acct)
    if problem:
        return [(f"push of {key}: {problem}", noreply_fix(entry["account"], acct))]
    if norm_url(url) != norm_url(entry["remote"]):  # this also catches an https or other non-SSH push URL
        names = pushes_to(remotes, entry["remote"])
        fix = (f"git -C {q(repo)} push {q(names[0])}" if names
               else f"git -C {q(repo)} remote add github {q(entry['remote'])} && git -C {q(repo)} push github")
        return [(f"push to {url}: {key} pushes only to its registered remote {entry['remote']}", fix)]
    # a push by bare URL trusts no tracking refs; a named push trusts only remotes that are the registered one
    excluded = [f"--remotes={name}" for name in tracks(remotes, entry["remote"])] if remote in remotes else []
    blocks: list[Block] = []
    for line in updates:
        fields = line.split()
        if len(fields) != 4:
            continue
        local_ref, local_sha, remote_ref, remote_sha = fields
        if remote_ref.startswith("refs/remotes/") or remote_ref == "refs/stash":
            blocks.append((f"push {local_ref} -> {remote_ref}: remote-tracking refs and the stash are never pushed "
                           "(git push --mirror sends them)", f"push branches and tags by name: git -C {q(repo)} push "
                           f"{q(remote)} <branch>"))
        elif not is_zero(local_sha):  # a deletion sends no commits
            blocks += ref_blocks(repo, key, entry, acct, local_ref, local_sha, remote_sha, excluded)
    return blocks


def run_hook(name: str, args: list[str]) -> int:
    """Shim entry point: 0 lets git proceed; anything else refuses, with the reasons on stderr."""
    repo = Path.cwd()
    try:
        if name == "pre-commit":
            blocks = pre_commit_blocks(repo)
        else:
            remote, url = (args + ["", ""])[:2]
            blocks = pre_push_blocks(repo, remote, url, sys.stdin.read().splitlines())
    except Exception as exc:  # a guard that crashes must still refuse: fail closed
        blocks = [(f"{name}: the guard itself failed: {exc}", f"{gg_cmd()} check --repo {q(repo)}")]
    for what, fix in blocks[:MAX_BLOCKS]:
        print(f"gitguard: BLOCKED {what}\n  fix: {fix}", file=sys.stderr)
    if len(blocks) > MAX_BLOCKS:
        print(f"gitguard: ... and {len(blocks) - MAX_BLOCKS} more", file=sys.stderr)
    return 1 if blocks else 0


# --------------------------------------------------------------------------- install
def is_shim(path: Path) -> bool:
    try:
        return SHIM_MARK in path.read_text(encoding="utf-8", errors="replace")[:300]
    except OSError:
        return False


def guard_shim(hook: str) -> str:
    # SRC holds the source path already shell-quoted, so the printed fix line can be pasted as is
    head = SHIM_HEAD.format(mark=SHIM_MARK, hook=hook, local=LOCAL_DIR, bus=q(bus_root().absolute()),
                            src=q(q(Path(__file__).resolve())))
    return head + SHIM_BODY[hook]


def move_hook(src: Path, dst: Path) -> None:
    """Move a hook into local/, keeping a relative symlink pointed at the same file."""
    if src.is_symlink() and not os.path.isabs(os.readlink(src)):
        dst.symlink_to(os.path.normpath(os.path.join(src.parent, os.readlink(src))))
        src.unlink()
    else:
        os.rename(src, dst)


def free_name(path: Path) -> Path:
    candidate, n = path, 1
    while candidate.exists() or candidate.is_symlink():
        candidate, n = path.with_name(f"{path.name}.{n}"), n + 1
    return candidate


def write_shim(hooks: Path, hook: str, text: str) -> str:
    """Install one shim. A foreign hook already there moves to local/<hook> and runs after the guard.
    If a tool rewrote the hook after an earlier install (pre-commit install -f, husky), the hook it
    replaced is kept as local/<hook>.prev and the tool's new hook is chained instead."""
    path, local = hooks / hook, hooks / LOCAL_DIR / hook
    hooks.mkdir(parents=True, exist_ok=True)
    note = ""
    if (path.exists() or path.is_symlink()) and not is_shim(path):
        local.parent.mkdir(exist_ok=True)
        note = f" (the existing hook now runs after the guard, from {LOCAL_DIR}/{hook})"
        if local.exists() or local.is_symlink():
            prev = free_name(local.with_name(f"{hook}.prev"))
            move_hook(local, prev)
            note = f" (the new {hook} now runs after the guard; the one it replaced is kept as {LOCAL_DIR}/{prev.name})"
        move_hook(path, local)
    if path.is_file() and path.read_text(encoding="utf-8") == text and os.access(path, os.X_OK):
        return f"  · {path}: up to date"
    path.write_text(text, encoding="utf-8")
    path.chmod(0o755)
    return f"  ✓ {path}{note}"


def install_machine_copy() -> str:
    src, dst = Path(__file__).resolve(), machine_copy()
    if dst.is_file() and dst.read_bytes() == src.read_bytes():
        return f"  · {dst}: up to date"
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_name(dst.name + ".tmp")
    shutil.copyfile(src, tmp)
    tmp.chmod(0o755)
    os.replace(tmp, dst)
    return f"  ✓ {dst} (the machine-wide copy every shim calls)"


def install_template(tpl: Path) -> None:
    for hook in HOOKS:
        print(write_shim(tpl / "hooks", hook, guard_shim(hook)))
    current = run_git(Path.home(), "config", "--global", "--get", "init.templateDir").stdout.strip()
    if current and Path(current).expanduser().resolve() == tpl:
        print("  · init.templateDir already points here: every new git init / git clone gets the shims")
        return
    if current:
        print(f"  ! init.templateDir is already {current}: pointing it here replaces that template "
              f"(copy what it holds into {tpl} first)")
    print(f"every new git init / git clone gets the shims once you run:\n  git config --global init.templateDir {q(tpl)}")


def cmd_install(repo: str | None, template: str | None, never_push: str | None) -> int:
    print(install_machine_copy())
    if template:
        install_template(Path(template).expanduser().resolve())
        return 0
    key = repo_key(Path(never_push or repo).expanduser())
    hooks = hooks_dir(key)
    inside_git_dir = hooks.resolve().is_relative_to(common_dir(key))
    if never_push:
        reg = load_registry()
        listed = str(key) in reg["never_push"]
        if not listed:
            reg["never_push"] = sorted(reg["never_push"] + [str(key)])
            save_registry(reg)
        if not inside_git_dir:
            raise GuardError(f"core.hooksPath sends {key}'s hooks to {hooks}, which other repos may share; an "
                             f"always-refuse shim there would block them too. {key} is on never_push, so the guard "
                             f"shims refuse it: {gg_cmd()} install --repo {q(key)}")
        print(write_shim(hooks, "pre-push", NEVER_PUSH_SHIM.format(mark=SHIM_MARK)))
        if not listed:
            print(f"  ✓ never-push: {key}")
        return 0
    for hook in HOOKS:
        print(write_shim(hooks, hook, guard_shim(hook)))
    in_work_tree = not inside_git_dir and hooks.resolve().is_relative_to(key)
    if in_work_tree and run_git(key, "check-ignore", "-q", str(hooks / "pre-push")).returncode != 0:
        print(f"  ! {hooks} is inside the work tree and not ignored: gitignore it so the shims are never committed")
    return 0


# --------------------------------------------------------------------------- account / register / identity
def cmd_account(name: str, email: str, user_name: str | None, host: str) -> int:
    if "@" not in email:
        raise GuardError(f"{email!r} is not an email address")
    reg = load_registry()
    reg["accounts"][name] = {"email": email, "user_name": user_name or name, "ssh_host": host}
    save_registry(reg)
    print(f"account {name}: commits as {user_name or name} <{email}>, pushes via {host}")
    return 0


def apply_identity(key: Path, acct: dict) -> None:
    git(key, "config", "--local", "user.name", acct["user_name"])
    git(key, "config", "--local", "user.email", acct["email"])


def cmd_register(repo: str, account: str, remote: str, visibility: str, allow: list[str]) -> int:
    reg = load_registry()
    acct = reg["accounts"].get(account)
    if acct is None:
        raise GuardError(f"unknown account {account!r}; add it first: {gg_cmd()} account --name {q(account)} "
                         "--email <noreply email> --ssh-host <host alias>")
    key = repo_key(Path(repo).expanduser())
    reason = local_only_reason(key, reg, logical_path(repo, key))
    if reason:
        raise GuardError(f"{key} is local-only ({reason}): private memory is never pushed, so it is never registered")
    problem = remote_problem(remote, acct)
    if problem:
        raise GuardError(f"{problem}; register it as {ssh_url_for(remote, acct)}")
    entry = {"account": account, "remote": remote, "visibility": visibility}
    problem = noreply_problem(key, entry, acct)
    if problem:
        raise GuardError(f"{problem}: {noreply_fix(account, acct)}")
    if allow:
        entry["allow_paths"] = sorted(set(allow))
    reg["repos"][str(key)] = entry
    save_registry(reg)
    apply_identity(key, acct)
    print(f"registered {key}: account {account} <{acct['email']}>, {visibility}, remote {remote}"
          + (f", allow_paths {', '.join(entry['allow_paths'])}" if allow else ""))
    if not all(is_shim(hooks_dir(key) / hook) for hook in HOOKS):
        print(f"next: {gg_cmd()} install --repo {q(key)}")
    return 0


def cmd_identity(repo: str) -> int:
    """Give a repo its registered account's user.name / user.email; an unregistered repo keeps its own."""
    key = repo_key(Path(repo).expanduser())
    reg = load_registry()
    entry = reg["repos"].get(str(key))
    if entry is None:
        print(f"  · {key} is not registered with gitguard: commits use the configured git identity")
        return 0
    acct = reg["accounts"].get(entry["account"])
    if acct is None:
        raise GuardError(f"{key} is registered to account {entry['account']}, missing from {registry_path()}")
    apply_identity(key, acct)
    print(f"  ✓ {key} commits as {acct['user_name']} <{acct['email']}> (account {entry['account']})")
    return 0


# --------------------------------------------------------------------------- check / status
def inspect(key: Path, reg: dict, alias: Path | None = None) -> list[Check]:
    """Every guard check for one repo; shared by `check` (prints all) and `status` (summarises)."""
    hooks = hooks_dir(key)
    remotes = remote_urls(key)
    reason = local_only_reason(key, reg, alias)
    if reason:
        guarded = is_shim(hooks / "pre-push")
        return [
            (True, "local-only", reason),
            (guarded, "pre-push", f"refuses every push ({hooks / 'pre-push'})" if guarded
             else f"not guarded — fix: {gg_cmd()} install --never-push {q(key)}"),
            (not remotes, "remote", "none, as it should be" if not remotes
             else f"{', '.join(remotes)} configured — fix: git -C {q(key)} remote remove {q(next(iter(remotes)))}"),
        ]
    missing = [hook for hook in HOOKS if not is_shim(hooks / hook)]
    checks: list[Check] = [
        (machine_copy().is_file(), "guard", str(machine_copy()) if machine_copy().is_file()
         else f"machine copy missing — fix: python3 {q(Path(__file__).resolve())} install --repo {q(key)}"),
        (not missing, "hooks", f"pre-commit + pre-push shims in {hooks}" if not missing
         else f"{' + '.join(missing)} not installed — fix: {gg_cmd()} install --repo {q(key)}"),
    ]
    entry = reg["repos"].get(str(key))
    if entry is None:
        names = "|".join(sorted(reg["accounts"])) or "name"
        checks.append((False, "registered", f"no — fix: {gg_cmd()} register --repo {q(key)} --account <{names}> "
                                            f"--remote <ssh url> --visibility <public|private>"))
        return checks
    public = is_public(key, entry)
    checks.append((True, "registered", f"account {entry['account']}, {'public' if public else 'private'}, remote "
                                       f"{entry['remote']}" + (f", allow_paths {', '.join(entry['allow_paths'])}"
                                                               if entry.get("allow_paths") else "")))
    acct = reg["accounts"].get(entry["account"])
    if acct is None:
        checks.append((False, "account", f"{entry['account']} is not in {registry_path()} — fix: {gg_cmd()} account "
                                         f"--name {q(entry['account'])} --email <noreply email> --ssh-host <host alias>"))
        return checks
    problem = remote_problem(entry["remote"], acct)
    if problem:
        checks.append((False, "ssh", f"{problem} — fix: {gg_cmd()} register --repo {q(key)} --account "
                                     f"{q(entry['account'])} --remote {q(ssh_url_for(entry['remote'], acct))} "
                                     f"--visibility {entry['visibility']}"))
    problem = noreply_problem(key, entry, acct)
    if problem:
        checks.append((False, "noreply", f"{problem} — fix: {noreply_fix(entry['account'], acct)}"))
    email = run_git(key, "config", "--local", "--get", "user.email").stdout.strip()
    ok = email.lower() == acct["email"].lower()
    checks.append((ok, "user.email", email if ok else f"{email or '<unset>'}, expected {acct['email']} — fix: "
                                                      f"{identity_fix(key, acct)}"))
    pushing, tracking = pushes_to(remotes, entry["remote"]), tracks(remotes, entry["remote"])
    if pushing:
        name = (tracking or pushing)[0]
        detail = f"{name} → {entry['remote']}" + ("" if tracking else f" (it fetches from {remotes[name][0][0]}, so "
                                                   "new branches are checked against their whole history)")
    else:
        detail = (f"no remote pushes to {entry['remote']} — fix: git -C {q(key)} remote add github "
                  f"{q(entry['remote'])} && git -C {q(key)} push github")
    checks.append((bool(pushing), "remote", detail))
    return checks


def cmd_check(repo: str | None) -> int:
    given = repo or "."
    key = repo_key(Path(given).expanduser())
    checks = inspect(key, load_registry(), logical_path(given, key))
    print(f"gitguard check: {key}")
    for ok, label, detail in checks:
        print(f"  {'✓' if ok else '✗'} {label}: {detail}")
    return 0 if all(ok for ok, _, _ in checks) else 1


def repo_state(path: Path, reg: dict) -> str:
    if not path.is_dir():
        return "missing"
    try:
        failed = [label for ok, label, _ in inspect(repo_key(path), reg) if not ok]
    except GuardError:
        return "not a git repo"
    return "ok" if not failed else "✗ " + ", ".join(failed)


def cmd_status() -> int:
    reg = load_registry()
    rows = [(path, e.get("account", "?"), e.get("visibility", "?"), repo_state(Path(path), reg))
            for path, e in sorted(reg["repos"].items())]
    rows += [(path, "-", "never-push", repo_state(Path(path), reg)) for path in sorted(reg["never_push"])]
    if not rows:
        print(f"gitguard status: nothing registered in {registry_path()}")
        return 0
    header = ("REPO", "ACCOUNT", "VISIBILITY", "STATE")
    widths = [max(len(r[i]) for r in rows + [header]) for i in range(3)]
    for row in [header] + rows:
        print("  ".join(cell.ljust(w) for cell, w in zip(row, widths)) + "  " + row[3])
    return 0 if all(r[3] == "ok" for r in rows) else 1


# --------------------------------------------------------------------------- main
def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="gitguard", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("account", help="add or update a hosting account")
    a.add_argument("--name", required=True); a.add_argument("--email", required=True)
    a.add_argument("--user-name"); a.add_argument("--ssh-host", required=True)
    r = sub.add_parser("register", help="bind a repo to an account, remote and visibility")
    r.add_argument("--repo", required=True); r.add_argument("--account", required=True)
    r.add_argument("--remote", required=True); r.add_argument("--visibility", required=True, choices=VISIBILITIES)
    r.add_argument("--allow-path", action="append", default=[], metavar="GLOB",
                   help="waive the path/secret rules for matching paths (repeatable; replaces earlier ones)")
    d = sub.add_parser("identity", help="give a repo its registered account's user.name/user.email")
    d.add_argument("--repo", required=True)
    i = sub.add_parser("install", help="install the hook shims")
    where = i.add_mutually_exclusive_group(required=True)
    where.add_argument("--repo"); where.add_argument("--template", metavar="DIR")
    where.add_argument("--never-push", metavar="PATH")
    c = sub.add_parser("check", help="dry run for one repo; non-zero on any problem")
    c.add_argument("--repo")
    sub.add_parser("status", help="every registered repo and its state; non-zero on any problem")
    h = sub.add_parser("hook", help="entry point the shims call")
    h.add_argument("name", choices=HOOKS); h.add_argument("args", nargs="*")
    args = ap.parse_args(argv)
    try:
        if args.cmd == "hook":
            return run_hook(args.name, args.args)
        if args.cmd == "account":
            return cmd_account(args.name, args.email, args.user_name, args.ssh_host)
        if args.cmd == "register":
            return cmd_register(args.repo, args.account, args.remote, args.visibility, args.allow_path)
        if args.cmd == "identity":
            return cmd_identity(args.repo)
        if args.cmd == "install":
            return cmd_install(args.repo, args.template, args.never_push)
        if args.cmd == "check":
            return cmd_check(args.repo)
        return cmd_status()
    except GuardError as exc:
        print(f"gitguard: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
