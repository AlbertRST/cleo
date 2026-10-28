#!/usr/bin/env python3
"""memctl — vault tooling for Cleo's memory system (generic; no personal data).

Single-file CLI. Python 3.12, stdlib + PyYAML. See subprojects/memory/README.md.

Subcommands: ingest, catalog, query, cues, lint, audit, incidents, wakepack, night, health,
beacon, portfolio, relocate, partner, drift, handoff (hub-and-spoke layer).
"""
from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import fcntl
import hashlib
import json
import math
import os
import random
import re
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

import yaml


class _StrDatesLoader(yaml.SafeLoader):
    """SafeLoader that keeps dates/timestamps as strings (faithful frontmatter snapshots)."""


_StrDatesLoader.yaml_implicit_resolvers = {
    k: [(tag, rx) for tag, rx in v if tag != "tag:yaml.org,2002:timestamp"]
    for k, v in yaml.SafeLoader.yaml_implicit_resolvers.items()
}

# --------------------------------------------------------------------------- constants
VAULT_SKIP_DIRS = {"raw", ".git", "index"}
LINT_EXEMPT_STEMS = {"WAKE-PACK", "HEALTH", "MEMORY"}  # plus README*
TRUNC = 200
WAKE_PACK_CAP = 9000
GOAL_STATE_LINES, GOAL_STATE_BYTES = 40, 3000
MEMORY_INDEX_LINES, MEMORY_INDEX_BYTES = 120, 12000
HALF_LIFE_DAYS, RECENCY_FLOOR = 60.0, 0.2
INCIDENT_PATTERNS = [
    r"i already (told|said)",
    r"as i (said|told)",
    r"ya te (lo )?dije",
    r"te lo dije",
    r"add (this|that) to your memory",
    r"you (forgot|already know)",
    r"no,? (that|this) is (wrong|not)",
]
CUE_PREFIXES = ("weekday:", "date:", "phase:", "topic:")
# Text the harness injects into user turns (sub-agent reports, reminders, slash-command output).
HARNESS_TAGS = ("task-notification", "system-reminder", "command-name", "command-message", "command-args",
                "local-command-stdout", "local-command-stderr", "bash-input", "bash-stdout", "bash-stderr")
HARNESS_TAG_RE = re.compile(r"<(%s)>.*?(?:</\1>|\Z)" % "|".join(HARNESS_TAGS), re.S)
WEEKDAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
CATALOG_KEYS = [
    "path", "id", "type", "name", "description", "created", "updated", "fires_when",
    "sources", "valid_to", "superseded_by", "pinned", "tags", "links", "frontmatter",
    "size", "sha256",
]
WIKILINK_RE = re.compile(r"\[\[([^\]|#]+)(?:[#|][^\]]*)?\]\]")
FENCE_RE = re.compile(r"^[ \t]*(`{3,}|~{3,}).*?^[ \t]*\1[ \t]*$", re.M | re.S)
INLINE_CODE_RE = re.compile(r"(`+)[^`\n]*?\1")
# Wikilinks under these repo-relative prefixes are placeholders by design: catalogued, never lint-resolved.
TEMPLATE_PREFIXES = ("docs/blueprint/templates/", ".cleo/blueprint/templates/", "templates/")
DECISION_RE = re.compile(r"^## (D\d+)\b(.*)$")
DATE_RE = re.compile(r"(\d{4}-\d{2}-\d{2})")
GENERIC_STEMS = {"SKILL", "README", "index"}
# hub-and-spoke: beacons on the home-level bus, portfolio rendered by the hq instance
STALE_BEACON_DAYS = 14
BEACON_TAIL_BYTES = 65536
PORTFOLIO_PACK_CAP = WAKE_PACK_CAP // 3
PORTFOLIO_TRUNC_LINE = "(truncated — see private/index/PORTFOLIO.md)"
PARTNER_SKIP = {"MEMORY.md", "_TEMPLATE.md"}
PHASE_RE = re.compile(r"\*\*Phase:\*\*(.*)$")
PLACEHOLDER_RE = re.compile(r"_[^_]*_")
OPEN_TASK_RE = re.compile(r"^\s*- \[ \]")
TIMESTAMP_RE = re.compile(r"\"timestamp\"\s*:\s*\"(\d{4}-\d{2}-\d{2}[^\"]*)")
FAILED_REASON = "last night run failed"
CARRY_MARK = "carried from the last full night: "
QUICK_CRASH = "night --quick crashed"  # a full night's crash reads "night crashed" and is carried
# Seed -> instance copies that install-cleo.sh makes on EVERY run (its steps 1 and 2). This is the
# one table to keep in sync with the installer. "dir/" = recursive copy, "*" = glob into the dir.
SEED_DOCTRINE = ("CLEO-IDENTITY.md", "OPERATING-MODEL.md", "MEMORY-SYSTEM.md", "DOCS-SYSTEM.md",
                 "HQ-LAYER.md", "ENGINEERING-STANDARDS.md", "AGENT-ROSTER.md", "GIT-GUARD.md", "README.md")
INSTALL_MAP: tuple[tuple[str, str], ...] = tuple((f, ".cleo/blueprint/" + f) for f in SEED_DOCTRINE) + (
    ("templates/", ".cleo/blueprint/templates/"),
    ("engine/memctl.py", ".cleo/engine/memctl.py"),
    ("engine/gitguard.py", ".cleo/engine/gitguard.py"),
    ("engine/hook.sh", ".cleo/engine/hook.sh"),
    ("engine/backup_vault.sh", ".cleo/engine/backup_vault.sh"),
    ("engine/LIBRARIAN.md", ".cleo/engine/LIBRARIAN.md"),
    ("engine/tests/*.py", ".cleo/engine/tests/"),
    ("templates/repo/.claude/skills/wake/", ".claude/skills/wake/"),
    ("templates/repo/.claude/skills/sleep/", ".claude/skills/sleep/"),
    ("templates/repo/.claude/output-styles/cleo-concise.md", ".claude/output-styles/cleo-concise.md"),
    ("templates/repo/.claude/agents/*.md", ".claude/agents/"),
)
# --public: the bootstrap is refreshed into a local-only file and stamped per instance.
PUBLIC_INSTALL_MAP: tuple[tuple[str, str], ...] = (("templates/repo/CLAUDE.md", ".cleo/CLAUDE-BOOTSTRAP.md"),)
STAMPED_DESTS = {".cleo/CLAUDE-BOOTSTRAP.md"}
# What a refresh copies or runs: an uncommitted change under any of these makes the seed dirty.
SEED_PATHSPECS = ("install-cleo.sh",) + tuple(sorted({s.rstrip("/") for s, _ in INSTALL_MAP + PUBLIC_INSTALL_MAP}))
DRIFT_SKIP_DIRS = {"__pycache__", ".pytest_cache"}
STAMP_RE = re.compile(r"[ \t]*<!-- CLEO-INSTANCE:.*?-->[ \t]*\n?", re.S)
# handoffs: one markdown file per task on the bus, open/ until the receiver closes it into done/
SLUG_RE = re.compile(r"[^a-z0-9]+")
INBOX_PACK_CAP = WAKE_PACK_CAP // 6
INBOX_TRUNC_LINE = "(more — run `memctl handoff list --print`)"


# --------------------------------------------------------------------------- helpers
def encode_project_path(path: Path) -> str:
    """Claude Code names a project's transcript dir after its absolute path, with
    '/' and '_' both collapsed to '-'.  /home/a/my_proj -> -home-a-my-proj"""
    return str(path).replace("/", "-").replace("_", "-")


def default_transcripts(root: Path) -> Path:
    """Transcript dir for THIS project, derived from its own root so a Cleo copy
    reads its own sessions and never another instance's.  Override: MEMCTL_TRANSCRIPTS."""
    override = os.environ.get("MEMCTL_TRANSCRIPTS")
    if override:
        return Path(override).expanduser().resolve()
    return Path.home() / ".claude" / "projects" / encode_project_path(root)


def today() -> dt.date:
    override = os.environ.get("MEMCTL_TODAY")
    return dt.date.fromisoformat(override) if override else dt.date.today()


def now_iso() -> str:
    override = os.environ.get("MEMCTL_NOW")
    return override or dt.datetime.now().replace(microsecond=0).isoformat()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def write_if_changed(path: Path, text: str) -> bool:
    """Write text to path only if content differs. Returns True when written."""
    data = text.encode("utf-8")
    if path.exists() and sha256_file(path) == sha256_bytes(data):
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return True


def copy_if_changed(src: Path, dst: Path) -> bool:
    if dst.exists() and sha256_file(dst) == sha256_file(src):
        return False
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src, dst)
    return True


def find_repo_root(start: Path | None = None) -> Path:
    for candidate in [start or Path.cwd(), Path(__file__).resolve().parent]:
        p = candidate.resolve()
        while True:
            if (p / "MISSION.md").exists():
                return p
            if p.parent == p:
                break
            p = p.parent
    raise SystemExit("memctl: repo root not found (no MISSION.md above cwd or script)")


def parse_date(value) -> dt.date | None:
    if value is None:
        return None
    if isinstance(value, dt.datetime):
        return value.date()
    if isinstance(value, dt.date):
        return value
    m = DATE_RE.search(str(value))
    return dt.date.fromisoformat(m.group(1)) if m else None


def jsonable(value):
    """Make a YAML-parsed structure JSON-safe (dates/datetimes -> ISO strings)."""
    if isinstance(value, dict):
        return {str(k): jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [jsonable(v) for v in value]
    if isinstance(value, (dt.date, dt.datetime)):
        return value.isoformat()
    return value


def as_list(value) -> list:
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return [str(v) for v in value]
    return [str(value)]


def rel(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.resolve().as_posix()


def read_jsonl(path: Path) -> list[dict]:
    rows = []
    if not path.exists():
        return rows
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return rows


def append_jsonl(path: Path, row: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def split_frontmatter(text: str) -> tuple[dict | None, str, str | None]:
    """Return (frontmatter dict or None, body, error). Tolerant: bad YAML -> (None, text, err)."""
    if not text.startswith("---"):
        return None, text, None
    lines = text.split("\n")
    if lines[0].strip() != "---":
        return None, text, None
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            raw = "\n".join(lines[1:i])
            body = "\n".join(lines[i + 1:])
            try:
                fm = yaml.load(raw, Loader=_StrDatesLoader)
            except (yaml.YAMLError, ValueError, RecursionError) as exc:  # tolerant; ValueError: an explicit tag
                return None, body, f"yaml: {exc.__class__.__name__}"  # (!!timestamp 2026-02-30, !!int abc)
            if fm is None:
                fm = {}
            if not isinstance(fm, dict):
                return None, body, "frontmatter is not a mapping"
            return fm, body, None
    return None, text, "unterminated frontmatter"


class Ctx:
    def __init__(self, root: Path, vault: Path, transcripts: Path):
        self.root = root
        self.vault = vault
        self.transcripts = transcripts
        self.raw = vault / "raw" / "transcripts"
        self.index = vault / "index"
        self.catalog_path = self.index / "catalog.jsonl"


# --------------------------------------------------------------------------- ingest / render
def _block_text(block) -> str:
    if isinstance(block, str):
        return block
    if not isinstance(block, dict):
        return ""
    kind = block.get("type")
    if kind == "text":
        return block.get("text", "")
    if kind == "tool_use":
        payload = json.dumps(block.get("input", {}), ensure_ascii=False, sort_keys=True)
        return f"[tool_use {block.get('name', '?')}] {payload[:TRUNC]}"
    if kind == "tool_result":
        content = block.get("content", "")
        if isinstance(content, list):
            content = "\n".join(_block_text(b) for b in content)
        content = str(content).replace("\r", "")
        return f"[tool_result] {content[:TRUNC]}"
    if kind in ("image", "document"):
        return f"[{kind}]"
    return ""  # thinking and unknown block types are skipped


def render_transcript(sid: str, records: list[dict]) -> str:
    out = [f"# Transcript {sid}", ""]
    index = 0
    for rec in records:
        rtype = rec.get("type")
        if rtype not in ("user", "assistant"):
            continue
        msg = rec.get("message") or {}
        content = msg.get("content", "")
        blocks = content if isinstance(content, list) else [content]
        parts = [t for t in (_block_text(b) for b in blocks) if t]
        ts = rec.get("timestamp", "")
        meta = " (meta)" if rec.get("isMeta") else ""
        out.append(f"## [{index}] {rtype} {ts}{meta}")
        out.append("\n".join(parts).replace("\r", ""))
        out.append("")
        index += 1
    return "\n".join(out).rstrip("\n") + "\n"


def source_transcripts(ctx: Ctx) -> list[tuple[Path, Path]]:
    """(source jsonl, destination jsonl) pairs for sessions and subagents."""
    pairs = []
    src = ctx.transcripts
    if not src.is_dir():
        return pairs
    for jsonl in sorted(src.glob("*.jsonl")):
        sid = jsonl.stem
        pairs.append((jsonl, ctx.raw / f"{sid}.jsonl"))
        sub = src / sid / "subagents"
        if sub.is_dir():
            for sj in sorted(sub.glob("*.jsonl")):
                pairs.append((sj, ctx.raw / f"{sid}-subagents" / sj.name))
    return pairs


def cmd_ingest(ctx: Ctx, quiet: bool = False) -> list[str]:
    changed: list[str] = []
    ctx.raw.mkdir(parents=True, exist_ok=True)
    for src, dst in source_transcripts(ctx):
        if copy_if_changed(src, dst):
            changed.append(rel(dst, ctx.vault))
    for jsonl in sorted(ctx.raw.rglob("*.jsonl")):
        md = jsonl.with_suffix(".md")
        text = render_transcript(jsonl.stem, read_jsonl(jsonl))
        if write_if_changed(md, text):
            changed.append(rel(md, ctx.vault))
    if cmd_catalog(ctx, quiet=True):
        changed.append("index/catalog.jsonl")
    if not quiet:
        print(f"ingest: {len(changed)} file(s) changed")
        for c in changed:
            print(f"  {c}")
    return changed


# --------------------------------------------------------------------------- catalog
def default_type(relpath: str, root: Path, vault: Path) -> str:
    vrel = rel(vault, root)
    parts = relpath.split("/")
    if relpath.startswith(vrel + "/") or vrel == ".":
        inner = relpath[len(vrel) + 1:] if vrel != "." else relpath
        top = inner.split("/")[0]
        if inner == "GOAL-STATE.md":
            return "goal-state"
        if inner == "memory/MEMORY.md":
            return "reference"  # the computed index, not a fact
        return {
            "sessions": "episode", "checkins": "episode", "memory": "fact",
            "commitments": "commitment", "archive": "archive",
        }.get(top, "reference")
    if relpath == "DECISIONS.md":
        return "decision"
    if parts[0] == ".claude":
        return "procedure"
    if parts[0] == "docs":
        return "reference"
    return "reference"


def _first_heading(body: str) -> str | None:
    for line in body.splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    return None


def wikilinks(body: str) -> list[str]:
    """Sorted unique [[targets]] in body, ignoring fenced code blocks and inline code spans."""
    stripped = INLINE_CODE_RE.sub("", FENCE_RE.sub("", body))
    return sorted({m.strip() for m in WIKILINK_RE.findall(stripped)})


def is_template_path(relpath: str) -> bool:
    return relpath.startswith(TEMPLATE_PREFIXES)


def build_row(path: Path, relpath: str, text: str, default_t: str) -> dict:
    fm, body, err = split_frontmatter(text)
    fm_snapshot = jsonable(fm) if fm is not None else None
    if err:
        fm_snapshot = {"_error": err}
    fm = fm or {}
    updated = fm.get("updated")
    if updated is None and isinstance(fm.get("metadata"), dict):
        updated = fm["metadata"].get("modified")
    if updated is None:
        m = DATE_RE.search(path.name)
        updated = m.group(1) if m else None
    upd = parse_date(updated)
    created = parse_date(fm.get("created"))
    rtype = str(fm.get("type") or default_t)
    stem = path.stem
    if stem in GENERIC_STEMS and path.parent.name:
        stem = path.parent.name  # skills/wake/SKILL.md -> wake; docs/x/README.md -> x
    data = text.encode("utf-8")
    return {
        "path": relpath,
        "id": f"{rtype}/{stem}",
        "type": rtype,
        "name": str(fm.get("name") or _first_heading(body) or stem),
        "description": str(fm.get("description") or ""),
        "created": created.isoformat() if created else None,
        "updated": upd.isoformat() if upd else None,
        "fires_when": str(fm.get("fires_when") or ""),
        "sources": as_list(fm.get("sources")),
        "valid_to": str(fm["valid_to"]) if fm.get("valid_to") else None,
        "superseded_by": str(fm["superseded_by"]) if fm.get("superseded_by") else None,
        "pinned": bool(fm.get("pinned", False)),
        "tags": as_list(fm.get("tags")),
        "links": wikilinks(body),
        "frontmatter": fm_snapshot,
        "size": len(data),
        "sha256": sha256_bytes(data),
    }


def decision_rows(path: Path, relpath: str) -> list[dict]:
    rows = []
    if not path.exists():
        return rows
    lines = path.read_text(encoding="utf-8").splitlines()
    current, buf = None, []
    sections: list[tuple[str, str, list[str]]] = []
    for line in lines:
        m = DECISION_RE.match(line)
        if m:
            if current:
                sections.append((current[0], current[1], buf))
            current, buf = (m.group(1), m.group(2).strip(" —-")), []
        elif current:
            buf.append(line)
    if current:
        sections.append((current[0], current[1], buf))
    for did, title, body_lines in sections:
        body = "\n".join(body_lines)
        date = parse_date(title)
        text = f"## {did} {title}\n{body}"
        rows.append({
            "path": relpath, "id": f"decision/{did}", "type": "decision",
            "name": f"{did} {title}".strip(), "description": "",
            "created": date.isoformat() if date else None,
            "updated": date.isoformat() if date else None,
            "fires_when": "", "sources": [], "valid_to": None, "superseded_by": None,
            "pinned": False, "tags": [],
            "links": wikilinks(body),
            "frontmatter": None, "size": len(text.encode("utf-8")),
            "sha256": sha256_bytes(text.encode("utf-8")),
        })
    return rows


def vault_md_files(vault: Path) -> list[Path]:
    files = []
    for p in sorted(vault.rglob("*.md")):
        parts = p.relative_to(vault).parts
        if parts[0] in VAULT_SKIP_DIRS:
            continue
        files.append(p)
    return files


def repo_md_files(root: Path) -> list[Path]:
    files = [root / n for n in ("MISSION.md", "MAP.md")]
    files += sorted((root / "docs").rglob("*.md")) if (root / "docs").is_dir() else []
    files += sorted((root / ".claude" / "skills").glob("*/SKILL.md"))
    files += sorted((root / ".claude" / "agents").glob("*.md"))
    return [f for f in files if f.exists()]


def build_catalog(ctx: Ctx) -> list[dict]:
    rows = []
    for p in vault_md_files(ctx.vault) + repo_md_files(ctx.root):
        relpath = rel(p, ctx.root)
        rows.append(build_row(p, relpath, p.read_text(encoding="utf-8"), default_type(relpath, ctx.root, ctx.vault)))
    rows += decision_rows(ctx.root / "DECISIONS.md", "DECISIONS.md")
    dedupe_ids(rows)
    rows.sort(key=lambda r: (r["path"], r["id"]))
    return rows


def dedupe_ids(rows: list[dict]) -> None:
    """Colliding `<type>/<stem>` ids fall back to `<type>/<path-without-ext>` (deterministic)."""
    counts: dict[str, int] = {}
    for r in rows:
        counts[r["id"]] = counts.get(r["id"], 0) + 1
    for r in rows:
        if counts[r["id"]] > 1 and r["type"] != "decision":
            r["id"] = f"{r['type']}/{re.sub(r'\.md$', '', r['path'])}"


def catalog_text(rows: list[dict]) -> str:
    return "".join(json.dumps({k: r[k] for k in CATALOG_KEYS}, ensure_ascii=False, sort_keys=True) + "\n" for r in rows)


def cmd_catalog(ctx: Ctx, quiet: bool = False) -> bool:
    rows = build_catalog(ctx)
    changed = write_if_changed(ctx.catalog_path, catalog_text(rows))
    if not quiet:
        print(f"catalog: {len(rows)} rows -> {rel(ctx.catalog_path, ctx.root)} ({'updated' if changed else 'unchanged'})")
    return changed


def load_catalog(ctx: Ctx) -> list[dict]:
    if not ctx.catalog_path.exists():
        return build_catalog(ctx)
    return read_jsonl(ctx.catalog_path)


def is_live(row: dict) -> bool:
    return not row.get("valid_to") and row.get("type") != "archive"


# --------------------------------------------------------------------------- query
def _grep_counts(terms: list[str], files: list[Path]) -> dict[str, dict[str, int]]:
    """{term: {abs_path: matching_line_count}} via rg (fallback grep)."""
    result: dict[str, dict[str, int]] = {t: {} for t in terms}
    if not files:
        return result
    rg = shutil.which("rg")
    for term in terms:
        if rg:
            cmd = [rg, "-i", "-F", "-c", "--no-messages", "-e", term] + [str(f) for f in files]
        else:
            cmd = ["grep", "-rIic", "-F", "-e", term] + [str(f) for f in files]
        proc = subprocess.run(cmd, capture_output=True, text=True)
        for line in proc.stdout.splitlines():
            path, _, count = line.rpartition(":")
            if path and count.isdigit() and int(count) > 0:
                result[term][str(Path(path).resolve())] = int(count)
    return result


def recency_factor(row: dict, ref: dt.date) -> float:
    if row.get("pinned") or not row.get("updated"):
        return 1.0
    upd = parse_date(row["updated"])
    if upd is None:
        return 1.0
    age = max(0, (ref - upd).days)
    return max(RECENCY_FLOOR, math.pow(0.5, age / HALF_LIFE_DAYS))


def score_rows(ctx: Ctx, rows: list[dict], text: str) -> list[tuple[float, dict]]:
    terms = [t.lower() for t in text.split() if t.strip()]
    if not terms:
        return []
    file_rows = [r for r in rows if r["type"] != "decision"]
    files = sorted({ctx.root / r["path"] for r in file_rows if (ctx.root / r["path"]).exists()})
    counts = _grep_counts(terms, files)
    decision_text: dict[str, str] = {}
    if any(r["type"] == "decision" for r in rows):
        for d in decision_rows(ctx.root / "DECISIONS.md", "DECISIONS.md"):
            decision_text[d["id"]] = d["name"].lower()
        dec_path = ctx.root / "DECISIONS.md"
        if dec_path.exists():
            sections = re.split(r"(?m)^(?=## D\d+\b)", dec_path.read_text(encoding="utf-8"))
            for sec in sections:
                m = DECISION_RE.match(sec.splitlines()[0] if sec else "")
                if m:
                    decision_text[f"decision/{m.group(1)}"] = sec.lower()
    ref = today()
    scored = []
    for r in rows:
        meta = " ".join([r.get("name", ""), r.get("description", ""), r.get("fires_when", "")]).lower()
        hits = 0.0
        for t in terms:
            if r["type"] == "decision":
                body_hit = 1 if t in decision_text.get(r["id"], "") else 0
            else:
                body_hit = 1 if counts[t].get(str((ctx.root / r["path"]).resolve()), 0) > 0 else 0
            hits += body_hit + (3 if t in meta else 0)
        if hits > 0:
            scored.append((round(hits * recency_factor(r, ref), 3), r))
    scored.sort(key=lambda s: (-s[0], s[1]["id"]))
    return scored


def cmd_query(ctx: Ctx, text: str, type_: str | None, since: str | None, top: int) -> None:
    rows = load_catalog(ctx)
    if type_:
        rows = [r for r in rows if r["type"] == type_]
    if since:
        s = dt.date.fromisoformat(since)
        rows = [r for r in rows if r.get("updated") and parse_date(r["updated"]) >= s]
    for score, r in score_rows(ctx, rows, text)[:top]:
        print(f"{r['id']} | {r['type']} | {score} | {r.get('fires_when') or '-'} | {r['path']}")


# --------------------------------------------------------------------------- cues
def parse_cue_tokens(fires_when: str) -> list[tuple[str, str]]:
    tokens = []
    for tok in (fires_when or "").split():
        low = tok.lower()
        if low == "always":
            tokens.append(("always", ""))
        elif low.startswith(CUE_PREFIXES):
            k, _, v = low.partition(":")
            tokens.append((k, v))
        else:
            break  # free text begins; ignored by cues
    return tokens


def cue_matches(fires_when: str, date: dt.date, phase: str | None, topics: list[str]) -> bool:
    wd = WEEKDAYS[date.weekday()]
    topics_l = {t.lower() for t in topics}
    for k, v in parse_cue_tokens(fires_when):
        if k == "always":
            return True
        if k == "weekday" and v[:3] == wd:
            return True
        if k == "date" and v == date.isoformat():
            return True
        if k == "phase" and phase and v == phase.lower():
            return True
        if k == "topic" and v in topics_l:
            return True
    return False


def cues_due(rows: list[dict], date: dt.date, phase: str | None, topics: list[str]) -> tuple[list[dict], list[dict]]:
    live, archived = [], []
    for r in rows:
        if not r.get("fires_when") or not cue_matches(r["fires_when"], date, phase, topics):
            continue
        (live if is_live(r) else archived).append(r)
    return live, archived


def cmd_cues(ctx: Ctx, date: str | None, phase: str | None, topics: list[str]) -> None:
    d = dt.date.fromisoformat(date) if date else today()
    live, archived = cues_due(load_catalog(ctx), d, phase, topics)
    print(f"cues for {d.isoformat()} ({WEEKDAYS[d.weekday()]}) phase={phase or '-'} topics={','.join(topics) or '-'}")
    for r in live:
        print(f"{r['id']} | {r['fires_when']} | {r['path']}")
    if archived:
        print("archived (resurrection candidates):")
        for r in archived:
            print(f"{r['id']} | {r['fires_when']} | {r['path']}")


# --------------------------------------------------------------------------- lint
def _resolve_names(rows: list[dict]) -> set[str]:
    names: set[str] = set()
    for r in rows:
        names.add(r["id"])
        names.add(r["name"])
        names.add(Path(r["path"]).stem)
        fm = r.get("frontmatter") or {}
        for a in as_list(fm.get("aliases")):
            names.add(a)
    return names


def _raw_ref(ctx: Ctx, ref: str) -> tuple[Path | None, int | None, int | None]:
    """raw:<sid-prefix>#Lx-Ly -> (md path, x, y)."""
    spec = ref[len("raw:"):]
    sid, _, lines = spec.partition("#")
    matches = sorted(ctx.raw.glob(f"{sid}*.md"))
    md = matches[0] if matches else None
    x = y = None
    m = re.match(r"L(\d+)(?:-L?(\d+))?$", lines)
    if m:
        x = int(m.group(1))
        y = int(m.group(2) or m.group(1))
    return md, x, y


def provenance_ok(ctx: Ctx, row: dict, by_path: dict[str, dict], seen: set[str] | None = None) -> bool:
    seen = seen or set()
    for s in row.get("sources", []):
        if s.startswith("raw:"):
            return True
        if s.startswith("private/") or s.startswith(rel(ctx.vault, ctx.root) + "/"):
            if s in seen:
                continue
            seen.add(s)
            nxt = by_path.get(s)
            if nxt and provenance_ok(ctx, nxt, by_path, seen):
                return True
    return False


def run_lint(ctx: Ctx, quick: bool = False) -> tuple[list[str], list[str], float | None]:
    errors: list[str] = []
    warnings: list[str] = []
    rows = build_catalog(ctx)
    vrel = rel(ctx.vault, ctx.root)
    by_path = {r["path"]: r for r in rows}
    names = _resolve_names(rows)
    tday = today()

    # 1. frontmatter + type on every vault note
    for p in vault_md_files(ctx.vault):
        if p.name.startswith("README") or p.stem in LINT_EXEMPT_STEMS:
            continue
        fm, _, err = split_frontmatter(p.read_text(encoding="utf-8"))
        relp = rel(p, ctx.root)
        if err:
            errors.append(f"{relp}: frontmatter unparseable ({err})")
        elif fm is None:
            errors.append(f"{relp}: missing frontmatter")
        elif not fm.get("type"):
            errors.append(f"{relp}: frontmatter has no `type`")

    # 2. unique ids
    seen_ids: dict[str, str] = {}
    for r in rows:
        if r["id"] in seen_ids:
            errors.append(f"duplicate id {r['id']}: {seen_ids[r['id']]} and {r['path']}")
        seen_ids.setdefault(r["id"], r["path"])

    # 3. sources resolve
    for r in rows:
        for s in r.get("sources", []):
            if s.startswith("raw:"):
                md, x, y = _raw_ref(ctx, s)
                if md is None:
                    errors.append(f"{r['path']}: source {s} -> no rendered transcript")
                elif y is not None:
                    n = len(md.read_text(encoding="utf-8").splitlines())
                    if y > n:
                        errors.append(f"{r['path']}: source {s} -> L{y} beyond {n} lines of {md.name}")
            elif s.startswith("pending:"):
                sid = s[len("pending:"):]
                rendered = any(ctx.raw.glob(f"{sid}*.md"))
                if rendered and r.get("updated") != tday.isoformat():
                    errors.append(f"{r['path']}: source {s} still pending but transcript is rendered")
            else:
                spath, _, frag = s.partition("#")
                if spath == "DECISIONS.md" and frag:
                    if f"decision/{frag}" not in seen_ids:
                        errors.append(f"{r['path']}: source {s} -> no such decision")
                elif spath.startswith("private/") or spath.startswith(vrel + "/"):
                    if not (ctx.root / spath).exists():
                        errors.append(f"{r['path']}: source {s} does not exist")
                elif (ctx.root / spath).exists():
                    pass  # repo-relative document (docs/, MAP.md, ...)
                else:
                    warnings.append(f"{r['path']}: source {s} unresolved (unknown scheme or missing path)")

    # 4. superseded_by + wikilinks resolve
    for r in rows:
        sb = r.get("superseded_by")
        if sb and sb not in names:
            errors.append(f"{r['path']}: superseded_by {sb} does not resolve")
        if not quick and not is_template_path(r["path"]):
            for link in r.get("links", []):
                if link not in names:
                    errors.append(f"{r['path']}: [[{link}]] does not resolve")

    # 5. size caps
    def cap(path: Path, max_lines: int | None, max_bytes: int | None, label: str, chars: bool = False):
        if not path.exists():
            return
        text = path.read_text(encoding="utf-8")
        n_lines = len(text.splitlines())
        n_size = len(text) if chars else len(text.encode("utf-8"))
        if max_lines is not None and n_lines > max_lines:
            errors.append(f"{label}: {n_lines} lines > {max_lines}")
        if max_bytes is not None and n_size > max_bytes:
            errors.append(f"{label}: {n_size} {'chars' if chars else 'bytes'} > {max_bytes}")

    cap(ctx.vault / "GOAL-STATE.md", GOAL_STATE_LINES, GOAL_STATE_BYTES, "GOAL-STATE.md")
    cap(ctx.vault / "memory" / "MEMORY.md", MEMORY_INDEX_LINES, MEMORY_INDEX_BYTES, "memory/MEMORY.md")
    cap(ctx.index / "WAKE-PACK.md", None, WAKE_PACK_CAP, "index/WAKE-PACK.md", chars=True)

    # 6. contradictions (warning)
    if not quick:
        live = [r for r in rows if is_live(r) and r.get("tags")]
        for i, a in enumerate(live):
            for b in live[i + 1:]:
                if len(set(a["tags"]) & set(b["tags"])) >= 2:
                    ta = set(a.get("fires_when", "").lower().split())
                    tb = set(b.get("fires_when", "").lower().split())
                    if ta & tb:
                        warnings.append(f"possible contradiction: {a['id']} vs {b['id']} (tags {sorted(set(a['tags']) & set(b['tags']))})")

    # 7. dangling profile references in .claude/
    if not (ctx.vault / "profile.md").exists():
        claude_dir = ctx.root / ".claude"
        for p in sorted(claude_dir.rglob("*.md")) if claude_dir.is_dir() else []:
            if "private/profile.md" in p.read_text(encoding="utf-8"):
                warnings.append(f"{rel(p, ctx.root)}: references private/profile.md which does not exist")

    # 8. outer-repo guard
    if (ctx.root / ".git").exists():
        for target in (vrel + "/", ".claude/settings.local.json"):
            proc = subprocess.run(["git", "-C", str(ctx.root), "check-ignore", "-q", target], capture_output=True)
            if proc.returncode != 0:
                errors.append(f"outer repo: {target} is not gitignored")
        proc = subprocess.run(["git", "-C", str(ctx.root), "ls-files"], capture_output=True, text=True)
        tracked = [l for l in proc.stdout.splitlines() if l.startswith(vrel + "/")]
        if tracked:
            errors.append(f"outer repo: {len(tracked)} tracked file(s) under {vrel}/")
    else:
        warnings.append("outer repo: no .git at repo root; guard skipped")

    # provenance %
    facts = [r for r in rows if r["type"] == "fact" and is_live(r)]
    prov = None
    if facts:
        prov = round(100.0 * sum(1 for f in facts if provenance_ok(ctx, f, by_path)) / len(facts), 1)
    return errors, warnings, prov


def cmd_lint(ctx: Ctx, quick: bool) -> int:
    errors, warnings, prov = run_lint(ctx, quick)
    for e in errors:
        print(f"ERROR {e}")
    for w in warnings:
        print(f"WARN  {w}")
    print(f"lint{' --quick' if quick else ''}: {len(errors)} error(s), {len(warnings)} warning(s); "
          f"provenance {prov if prov is not None else 'n/a'}%")
    return 1 if errors else 0


# --------------------------------------------------------------------------- audit
def cmd_audit(ctx: Ctx, pick: int | None, verdict: list[str] | None) -> None:
    audits = ctx.index / "audits.jsonl"
    if verdict:
        aid, status, note = verdict[0], verdict[1], (verdict[2] if len(verdict) > 2 else "")
        if status not in ("ok", "drift"):
            raise SystemExit("audit: verdict must be ok|drift")
        rows = read_jsonl(audits)
        target = None
        for r in reversed(rows):
            if r.get("id") == aid:
                target = r
                break
        if target is None:
            target = {"date": today().isoformat(), "id": aid}
            rows.append(target)
        target.update({"status": status, "note": note, "verdict_date": today().isoformat()})
        audits.parent.mkdir(parents=True, exist_ok=True)
        audits.write_text("".join(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n" for r in rows), encoding="utf-8")
        print(f"audit: {aid} -> {status}")
        return
    n = pick or 2
    facts = [r for r in load_catalog(ctx) if r["type"] == "fact" and is_live(r)]
    rng = random.Random(today().isoformat())
    chosen = rng.sample(facts, min(n, len(facts))) if facts else []
    for r in chosen:
        print(f"== {r['id']}  ({r['path']})")
        print(f"   sources: {r.get('sources') or '[]'}")
        for s in r.get("sources", []):
            if s.startswith("raw:"):
                md, x, y = _raw_ref(ctx, s)
                if md and x:
                    lines = md.read_text(encoding="utf-8").splitlines()
                    for i in range(x, min(y or x, len(lines)) + 1):
                        print(f"   L{i}: {lines[i - 1][:TRUNC]}")
            elif (ctx.root / s).exists():
                for i, line in enumerate((ctx.root / s).read_text(encoding="utf-8").splitlines()[:5], 1):
                    print(f"   {s}:{i}: {line[:TRUNC]}")
        append_jsonl(audits, {"date": today().isoformat(), "id": r["id"], "status": "pending"})
    print(f"audit: {len(chosen)} fact(s) queued as pending in {rel(audits, ctx.root)}")


# --------------------------------------------------------------------------- incidents
def _is_text_block(block) -> bool:
    return isinstance(block, str) or (isinstance(block, dict) and block.get("type") == "text")


def _mask_harness(text: str) -> str:
    """Blank harness-injected tag regions, preserving line count."""
    return HARNESS_TAG_RE.sub(lambda m: "\n" * m.group(0).count("\n"), text)


def user_turn_lines(records: list[dict]):
    """Yield (md_line_no, text) for the partner's own words only: text parts of non-meta user turns,
    skipping assistant turns, tool_use, tool_result and harness-injected tags. Line numbers follow
    render_transcript exactly (header, body parts joined by newline, blank line)."""
    line = 2  # "# Transcript <sid>", ""
    for rec in records:
        rtype = rec.get("type")
        if rtype not in ("user", "assistant"):
            continue
        msg = rec.get("message") or {}
        content = msg.get("content", "")
        blocks = content if isinstance(content, list) else [content]
        parts = [(_is_text_block(b), t) for b, t in ((b, _block_text(b)) for b in blocks) if t]
        own_words = rtype == "user" and not rec.get("isMeta")
        cur = line + 2  # header at line+1, body starts at line+2
        total = 0
        for is_text, text in parts:
            text = text.replace("\r", "")
            n = text.count("\n") + 1
            if own_words and is_text:
                for i, ln in enumerate(_mask_harness(text).split("\n")):
                    if ln:
                        yield cur + i, ln
            cur += n
            total += n
        line += 1 + max(total, 1) + 1  # header + body + blank


def newest_sid(ctx: Ctx) -> str | None:
    cands = [p for p in ctx.raw.glob("*.jsonl")]
    if not cands:
        return None
    return max(cands, key=lambda p: (p.stat().st_mtime, p.name)).stem


def cmd_incidents(ctx: Ctx, sid: str | None, quiet: bool = False) -> list[dict]:
    sid = sid or newest_sid(ctx)
    if not sid:
        if not quiet:
            print("incidents: no transcripts")
        return []
    md = ctx.raw / f"{sid}.md"
    if not md.exists():
        if not quiet:
            print(f"incidents: {sid}.md not rendered")
        return []
    inc_path = ctx.index / "incidents.jsonl"
    existing = {(r.get("sid"), r.get("line")) for r in read_jsonl(inc_path)}
    pats = [re.compile(p, re.IGNORECASE) for p in INCIDENT_PATTERNS]
    found = []
    for no, line in user_turn_lines(read_jsonl(ctx.raw / f"{sid}.jsonl")):
        for pat in pats:
            m = pat.search(line)
            if m:
                row = {"date": today().isoformat(), "sid": sid, "line": no, "phrase": m.group(0)}
                if (sid, no) not in existing:
                    append_jsonl(inc_path, row)
                    existing.add((sid, no))
                    found.append(row)
                break
    if not quiet:
        print(f"incidents: {len(found)} new in {sid}")
        for r in found:
            print(f"  L{r['line']}: {r['phrase']}")
    return found


# --------------------------------------------------------------------------- wakepack
def newest_source(ctx: Ctx, exclude_sid: str | None = None) -> Path | None:
    """Newest session transcript (the just-started one excluded): Claude Code's dir, else the vault's
    raw copies (Claude Code prunes old transcripts)."""
    for d in (ctx.transcripts, ctx.raw):
        cands = [p for p in d.glob("*.jsonl") if p.stem != exclude_sid] if d.is_dir() else []
        if cands:
            return max(cands, key=lambda q: (q.stat().st_mtime, q.name))
    return None


def health_reasons(ctx: Ctx, src: Path) -> list[str]:
    """Why HEALTH does not vouch for transcript `src` (empty = ok). A failure is reported whatever
    HEALTH's age: the session goes on writing its transcript after /sleep's full night."""
    health = ctx.index / "HEALTH.md"
    sid = src.stem
    reasons = []
    md = ctx.raw / f"{sid}.md"
    raw_jsonl = ctx.raw / f"{sid}.jsonl"
    if not md.exists() or not raw_jsonl.exists() or sha256_file(raw_jsonl) != sha256_file(src):
        reasons.append(f"transcript {sid[:8]} not rendered")
    if not health.exists():
        reasons.append("no HEALTH.md")
        return reasons
    if health.stat().st_mtime < src.stat().st_mtime:
        reasons.append("HEALTH older than newest transcript")
    if health.read_text(encoding="utf-8").startswith("fail"):
        reasons.append(FAILED_REASON)
    return reasons


def brain_is_stale(ctx: Ctx, exclude_sid: str | None = None) -> bool:
    """A night is owed: something un-captured, or HEALTH older than the newest transcript.
    A failed run alone is not staleness — a quick night cannot fix lint errors (it carries them)."""
    src = newest_source(ctx, exclude_sid)
    return src is not None and any(r != FAILED_REASON for r in health_reasons(ctx, src))


def health_line(ctx: Ctx, exclude_sid: str | None = None) -> str:
    health = ctx.index / "HEALTH.md"
    hts = health.read_text(encoding="utf-8").split()[1] if health.exists() and len(health.read_text(encoding="utf-8").split()) > 1 else "never"
    src = newest_source(ctx, exclude_sid)
    if src is None:
        return f"HEALTH: ok (night run {hts}, newest transcript none)"
    mtime = dt.datetime.fromtimestamp(src.stat().st_mtime).replace(microsecond=0).isoformat()
    reasons = health_reasons(ctx, src)
    status = "ok" if not reasons else "STALE " + "; ".join(reasons)
    return f"HEALTH: {status} (night run {hts}, newest transcript {src.stem} {mtime})"


def build_wakepack(ctx: Ctx, exclude_sid: str | None = None) -> str:
    top = [health_line(ctx, exclude_sid)]
    try:
        line = drift_line(ctx)
        if line:
            top.append(line)
    except Exception as exc:  # the drift check is a bonus; never block a wake on it
        _log_hook_error(ctx, "drift", exc)
    sections: list[list[str]] = [top]
    goal = ctx.vault / "GOAL-STATE.md"
    if goal.exists():
        sections.append(["## GOAL-STATE"] + goal.read_text(encoding="utf-8").rstrip("\n").splitlines())
    if read_version(ctx.root).get("role") == "hq":
        try:
            sections.append(portfolio_section(cmd_portfolio(ctx)))
        except Exception as exc:  # the map is a bonus; never block a wake on it
            _log_hook_error(ctx, "portfolio", exc)
    try:
        items = inbox(ctx)
        if items:
            sections.append(inbox_section(items))
    except Exception as exc:  # an unreadable bus must not cost the wake
        _log_hook_error(ctx, "inbox", exc)
    rows = load_catalog(ctx)
    live, archived = cues_due(rows, today(), None, [])
    cue_lines = ["## Cues due today"] + [f"- {r['id']} — {r['fires_when']} ({r['path']})" for r in live]
    if archived:
        cue_lines += ["archived (resurrection candidates):"] + [f"- {r['id']} — {r['fires_when']}" for r in archived]
    if not live and not archived:
        cue_lines.append("- none")
    sections.append(cue_lines)
    mem = ctx.vault / "memory" / "MEMORY.md"
    if mem.exists():
        sections.append(["## Hot index"] + mem.read_text(encoding="utf-8").rstrip("\n").splitlines())
    episodes = [p for d in ("sessions", "checkins") for p in (ctx.vault / d).glob("*.md")] if ctx.vault.exists() else []
    if episodes:
        last = max(episodes, key=lambda p: (p.name, p.stat().st_mtime))
        first8 = last.read_text(encoding="utf-8").splitlines()[:8]
        sections.append(["## Last episode", f"{rel(last, ctx.root)}"] + first8)
    health = ctx.index / "HEALTH.md"
    if health.exists():
        sections.append(["## Health"] + health.read_text(encoding="utf-8").rstrip("\n").splitlines()[:12])

    def render() -> str:
        return "\n\n".join("\n".join(s) for s in sections if s) + "\n"

    text = render()
    while len(text) > WAKE_PACK_CAP and len(sections) > 1:
        last = sections[-1]
        if len(last) > 1:
            last.pop()
        else:
            sections.pop()
        text = render()
    if len(text) > WAKE_PACK_CAP:  # only the HEALTH line left and still too long
        text = text[:WAKE_PACK_CAP]
    return text


def wake_capture(ctx: Ctx, exclude_sid: str | None) -> None:
    """Capture at wake. When the last session left the brain stale (SessionEnd rarely fires in the
    desktop app, so `night` never ran), run the quick night it missed — it ingests too; otherwise a
    plain ingest. Never raises, never waits on another night. night talks on stdout and the hook's
    stdout IS the pack: divert it."""
    try:
        stale = brain_is_stale(ctx, exclude_sid)
    except Exception as exc:  # cannot tell: capture anyway
        _log_hook_error(ctx, "wake stale check", exc)
        stale = False
    if stale:
        try:
            with contextlib.redirect_stdout(sys.stderr):
                rc = cmd_night(ctx, quick=True, no_commit=False, wake=True)
            if rc:
                _log_hook_error(ctx, "wake night", RuntimeError(f"night --quick exited {rc}; see HEALTH.md"))
            return
        except BlockingIOError:  # another night is running; it captures and rewrites HEALTH itself
            _log_hook_error(ctx, "wake night", RuntimeError("skipped: another night holds night.lock"))
            return
        except Exception as exc:  # fall through to a plain ingest
            _log_hook_error(ctx, "wake night", exc)
    try:
        cmd_ingest(ctx, quiet=True)
    except Exception as exc:  # never block a wake on housekeeping trouble
        _log_hook_error(ctx, "ingest", exc)


def cmd_wakepack(ctx: Ctx, ingest_missing: bool, do_print: bool, exclude_sid: str | None = None) -> str:
    if ingest_missing:
        for label, fn in (("relocate", lambda: cmd_relocate(ctx)),
                          ("partner link", lambda: cmd_partner(ctx, "link"))):
            try:
                fn()
            except Exception as exc:  # never block a wake on housekeeping trouble
                _log_hook_error(ctx, label, exc)
        wake_capture(ctx, exclude_sid)
    text = build_wakepack(ctx, exclude_sid)
    write_if_changed(ctx.index / "WAKE-PACK.md", text)
    if do_print:
        sys.stdout.write(text)
    return text


# --------------------------------------------------------------------------- hub / spokes
def bus_root() -> Path:
    """Home-level bus shared by every Cleo instance on this machine (CLEO_HOME overrides)."""
    override = os.environ.get("CLEO_HOME")
    return Path(override).expanduser() if override else Path.home() / ".cleo"


def memory_store(root: Path) -> Path:
    """Claude Code's per-project store (transcripts + memory/) keyed to the absolute path."""
    return Path.home() / ".claude" / "projects" / encode_project_path(root)


def read_version(root: Path) -> dict:
    """<root>/.cleo/VERSION is a plain `key: value` file; order preserved."""
    path = root / ".cleo" / "VERSION"
    data: dict[str, str] = {}
    if not path.exists():
        return data
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        key, sep, value = line.partition(":")
        if sep and key.strip():
            data[key.strip()] = value.strip()
    return data


def write_version(root: Path, data: dict) -> None:
    path = root / ".cleo" / "VERSION"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(f"{k}: {v}\n" for k, v in data.items()), encoding="utf-8")


def instance_name(ctx: Ctx) -> str:
    return read_version(ctx.root).get("name") or ctx.root.name


def _log_hook_error(ctx: Ctx, where: str, exc: Exception) -> None:
    append_jsonl(ctx.index / "hook-errors.jsonl", {"ts": now_iso(), "error": f"{where}: {exc!r}"})


def parse_goal_state(text: str) -> dict:
    """phase / commitments / last_thread out of GOAL-STATE.md (tolerant of missing parts)."""
    _, body, _ = split_frontmatter(text)
    phase, commitments, thread = "", [], []
    section = None
    for line in body.splitlines():
        m = PHASE_RE.search(line)
        if m and not phase:
            phase = m.group(1).strip()
            continue
        if line.startswith("## "):
            section = line[3:].strip().lower()
            continue
        stripped = line.strip()
        if section == "commitments" and stripped.startswith("- "):
            item = stripped[2:].strip()
            if item and not PLACEHOLDER_RE.fullmatch(item):
                commitments.append(item)
        elif section == "last thread" and stripped:
            thread.append(stripped)
    return {"phase": phase, "commitments": commitments, "last_thread": " ".join(thread)}


def count_open_partner_tasks(path: Path) -> int:
    if not path.exists():
        return 0
    return sum(1 for line in path.read_text(encoding="utf-8").splitlines()
               if OPEN_TASK_RE.match(line) and "<task" not in line)


def last_session_date(ctx: Ctx) -> str | None:
    """ISO date of the newest transcript: its last `timestamp`, else the file mtime."""
    path = newest_source(ctx)
    if path is None:
        return None
    try:
        size = path.stat().st_size
        with path.open("rb") as fh:
            fh.seek(max(0, size - BEACON_TAIL_BYTES))
            tail = fh.read().decode("utf-8", errors="replace")
        stamps = TIMESTAMP_RE.findall(tail)
        if stamps:
            # Transcript stamps are UTC; report the partner's local date, not the UTC one.
            try:
                return dt.datetime.fromisoformat(stamps[-1].replace("Z", "+00:00")).astimezone().date().isoformat()
            except ValueError:
                return stamps[-1][:10]
    except OSError:
        pass
    return dt.date.fromtimestamp(path.stat().st_mtime).isoformat()


def _beacon_count(ctx: Ctx, label: str, fn) -> int | None:
    """A derived count must never cost the beacon itself: failures are logged, the field is null."""
    try:
        return fn()
    except Exception as exc:
        _log_hook_error(ctx, f"beacon {label}", exc)
        return None


def build_beacon(ctx: Ctx) -> dict:
    ver = read_version(ctx.root)
    goal = ctx.vault / "GOAL-STATE.md"
    parsed = parse_goal_state(goal.read_text(encoding="utf-8")) if goal.exists() else parse_goal_state("")
    health = ctx.index / "HEALTH.md"
    health_first = health.read_text(encoding="utf-8").splitlines()[:1] if health.exists() else []
    return {
        "name": ver.get("name") or ctx.root.name,
        "path": str(ctx.root),
        "role": ver.get("role") or "spoke",
        "installed": ver.get("installed"),
        "blueprint": ver.get("blueprint"),
        "status": "dormant" if (ctx.root / ".cleo" / "DORMANT").exists() else "active",
        "phase": parsed["phase"],
        "commitments": parsed["commitments"],
        "last_thread": parsed["last_thread"],
        "health": (health_first[0].strip() if health_first and health_first[0].strip() else "unknown"),
        "partner_tasks_open": count_open_partner_tasks(ctx.root / "ops" / "PARTNER-TASKS.md"),
        "handoffs_open": _beacon_count(ctx, "inbox", lambda: len(inbox(ctx))),
        "drift": _beacon_count(ctx, "drift", lambda: drift_count(ctx)),
        "last_session": last_session_date(ctx),
        "seed": str(seed) if ver.get("role") == "hq" and (seed := local_seed(ctx.root)) else None,
        "updated": now_iso(),
    }


def beacon_path(root: Path) -> Path:
    return bus_root() / "spokes" / f"{encode_project_path(root)}.json"


def cmd_beacon(ctx: Ctx, do_print: bool = False) -> dict:
    """Write this instance's status beacon onto the bus (atomic: tmp + os.replace)."""
    beacon = build_beacon(ctx)
    dest = beacon_path(ctx.root)
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps(beacon, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, dest)
    if do_print:
        print(json.dumps(beacon, ensure_ascii=False, indent=2))
    return beacon


def load_beacons() -> tuple[list[dict], list[str]]:
    """All parseable beacons on the bus, plus the names of unreadable ones."""
    spokes = bus_root() / "spokes"
    beacons, bad = [], []
    for p in sorted(spokes.glob("*.json")) if spokes.is_dir() else []:
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                raise ValueError("beacon is not a JSON object")
            beacons.append(data)
        except (OSError, ValueError) as exc:
            bad.append(f"{p.name} ({exc.__class__.__name__})")
    return beacons, bad


def beacon_age_days(beacon: dict, ref: dt.date) -> int | None:
    upd = parse_date(beacon.get("updated"))
    return None if upd is None else max(0, (ref - upd).days)


def render_portfolio(ctx: Ctx, beacons: list[dict], bad: list[str]) -> str:
    ref = today()
    here = str(ctx.root)
    active, dormant, stale = [], [], []
    for b in beacons:
        age = beacon_age_days(b, ref)
        if b.get("status") == "dormant":
            dormant.append((b, age))
        elif age is None or age > STALE_BEACON_DAYS:
            stale.append((b, age))
        else:
            active.append((b, age))
    lines = [f"# PORTFOLIO — {len(active)} active · {len(dormant)} dormant · {len(stale)} stale"]
    if not beacons:
        lines += ["", "No spokes are enrolled yet — run `install-cleo.sh` in a project; its nightly "
                  "`memctl night` emits the beacon that lists it here."]
    else:
        active.sort(key=lambda t: (str(t[0].get("updated") or ""), str(t[0].get("name") or "")), reverse=True)

        def name_of(b: dict) -> str:
            n = str(b.get("name") or b.get("path") or "?")
            return n + " (this)" if b.get("path") == here else n

        if active:
            lines += ["", "| Name | Phase | Health | Last session | Open partner tasks | Open handoffs | Drift | Beacon age |",
                      "|---|---|---|---|---|---|---|---|"]
            for b, age in active:
                counts = ["—" if b.get(k) is None else str(b[k]) for k in ("handoffs_open", "drift")]  # null/absent: unknown
                cells = [name_of(b), str(b.get("phase") or "—"), str(b.get("health") or "unknown"),
                         str(b.get("last_session") or "—"), str(b.get("partner_tasks_open", 0)), *counts, f"{age}d"]
                lines.append("| " + " | ".join(c.replace("|", "\\|").replace("\n", " ") for c in cells) + " |")
            lines += ["", "## Last threads"]
            for b, _ in active:
                lines.append(f"- **{name_of(b)}:** {str(b.get('last_thread') or '—')}")
        if dormant:
            lines += ["", "## Dormant"] + [f"- {name_of(b)} — {b.get('path') or '?'}" for b, _ in dormant]
        if stale:
            lines += ["", f"## Stale beacons (>{STALE_BEACON_DAYS} days)"]
            lines += [f"- {name_of(b)} — {b.get('path') or '?'} ({'unknown age' if age is None else f'{age}d'})"
                      for b, age in stale]
    if bad:
        lines += ["", "## Unreadable beacons"] + [f"- {name}" for name in bad]
    return "\n".join(lines) + "\n"


def cmd_portfolio(ctx: Ctx, do_print: bool = False) -> str:
    """Render the map of every spoke on the bus to private/index/PORTFOLIO.md."""
    beacons, bad = load_beacons()
    text = render_portfolio(ctx, beacons, bad)
    write_if_changed(ctx.index / "PORTFOLIO.md", text)
    if do_print:
        sys.stdout.write(text)
    return text


def portfolio_section(text: str) -> list[str]:
    """Wake-pack section for the portfolio, self-capped so it never evicts the hot index."""
    lines = ["## PORTFOLIO"]
    used = len(lines[0])
    for line in text.rstrip("\n").splitlines():
        if used + len(line) + 1 > PORTFOLIO_PACK_CAP:
            lines.append(PORTFOLIO_TRUNC_LINE)
            break
        lines.append(line)
        used += len(line) + 1
    return lines


def _move_if_absent(src: Path, dst: Path, label: str, actions: list[str]) -> None:
    if dst.exists() or dst.is_symlink():
        actions.append(f"kept {label}: already present at destination, left the old copy in place")
        return
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(src), str(dst))
    actions.append(f"moved {label}")


def cmd_relocate(ctx: Ctx) -> list[str]:
    """Self-heal after a hand rename: merge the path-keyed store of the old path into the new one."""
    ver = read_version(ctx.root)
    old = ver.get("path")
    if not old or old == str(ctx.root):
        return []
    actions: list[str] = []
    old_store, new_store = memory_store(Path(old)), memory_store(ctx.root)
    if old_store.is_dir() and old_store != new_store:
        (new_store / "memory").mkdir(parents=True, exist_ok=True)
        old_mem = old_store / "memory"
        if old_mem.is_dir():
            for entry in sorted(old_mem.iterdir()):
                _move_if_absent(entry, new_store / "memory" / entry.name, f"memory/{entry.name}", actions)
        for entry in sorted(old_store.iterdir()):
            if entry.name == "memory":
                continue
            if entry.suffix == ".jsonl" or entry.is_dir():
                _move_if_absent(entry, new_store / entry.name, entry.name, actions)
        for d in (old_mem, old_store):
            if d.is_dir() and not any(d.iterdir()):
                d.rmdir()
                actions.append(f"removed empty {d}")
        leftovers = sorted(rel(p, old_store) for p in old_store.rglob("*") if not p.is_dir()) if old_store.is_dir() else []
        if leftovers:
            actions.append(f"left behind in {old_store}: {', '.join(leftovers)}")
    else:
        actions.append(f"no store to merge at {old_store}")
    ver["path"] = str(ctx.root)
    write_version(ctx.root, ver)
    actions.append(f"VERSION path: {old} -> {ctx.root}")
    # A beacon keyed to the old path would show up at HQ as a ghost spoke; drop it.
    # The next night run writes a fresh one under the new key.
    ghost = beacon_path(Path(old))
    if ghost.exists():
        ghost.unlink()
        actions.append(f"removed ghost beacon {ghost.name}")
    return actions


def note_description(path: Path) -> str:
    try:
        fm, _, _ = split_frontmatter(path.read_text(encoding="utf-8"))
    except OSError:
        return ""
    return str((fm or {}).get("description") or "")


def partner_store_files() -> list[Path]:
    store = bus_root() / "partner"
    if not store.is_dir():
        return []
    return [p for p in sorted(store.glob("*.md")) if p.name not in PARTNER_SKIP and p.is_file()]


def _same_link(link: Path, target: Path) -> bool:
    try:
        return Path(os.readlink(link)).resolve() == target.resolve()
    except OSError:
        return False


def cmd_partner(ctx: Ctx, action: str, do_print: bool = False) -> list[str]:
    """Shared partner memories: `list` the bus store, `link` them into this instance's memory dir."""
    files = partner_store_files()
    if action == "list":
        lines = [f"{p.name} — {note_description(p) or 'no description'}" for p in files]
        if do_print:
            print("\n".join(lines) if lines else f"partner: no notes in {bus_root() / 'partner'}")
        return lines
    if action != "link":
        raise SystemExit("partner: action must be link|list")
    if not files:
        return []
    memdir = memory_store(ctx.root) / "memory"
    memdir.mkdir(parents=True, exist_ok=True)
    actions: list[str] = []
    index = memdir / "MEMORY.md"
    if not index.exists():
        index.write_text(f"# Memory Index — {instance_name(ctx)}\n\n", encoding="utf-8")
        actions.append("created MEMORY.md")
    index_text = index.read_text(encoding="utf-8")
    for src in files:
        target = memdir / src.name
        if target.is_symlink():
            if not _same_link(target, src):
                target.unlink()
                target.symlink_to(src)
                actions.append(f"relinked {src.name}")
        elif target.exists():
            if target.read_bytes() == src.read_bytes():
                target.unlink()
                target.symlink_to(src)
                actions.append(f"replaced identical file {src.name} with a symlink")
            else:
                actions.append(f"CONFLICT {src.name}: local copy differs from the partner store; left in place")
        else:
            target.symlink_to(src)
            actions.append(f"linked {src.name}")
        prefix = f"- [{src.stem}]({src.name})"
        if not any(line.startswith(prefix) for line in index_text.splitlines()):
            if index_text and not index_text.endswith("\n"):
                index_text += "\n"
            index_text += f"{prefix} — {note_description(src) or 'no description'}\n"
            actions.append(f"indexed {src.stem} in MEMORY.md")
    if index_text != index.read_text(encoding="utf-8"):
        index.write_text(index_text, encoding="utf-8")
    if do_print:
        print("\n".join(actions) if actions else "partner link: nothing to do")
    return actions


# --------------------------------------------------------------------------- drift (instance vs seed)
def local_seed(root: Path) -> Path | None:
    """HQ carries the seed inside its own repo, at <root>/blueprint/."""
    cand = root / "blueprint"
    return cand if (cand / "install-cleo.sh").is_file() else None


def find_seed(ctx: Ctx) -> Path | None:
    """HQ: its own blueprint/. A spoke: the seed named by the freshest HQ beacon on the bus."""
    if read_version(ctx.root).get("role") == "hq":
        return local_seed(ctx.root)
    hq = [b for b in load_beacons()[0] if b.get("role") == "hq" and b.get("seed")]
    for b in sorted(hq, key=lambda b: str(b.get("updated") or ""), reverse=True):
        seed = Path(str(b["seed"]))
        if (seed / "install-cleo.sh").is_file():
            return seed
    return None


def install_pairs(seed: Path, public: bool) -> list[tuple[Path, str]]:
    """(seed file, instance-relative destination) for every copy the installer refreshes."""
    pairs: list[tuple[Path, str]] = []
    for src, dst in INSTALL_MAP + (PUBLIC_INSTALL_MAP if public else ()):
        if src.endswith("/"):
            base = seed / src
            for p in sorted(base.rglob("*")) if base.is_dir() else []:
                inner = p.relative_to(base)
                if p.is_file() and p.suffix != ".pyc" and not DRIFT_SKIP_DIRS & set(inner.parts):
                    pairs.append((p, dst + inner.as_posix()))
        elif "*" in src:
            pairs += [(p, dst + p.name) for p in sorted(seed.glob(src)) if p.is_file()]
        elif (seed / src).is_file():
            pairs.append((seed / src, dst))
    return pairs


def _unstamped(path: Path) -> str:
    return STAMP_RE.sub("", path.read_text(encoding="utf-8").replace("\r\n", "\n")).rstrip()


def drift_report(ctx: Ctx) -> tuple[Path | None, list[str]]:
    """(seed or None, instance files that differ from or are missing against the seed)."""
    seed = find_seed(ctx)
    if seed is None:
        return None, []
    public = read_version(ctx.root).get("visibility") == "public"
    differ = []
    for src, dst_rel in install_pairs(seed, public):
        dst = ctx.root / dst_rel
        if not dst.is_file():
            differ.append(f"{dst_rel} (missing)")
        elif sha256_file(src) != sha256_file(dst) and not (dst_rel in STAMPED_DESTS and _unstamped(src) == _unstamped(dst)):
            differ.append(dst_rel)
    return seed, differ


def drift_count(ctx: Ctx) -> int | None:
    """Files lagging the seed, for the beacon (None: no seed found)."""
    seed, differ = drift_report(ctx)
    return None if seed is None else len(differ)


def _seed_git(seed: Path, *args: str) -> str | None:
    """stdout of a git command run in the seed; None when it is not a repo or git fails."""
    try:
        proc = subprocess.run(["git", "-C", str(seed), *args], capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    return proc.stdout.strip() if proc.returncode == 0 else None


def seed_rev(seed: Path) -> str:
    """Short HEAD hash, plus `+dirty` when anything a refresh would copy or run is uncommitted (or git
    cannot tell). Drift compares the working tree — that is what the installer copies. "" outside git."""
    rev = _seed_git(seed, "rev-parse", "--short", "HEAD")
    if not rev:
        return ""
    status = _seed_git(seed, "status", "--porcelain", "--", *SEED_PATHSPECS)
    return rev + ("+dirty" if status is None or status else "")


def refresh_command(ctx: Ctx, seed: Path) -> str:
    ver = read_version(ctx.root)
    cmd = ["bash", str(seed / "install-cleo.sh"), str(ctx.root)]
    if ver.get("name") and ver["name"] != ctx.root.name:
        cmd += ["--name", ver["name"]]  # without it the installer renames the instance to its dir name
    if ver.get("role") == "hq":
        cmd.append("--hq")
    if ver.get("visibility") == "public":
        cmd.append("--public")
    return shlex.join(cmd)


def drift_summary(ctx: Ctx, seed: Path | None, differ: list[str]) -> str:
    if seed is None:
        return "DRIFT: no seed found (not HQ with blueprint/, and no HQ beacon on the bus names one)"
    rev = seed_rev(seed)
    if not differ:
        return f"DRIFT: none (seed {seed}{' @ ' + rev if rev else ''})"
    head = f"DRIFT: {len(differ)} file(s) differ from seed {rev or seed}"
    if rev.endswith("+dirty"):  # no refresh command: it would copy uncommitted, possibly broken code
        return head + " — commit the seed (tests green) first; never refresh from a dirty seed"
    return f"{head} — refresh: {refresh_command(ctx, seed)}"


def drift_line(ctx: Ctx) -> str | None:
    """The wake-pack line: only when this instance lags its seed."""
    seed, differ = drift_report(ctx)
    return drift_summary(ctx, seed, differ) if differ else None


def cmd_drift(ctx: Ctx, do_print: bool = False) -> int:
    seed, differ = drift_report(ctx)
    print(drift_summary(ctx, seed, differ))
    if do_print:
        for d in differ:
            print(f"  {d}")
    return 1 if differ else 0


# --------------------------------------------------------------------------- handoffs (bus mailbox)
def handoff_dir(state: str) -> Path:
    return bus_root() / "handoff" / state


def slug(text: str, cap: int = 40) -> str:
    return SLUG_RE.sub("-", text.lower()).strip("-")[:cap].strip("-") or "x"


def one_line(value) -> str:
    """Collapse whitespace: bus text rendered into the wake pack must never inject lines."""
    return " ".join(str(value).split())


def is_pathlike(to: str) -> bool:
    return "/" in to or to.startswith("~")


def addressed_to(to: str, name: str, path: str, role: str) -> bool:
    """`hq` = the HQ instance; a name matches case-insensitively; a path matches the instance root."""
    to = to.strip()
    if to.casefold() == "hq":
        return role == "hq"
    if to.casefold() == name.casefold():
        return True
    if is_pathlike(to):
        try:
            return Path(to).expanduser().resolve() == Path(path).resolve()
        except (OSError, RuntimeError, ValueError):  # ~nouser, a symlink loop, a NUL byte: matches nothing
            return False
    return False


def load_handoffs(state: str = "open", ctx: Ctx | None = None) -> tuple[list[tuple[Path, dict]], list[str]]:
    """(path, frontmatter) for every parseable handoff in `state`, plus the unreadable ones. One bad
    file is skipped (and logged, given a ctx); it never costs the good ones."""
    d = handoff_dir(state)
    items, bad = [], []
    for p in sorted(d.glob("*.md"), key=lambda q: q.stem) if d.is_dir() else []:  # stem: x before x-2
        try:
            fm, _, err = split_frontmatter(p.read_text(encoding="utf-8"))
        except Exception as exc:  # unreadable, undecodable, or anything the parser did not foresee
            fm, err = None, exc.__class__.__name__
            if ctx is not None:
                _log_hook_error(ctx, f"handoff {p.name}", exc)
        if err or not fm or not fm.get("to"):
            bad.append(f"{p.name} ({err or 'no `to`'})")
        else:
            items.append((p, fm))
    return items, bad


def inbox(ctx: Ctx) -> list[tuple[Path, dict]]:
    """Open handoffs addressed to this instance."""
    me = (instance_name(ctx), str(ctx.root), read_version(ctx.root).get("role") or "spoke")
    return [(p, fm) for p, fm in load_handoffs("open", ctx)[0] if addressed_to(str(fm["to"]), *me)]


def handoff_sender(fm: dict) -> str:
    sender = fm.get("from")
    if isinstance(sender, dict):
        sender = sender.get("name") or sender.get("path")
    return one_line(sender or "?")


def handoff_age(fm: dict) -> str:
    try:
        created = parse_date(fm.get("created"))
    except ValueError:  # an impossible date such as 2026-02-30
        created = None
    return "?d" if created is None else f"{max(0, (today() - created).days)}d"


def inbox_section(items: list[tuple[Path, dict]]) -> list[str]:
    """Titles only (bodies are read on demand), self-capped so it never evicts the hot index."""
    lines = ["## INBOX"]
    used = len(lines[0])
    for p, fm in items:
        line = f"- {one_line(fm.get('subject') or p.stem)} — from {handoff_sender(fm)}, {handoff_age(fm)} — {p}"
        if used + len(line) + 1 > INBOX_PACK_CAP:
            lines.append(INBOX_TRUNC_LINE)
            break
        lines.append(line)
        used += len(line) + 1
    return lines


def cmd_handoff_send(ctx: Ctx, to: str, subject: str, body: str, sources: list[str]) -> Path:
    """Queue a task for another instance (or `hq`) on the bus: open/<stamp>-<from>-to-<to>-<subject>.md."""
    to, subject = to.strip(), one_line(subject)
    if not to or not subject or not body.strip():
        raise SystemExit("handoff send: --to, --subject and the body must not be empty")
    if is_pathlike(to):
        try:  # stored absolute: the receiver reads it later, from another cwd
            to = str(Path(to).expanduser().resolve())
        except (OSError, RuntimeError, ValueError) as exc:
            raise SystemExit(f"handoff send: cannot resolve --to {to!r} ({exc})") from None
    if not any(addressed_to(to, str(b.get("name") or ""), str(b.get("path") or "/nonexistent"), str(b.get("role") or ""))
               for b in load_beacons()[0]):
        print(f"handoff send: warning — no beacon on the bus matches --to {to!r}; it waits until one does",
              file=sys.stderr)
    created = now_iso()
    stamp = dt.datetime.fromisoformat(created).strftime("%Y%m%d-%H%M%S")
    name = instance_name(ctx)
    base = f"{stamp}-{slug(name)}-to-{slug(Path(to).name if is_pathlike(to) else to)}-{slug(subject)}"
    fm = {"from": {"name": name, "path": str(ctx.root)}, "to": to, "created": created,
          "status": "open", "subject": subject, "sources": list(sources)}
    text = ("---\n" + yaml.safe_dump(fm, sort_keys=False, allow_unicode=True) + "---\n"
            + f"# {subject}\n\n{body.strip()}\n")
    dest_dir = handoff_dir("open")
    dest_dir.mkdir(parents=True, exist_ok=True)
    n = 1
    while True:
        dest = dest_dir / (f"{base}.md" if n == 1 else f"{base}-{n}.md")
        try:
            with dest.open("x", encoding="utf-8") as fh:  # exclusive: never clobber a same-second twin
                fh.write(text)
            return dest
        except FileExistsError:
            n += 1


def find_open_handoff(ctx: Ctx, ref: str, force: bool = False) -> Path:
    """An open handoff addressed here, by id (file stem), file name, path, or a unique id prefix.
    Closing is the receiver's act: an exact id addressed elsewhere is refused unless `force`."""
    name = Path(ref).name
    stem = name[:-3] if name.endswith(".md") else name
    exact = handoff_dir("open") / f"{stem}.md"
    mine = [p for p, _ in inbox(ctx)]
    if stem and exact.is_file():
        if exact in mine or force:
            return exact
        raise SystemExit(f"handoff done: {stem} is not addressed to {instance_name(ctx)}; --force closes it anyway")
    cands = [p for p in mine if p.stem.startswith(stem)] if stem else []
    if len(cands) == 1:
        return cands[0]
    raise SystemExit(f"handoff done: {'ambiguous' if cands else 'no'} open handoff addressed here matches {ref!r}")


def cmd_handoff_done(ctx: Ctx, ref: str, note: str | None, force: bool = False) -> Path:
    """Close a handoff: move it to done/ with status done and a dated resolution section."""
    src = find_open_handoff(ctx, ref, force)
    dest = handoff_dir("done") / src.name
    if dest.exists():
        raise SystemExit(f"handoff done: {dest} already exists")
    try:
        fm, body, err = split_frontmatter(src.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise SystemExit(f"handoff done: {src.name} was already closed") from None
    if fm is None or err:
        raise SystemExit(f"handoff done: {src.name} has unreadable frontmatter ({err or 'none'}); fix it by hand")
    fm["status"] = "done"
    text = ("---\n" + yaml.safe_dump(fm, sort_keys=False, allow_unicode=True) + "---\n" + body.rstrip("\n")
            + f"\n\n## Resolution\n\n- {today().isoformat()} — done by {instance_name(ctx)} ({ctx.root}): "
            + f"{one_line(note) if note else 'no note'}\n")
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(f".{dest.name}.{os.getpid()}.tmp")
    tmp.write_text(text, encoding="utf-8")
    try:
        os.replace(src, dest)  # the claim: of two instances closing the same item, only one wins
    except FileNotFoundError:
        tmp.unlink()
        raise SystemExit(f"handoff done: {src.name} was already closed") from None
    os.replace(tmp, dest)  # then the resolved text, atomically
    return dest


def cmd_handoff_list(ctx: Ctx, show_all: bool, do_print: bool) -> list[tuple[Path, dict]]:
    """Open handoffs addressed here (--all: every open handoff on the bus)."""
    items, bad = load_handoffs("open", ctx)
    if not show_all:
        items = inbox(ctx)
    print(f"handoff: {len(items)} open {'on the bus' if show_all else 'addressed to ' + instance_name(ctx)}")
    if do_print:
        for p, fm in items:
            print(f"- {p.stem}: {one_line(fm.get('subject') or '?')} (from {handoff_sender(fm)} to "
                  f"{one_line(fm.get('to'))}, {handoff_age(fm)}) {p}")
        for b in bad:
            print(f"! unreadable: {b}")
    return items


# --------------------------------------------------------------------------- night
def vault_git(ctx: Ctx, *args: str, check: bool = False) -> subprocess.CompletedProcess:
    cmd = ["git", "-C", str(ctx.vault), "-c", "user.name=memctl", "-c", "user.email=memctl@local"] + list(args)
    return subprocess.run(cmd, capture_output=True, text=True, check=check)


def write_health(ctx: Ctx, status: str, summary: list[str]) -> None:
    text = f"{status} {now_iso()} " + (summary[0] if summary else "") + "\n"
    if len(summary) > 1:
        text += "\n".join(f"- {s}" for s in summary[1:]) + "\n"
    ctx.index.mkdir(parents=True, exist_ok=True)
    (ctx.index / "HEALTH.md").write_text(text, encoding="utf-8")


def carried_failure(ctx: Ctx) -> list[str]:
    """The last full night's failure — lint errors, or a crash before lint finished — which a quick
    night must carry forward: it cannot re-run lint, so it cannot vouch for it. Only a full night
    that completes clean writes ok. A quick night's own crash, carrying nothing, is not carried."""
    health = ctx.index / "HEALTH.md"
    lines = health.read_text(encoding="utf-8").splitlines() if health.exists() else []
    if not lines or not lines[0].startswith("fail "):
        return []
    parts = lines[0].split(" ", 2)  # "fail <ts> <summary>"
    head = parts[2].split(CARRY_MARK, 1)[-1] if len(parts) > 2 else ""  # already carried once: no nesting
    if head.startswith(QUICK_CRASH):
        return []
    return [head] + [line[2:] if line.startswith("- ") else line for line in lines[1:]]


def with_carry(summary: list[str], carried: list[str]) -> list[str]:
    """HEALTH summary lines with a carried failure appended (first line marked, details after)."""
    return [f"{summary[0]}; {CARRY_MARK}{carried[0]}"] + summary[1:] + carried[1:] if carried else summary


def emit_beacon(ctx: Ctx) -> None:
    """Beacon from night: failures are logged, never fatal."""
    try:
        cmd_beacon(ctx)
    except Exception as exc:
        _log_hook_error(ctx, "beacon", exc)


def cmd_night(ctx: Ctx, quick: bool, no_commit: bool, wake: bool = False) -> int:
    """wake=True (the SessionStart hook): never queue behind another night — raise BlockingIOError
    instead — and leave the wake pack to the caller, which builds it once, after this run."""
    ctx.index.mkdir(parents=True, exist_ok=True)
    lock_path = ctx.vault / ".git" / "night.lock"
    lock_fh = None
    if lock_path.parent.is_dir():
        lock_fh = lock_path.open("w")
        try:
            fcntl.flock(lock_fh, fcntl.LOCK_EX | (fcntl.LOCK_NB if wake else 0))
        except OSError:
            lock_fh.close()
            raise
    carried: list[str] = []
    try:
        try:
            carried = carried_failure(ctx) if quick else []  # read before this run touches HEALTH
            changed = cmd_ingest(ctx, quiet=True)  # includes catalog rebuild
            summary: list[str] = []
            errors: list[str] = []
            # Nothing new AND HEALTH already vouches for the newest transcript. A stale HEALTH (an
            # earlier wake ingested, but no night ran since) still needs this run to rewrite it.
            if quick and not changed and not brain_is_stale(ctx):
                emit_beacon(ctx)  # cheap, and the bus must still see the current state
                print("night --quick: no new transcript / catalog delta; nothing to do")
                return 0
            if not quick:
                inc = cmd_incidents(ctx, None, quiet=True)
                errors, warnings, prov = run_lint(ctx, quick=False)
                cutoff = (today() - dt.timedelta(days=14)).isoformat()
                inc14 = sum(1 for r in read_jsonl(ctx.index / "incidents.jsonl") if r.get("date", "") >= cutoff)
                pending = sum(1 for r in read_jsonl(ctx.index / "audits.jsonl") if r.get("status") == "pending")
                # audit pick (prints to stdout)
                cmd_audit(ctx, 2, None)
                pending_after = sum(1 for r in read_jsonl(ctx.index / "audits.jsonl") if r.get("status") == "pending")
                summary.append(f"lint {len(errors)} error(s) {len(warnings)} warning(s); provenance "
                               f"{prov if prov is not None else 'n/a'}%; incidents14d {inc14} (+{len(inc)} new); "
                               f"audits pending {pending_after} (was {pending})")
                summary += [f"lint error: {e}" for e in errors[:20]]
                summary += [f"lint warning: {w}" for w in warnings[:20]]
            else:
                summary.append(f"quick run; {len(changed)} file(s) ingested/rebuilt")
            summary = with_carry(summary, carried)
            failed = bool(errors or carried)
            write_health(ctx, "fail" if failed else "ok", summary)
            emit_beacon(ctx)
            if not wake:
                cmd_wakepack(ctx, ingest_missing=False, do_print=False)
            sid = newest_sid(ctx) or "none"
            if not no_commit and (ctx.vault / ".git").is_dir():
                status = vault_git(ctx, "status", "--porcelain").stdout.strip()
                if status:
                    vault_git(ctx, "add", "-A", check=True)
                    msg = f"night{'-quick' if quick else ''} {today().isoformat()} sid={sid}"
                    vault_git(ctx, "commit", "-q", "-m", msg, check=True)
                    print(f"night: committed '{msg}'")
                else:
                    print("night: vault clean; no commit")
            print(f"night: HEALTH {'fail' if failed else 'ok'}; {summary[0]}")
            return 1 if errors else 0  # a carried failure is old news, not a failure of this run
        except Exception as exc:
            # a crash must not drop the failure this quick run was carrying
            write_health(ctx, "fail", with_carry([f"{QUICK_CRASH if quick else 'night crashed'}: {exc!r}"], carried))
            print(f"night: FAILED {exc!r}", file=sys.stderr)
            return 1
    finally:
        if lock_fh:
            fcntl.flock(lock_fh, fcntl.LOCK_UN)
            lock_fh.close()


# --------------------------------------------------------------------------- main
def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="memctl", description=__doc__)
    ap.add_argument("--vault", help="vault root (default: <repo>/private)")
    ap.add_argument("--transcripts", help="Claude Code project transcript dir")
    ap.add_argument("--root", help="repo root (default: walk up for MISSION.md)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("ingest")
    sub.add_parser("catalog")
    q = sub.add_parser("query"); q.add_argument("text"); q.add_argument("--type"); q.add_argument("--since"); q.add_argument("--top", type=int, default=10)
    c = sub.add_parser("cues"); c.add_argument("--date"); c.add_argument("--phase"); c.add_argument("--topic", nargs="*", default=[])
    l = sub.add_parser("lint"); l.add_argument("--quick", action="store_true")
    a = sub.add_parser("audit"); a.add_argument("--pick", type=int); a.add_argument("--verdict", nargs="+", metavar=("ID", "STATUS"))
    i = sub.add_parser("incidents"); i.add_argument("--sid")
    w = sub.add_parser("wakepack"); w.add_argument("--ingest-missing", action="store_true"); w.add_argument("--print", action="store_true"); w.add_argument("--exclude-sid")
    n = sub.add_parser("night"); n.add_argument("--quick", action="store_true"); n.add_argument("--no-commit", action="store_true")
    sub.add_parser("health")
    b = sub.add_parser("beacon"); b.add_argument("--print", action="store_true")
    p = sub.add_parser("portfolio"); p.add_argument("--print", action="store_true")
    sub.add_parser("relocate")
    pa = sub.add_parser("partner"); pa.add_argument("action", choices=["link", "list"])
    dr = sub.add_parser("drift", help="compare this instance's vendored files with its seed")
    dr.add_argument("--print", action="store_true", help="also list the files that differ")
    h = sub.add_parser("handoff", help="cross-instance task mailbox on the bus")
    hs = h.add_subparsers(dest="action", required=True)
    hsend = hs.add_parser("send"); hsend.add_argument("--to", required=True, help="instance name, path, or hq")
    hsend.add_argument("--subject", required=True); hsend.add_argument("--sources", nargs="+", default=[])
    hbody = hsend.add_mutually_exclusive_group(required=True)
    hbody.add_argument("--body"); hbody.add_argument("--body-file", help="file with the body; - for stdin")
    hlist = hs.add_parser("list"); hlist.add_argument("--all", action="store_true", help="every open handoff on the bus")
    hlist.add_argument("--print", action="store_true", help="also list the items")
    hdone = hs.add_parser("done"); hdone.add_argument("id"); hdone.add_argument("--note")
    hdone.add_argument("--force", action="store_true", help="close a handoff not addressed to this instance")
    args = ap.parse_args(argv)

    root = Path(args.root).resolve() if args.root else find_repo_root()
    vault = Path(args.vault).resolve() if args.vault else root / "private"
    transcripts = Path(args.transcripts).expanduser().resolve() if args.transcripts else default_transcripts(root)
    ctx = Ctx(root, vault, transcripts)

    if args.cmd == "ingest":
        cmd_ingest(ctx); return 0
    if args.cmd == "catalog":
        cmd_catalog(ctx); return 0
    if args.cmd == "query":
        cmd_query(ctx, args.text, args.type, args.since, args.top); return 0
    if args.cmd == "cues":
        cmd_cues(ctx, args.date, args.phase, args.topic); return 0
    if args.cmd == "lint":
        return cmd_lint(ctx, args.quick)
    if args.cmd == "audit":
        cmd_audit(ctx, args.pick, args.verdict); return 0
    if args.cmd == "incidents":
        cmd_incidents(ctx, args.sid); return 0
    if args.cmd == "wakepack":
        cmd_wakepack(ctx, args.ingest_missing, args.print, args.exclude_sid); return 0
    if args.cmd == "night":
        return cmd_night(ctx, args.quick, args.no_commit)
    if args.cmd == "health":
        h = ctx.index / "HEALTH.md"
        print(h.read_text(encoding="utf-8").rstrip("\n") if h.exists() else "no HEALTH.md yet (run `memctl night`)")
        return 0
    if args.cmd == "beacon":
        cmd_beacon(ctx, do_print=args.print); return 0
    if args.cmd == "portfolio":
        cmd_portfolio(ctx, do_print=args.print); return 0
    if args.cmd == "relocate":
        acts = cmd_relocate(ctx)
        print("\n".join(acts) if acts else "relocate: VERSION path matches; nothing to do"); return 0
    if args.cmd == "partner":
        cmd_partner(ctx, args.action, do_print=True); return 0
    if args.cmd == "drift":
        return cmd_drift(ctx, do_print=args.print)
    if args.cmd == "handoff":
        if args.action == "send":
            if args.body_file:
                body = sys.stdin.read() if args.body_file == "-" else Path(args.body_file).read_text(encoding="utf-8")
            else:
                body = args.body
            print(cmd_handoff_send(ctx, args.to, args.subject, body, args.sources)); return 0
        if args.action == "list":
            cmd_handoff_list(ctx, args.all, args.print); return 0
        print(f"handoff: closed -> {cmd_handoff_done(ctx, args.id, args.note, args.force)}"); return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())
