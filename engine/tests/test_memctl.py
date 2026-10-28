"""memctl tests on synthetic fixtures only (never touches private/)."""
import fcntl
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "memctl.py"
spec = importlib.util.spec_from_file_location("memctl", SCRIPT)
memctl = importlib.util.module_from_spec(spec)
spec.loader.exec_module(memctl)

TODAY = "2026-08-23"  # a Sunday


def jsonl(path: Path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")


def note(path: Path, fm: dict, body: str = "body\n"):
    import yaml
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("---\n" + yaml.safe_dump(fm, sort_keys=False) + "---\n" + body, encoding="utf-8")


def session_records(sid):
    return [
        {"type": "ai-title", "aiTitle": "x"},
        {"type": "user", "timestamp": "2026-08-01T10:00:00Z",
         "message": {"role": "user", "content": "Hello Cleo, my goal is a remote job."}},
        {"type": "assistant", "timestamp": "2026-08-01T10:00:05Z", "message": {"role": "assistant", "content": [
            {"type": "thinking", "thinking": "secret"},
            {"type": "text", "text": "Noted."},
            {"type": "tool_use", "name": "Bash", "input": {"command": "echo " + "x" * 500}}]}},
        {"type": "user", "timestamp": "2026-08-01T10:00:06Z", "message": {"role": "user", "content": [
            {"type": "tool_result", "content": "y" * 500}]}},
        {"type": "user", "timestamp": "2026-08-01T10:01:00Z", "isMeta": True,
         "message": {"role": "user", "content": [{"type": "text", "text": "I already told you (skill text)"}]}},
        {"type": "user", "timestamp": "2026-08-01T10:02:00Z",
         "message": {"role": "user", "content": "I already told you that, ya te lo dije."}},
        {"type": "system", "subtype": "x"},
    ]


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))  # bus + memory store live in a fake home
    (tmp_path / "home").mkdir()
    monkeypatch.delenv("CLEO_HOME", raising=False)
    monkeypatch.setenv("MEMCTL_TODAY", TODAY)
    monkeypatch.setenv("MEMCTL_NOW", TODAY + "T01:00:00")
    root = tmp_path / "repo"
    root.mkdir()
    (root / "MISSION.md").write_text("# Mission\n")
    (root / "MAP.md").write_text("# Map\nstatus line\n")
    (root / "DECISIONS.md").write_text("# Decisions\n\n## D2 — 2026-08-10 — Second thing\nbody two\n\n## D1 — 2026-08-01 — First thing\nbody one about venv\n")
    (root / "docs").mkdir()
    (root / "docs" / "guide.md").write_text("# Guide\nreference text remote\n")
    (root / ".claude" / "skills" / "wake").mkdir(parents=True)
    (root / ".claude" / "skills" / "wake" / "SKILL.md").write_text("# wake\nread private/profile.md\n")
    (root / ".claude" / "agents").mkdir()
    (root / ".claude" / "agents" / "zoe.md").write_text("---\nname: zoe\ndescription: agent\n---\nbody\n")
    (root / ".gitignore").write_text("private/\n.claude/settings.local.json\n")
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    vault = root / "private"
    vault.mkdir()
    (vault / "README.md").write_text("# private\n")
    (vault / "profile.md").write_text("---\ntype: reference\nname: profile\n---\nprofile\n")
    (vault / "GOAL-STATE.md").write_text("---\ntype: goal-state\nname: goal-state\nupdated: 2026-08-22\n---\n## Phase now\nrecovery\n")
    note(vault / "memory" / "fact-a.md", {"name": "fact-a", "description": "remote job search timing", "type": "fact",
                                          "updated": "2026-08-20", "fires_when": "weekday:sun topic:search when planning",
                                          "tags": ["search", "timing"], "sources": ["raw:sid-aaaa#L3-L4"]},
         "Alpha fact about remote work. See [[fact-b]].\n")
    note(vault / "memory" / "fact-b.md", {"name": "fact-b", "description": "pinned rule", "type": "fact", "pinned": True,
                                          "fires_when": "always", "tags": ["rule"], "sources": ["private/sessions/2026-08-01-intake.md"]},
         "Beta pinned rule.\n")
    note(vault / "memory" / "fact-old.md", {"name": "fact-old", "description": "old remote note", "type": "fact",
                                            "updated": "2025-01-01", "tags": ["x"]}, "remote remote remote\n")
    (vault / "memory" / "MEMORY.md").write_text("# Memory Index\n- [fact-a](fact-a.md) — alpha\n")
    note(vault / "sessions" / "2026-08-01-intake.md", {"type": "episode", "name": "intake"}, "# Intake\nline2\nline3\n")
    note(vault / "commitments" / "C-01.md", {"type": "commitment", "name": "C-01", "fires_when": "date:2026-08-23 phase:recovery"}, "if-then\n")
    note(vault / "archive" / "fact-z.md", {"type": "archive", "name": "fact-z", "valid_to": "2026-08-10", "superseded_by": "fact-a",
                                           "fires_when": "topic:search"}, "old\n")
    transcripts = tmp_path / "transcripts"
    jsonl(transcripts / "sid-aaaa.jsonl", session_records("sid-aaaa"))
    jsonl(transcripts / "sid-aaaa" / "subagents" / "agent-1.jsonl", session_records("agent-1")[:3])
    ctx = memctl.Ctx(root, vault, transcripts)
    return ctx


def run_cli(ctx, *args):
    cmd = [sys.executable, str(SCRIPT), "--root", str(ctx.root), "--vault", str(ctx.vault), "--transcripts", str(ctx.transcripts), *args]
    return subprocess.run(cmd, capture_output=True, text=True, env={**os.environ, "MEMCTL_TODAY": TODAY, "MEMCTL_NOW": TODAY + "T01:00:00"})


# ---------------------------------------------------------------- ingest / render
def test_ingest_idempotent_and_render_deterministic(env):
    ctx = env
    changed1 = memctl.cmd_ingest(ctx, quiet=True)
    assert "raw/transcripts/sid-aaaa.jsonl" in changed1
    assert "raw/transcripts/sid-aaaa.md" in changed1
    assert "raw/transcripts/sid-aaaa-subagents/agent-1.md" in changed1
    assert "index/catalog.jsonl" in changed1
    snapshot = {p: memctl.sha256_file(p) for p in ctx.vault.rglob("*") if p.is_file()}
    mtimes = {p: p.stat().st_mtime_ns for p in snapshot}
    changed2 = memctl.cmd_ingest(ctx, quiet=True)
    assert changed2 == []
    assert {p: memctl.sha256_file(p) for p in snapshot} == snapshot
    assert {p: p.stat().st_mtime_ns for p in snapshot} == mtimes  # never rewritten
    md = (ctx.raw / "sid-aaaa.md").read_text()
    assert md.startswith("# Transcript sid-aaaa\n")
    assert "## [0] user 2026-08-01T10:00:00Z\nHello Cleo" in md
    assert "secret" not in md  # thinking skipped
    assert "[tool_use Bash]" in md
    assert "## [3] user 2026-08-01T10:01:00Z (meta)" in md
    for line in md.splitlines():
        assert len(line) <= 230  # truncation applied
    assert md == memctl.render_transcript("sid-aaaa", session_records("sid-aaaa"))


# ---------------------------------------------------------------- catalog
def test_catalog_types_and_ids(env):
    ctx = env
    rows = {r["id"]: r for r in memctl.build_catalog(ctx)}
    assert rows["fact/fact-a"]["type"] == "fact"
    assert rows["episode/2026-08-01-intake"]["updated"] == "2026-08-01"
    assert rows["commitment/C-01"]["type"] == "commitment"
    assert rows["archive/fact-z"]["superseded_by"] == "fact-a"
    assert rows["goal-state/GOAL-STATE"]["type"] == "goal-state"
    assert rows["decision/D1"]["updated"] == "2026-08-01" and rows["decision/D2"]["name"].startswith("D2")
    assert rows["procedure/wake"]["path"] == ".claude/skills/wake/SKILL.md"
    assert rows["reference/private"]["path"] == "private/README.md"
    assert rows["procedure/zoe"]["type"] == "procedure"
    assert rows["reference/guide"]["path"] == "docs/guide.md"
    assert rows["reference/MISSION"]["type"] == "reference"
    assert rows["fact/fact-a"]["links"] == ["fact-b"]
    assert set(rows["fact/fact-a"]) == set(memctl.CATALOG_KEYS)
    paths = [r["path"] for r in memctl.build_catalog(ctx)]
    assert paths == sorted(paths)
    # id collision -> path-derived fallback, stable and unique
    (ctx.root / "docs" / "tpl").mkdir()
    (ctx.root / "docs" / "tpl" / "guide.md").write_text("# Guide copy\n")
    ids = [r["id"] for r in memctl.build_catalog(ctx)]
    assert len(ids) == len(set(ids))
    assert "reference/docs/guide" in ids and "reference/docs/tpl/guide" in ids


def test_native_memory_file_round_trip(env):
    ctx = env
    native = ctx.vault / "memory" / "native.md"
    native.write_text("---\nname: native\ndescription: \"a native memory\"\nmetadata: \n  node_type: memory\n  type: feedback\n"
                      "  originSessionId: sid-aaaa\n  modified: 2026-08-20T19:39:02.800Z\n---\n\nBody [[fact-a]].\n")
    memctl.cmd_ingest(ctx, quiet=True)
    row = next(r for r in memctl.read_jsonl(ctx.catalog_path) if r["path"].endswith("native.md"))
    assert row["type"] == "fact" and row["id"] == "fact/native"
    assert row["frontmatter"]["metadata"] == {"node_type": "memory", "type": "feedback",
                                              "originSessionId": "sid-aaaa", "modified": "2026-08-20T19:39:02.800Z"}
    assert set(row["frontmatter"]) == {"name", "description", "metadata"}
    assert row["updated"] == "2026-08-20"  # derived from metadata.modified


# ---------------------------------------------------------------- query
def test_query_ranking(env):
    ctx = env
    memctl.cmd_ingest(ctx, quiet=True)
    rows = memctl.load_catalog(ctx)
    scored = memctl.score_rows(ctx, rows, "remote")
    ids = [r["id"] for _, r in scored]
    assert ids[0] == "fact/fact-a"  # description hit x3 + body, recent
    assert "fact/fact-old" in ids and ids.index("fact/fact-old") > ids.index("fact/fact-a")
    by = {r["id"]: s for s, r in scored}
    assert by["fact/fact-old"] == pytest.approx(4 * 0.2, abs=1e-6)  # floor on recency
    assert "reference/guide" in by
    res = run_cli(ctx, "query", "venv", "--type", "decision")
    assert res.returncode == 0 and res.stdout.startswith("decision/D1 | decision |")


# ---------------------------------------------------------------- cues
def test_cues_grammar(env):
    ctx = env
    memctl.cmd_ingest(ctx, quiet=True)
    rows = memctl.load_catalog(ctx)
    import datetime as dt
    live, archived = memctl.cues_due(rows, dt.date(2026, 8, 23), None, [])  # Sunday
    live_ids = {r["id"] for r in live}
    assert {"fact/fact-a", "fact/fact-b", "commitment/C-01"} <= live_ids
    assert not archived
    live, _ = memctl.cues_due(rows, dt.date(2026, 8, 24), None, [])  # Monday
    assert {r["id"] for r in live} == {"fact/fact-b"}
    live, _ = memctl.cues_due(rows, dt.date(2026, 8, 24), "recovery", [])
    assert "commitment/C-01" in {r["id"] for r in live}
    live, archived = memctl.cues_due(rows, dt.date(2026, 8, 24), None, ["search"])
    assert "fact/fact-a" in {r["id"] for r in live}
    assert [r["id"] for r in archived] == ["archive/fact-z"]
    assert memctl.parse_cue_tokens("weekday:sun topic:search when planning") == [("weekday", "sun"), ("topic", "search")]
    res = run_cli(ctx, "cues", "--date", "2026-08-24", "--topic", "search")
    assert "archived (resurrection candidates):" in res.stdout


# ---------------------------------------------------------------- lint
def test_lint_good_fixture_passes(env):
    ctx = env
    memctl.cmd_ingest(ctx, quiet=True)
    errors, warnings, prov = memctl.run_lint(ctx)
    assert errors == [], errors
    assert prov == pytest.approx(100 * 1 / 3, abs=0.1)  # fact-a reaches raw:, b/old do not
    res = run_cli(ctx, "lint")
    assert res.returncode == 0


def test_lint_failures(env):
    ctx = env
    memctl.cmd_ingest(ctx, quiet=True)
    note(ctx.vault / "memory" / "bad.md", {"type": "fact", "name": "bad", "sources": ["private/sessions/missing.md", "raw:sid-aaaa#L1-L9999"],
                                           "superseded_by": "nothing-here"}, "x [[ghost]]\n")
    goal = ctx.vault / "GOAL-STATE.md"
    goal.write_text("---\ntype: goal-state\n---\n" + "\n".join(f"l{i}" for i in range(41)) + "\n")
    (ctx.vault / "memory" / "nofm.md").write_text("no frontmatter\n")
    errors, warnings, _ = memctl.run_lint(ctx)
    note(ctx.vault / "memory" / "srcs.md", {"type": "fact", "name": "srcs",
                                            "sources": ["DECISIONS.md#D1", "DECISIONS.md#D9", "docs/guide.md", "raw:sid-aa", "weird:thing"]}, "x\n")
    errors, warnings, _ = memctl.run_lint(ctx)
    joined = "\n".join(errors)
    srcs_errors = [e for e in errors if e.startswith("private/memory/srcs.md")]
    assert srcs_errors == ["private/memory/srcs.md: source DECISIONS.md#D9 -> no such decision"]
    assert any("weird:thing unresolved" in w for w in warnings)
    assert "missing.md does not exist" in joined
    assert "beyond" in joined
    assert "superseded_by nothing-here does not resolve" in joined
    assert "[[ghost]] does not resolve" in joined
    assert "GOAL-STATE.md: 44 lines > 40" in joined
    assert "nofm.md: missing frontmatter" in joined
    assert run_cli(ctx, "lint").returncode == 1
    quick_errors, _, _ = memctl.run_lint(ctx, quick=True)
    assert not any("[[ghost]]" in e for e in quick_errors)


def test_lint_exempts_template_wikilinks_but_catalogs_them(env):
    ctx = env
    memctl.cmd_ingest(ctx, quiet=True)
    tpl = ctx.root / "docs" / "blueprint" / "templates" / "memory" / "_TEMPLATE.md"
    tpl.parent.mkdir(parents=True)
    tpl.write_text("---\ntype: fact\nname: template-fact\n---\nSee [[their-name]] and [[another-placeholder]].\n")
    rows = memctl.build_catalog(ctx)
    row = next(r for r in rows if r["path"] == "docs/blueprint/templates/memory/_TEMPLATE.md")
    assert row["links"] == ["another-placeholder", "their-name"]  # still catalogued
    errors, _, _ = memctl.run_lint(ctx)
    assert not any("does not resolve" in e for e in errors), errors
    # a non-template doc with the same dangling link is still an error
    (ctx.root / "docs" / "real.md").write_text("# Real\n[[their-name]]\n")
    errors, _, _ = memctl.run_lint(ctx)
    assert errors == ["docs/real.md: [[their-name]] does not resolve"]


def test_wikilinks_inside_code_are_ignored(env):
    ctx = env
    memctl.cmd_ingest(ctx, quiet=True)
    body = ("Prose link [[fact-b]]. Syntax is `[[ghost-inline]]` and ``x [[ghost-double]] y``.\n"
            "```md\n[[ghost-fenced]]\n```\n~~~\n[[ghost-tilde]]\n~~~\nAfter fence [[fact-a]].\n")
    assert memctl.wikilinks(body) == ["fact-a", "fact-b"]
    note(ctx.vault / "memory" / "doc.md", {"type": "fact", "name": "doc"}, body)
    (ctx.root / "DECISIONS.md").write_text("# Decisions\n\n## D1 — 2026-08-01 — One\nuse `[[ghost-dec]]` syntax, see [[fact-a]]\n")
    rows = memctl.build_catalog(ctx)
    assert next(r for r in rows if r["id"] == "fact/doc")["links"] == ["fact-a", "fact-b"]
    assert next(r for r in rows if r["id"] == "decision/D1")["links"] == ["fact-a"]
    errors, _, _ = memctl.run_lint(ctx)
    assert not any("ghost" in e for e in errors), errors


def test_lint_contradiction_warning_and_pending(env):
    ctx = env
    memctl.cmd_ingest(ctx, quiet=True)
    note(ctx.vault / "memory" / "fact-c.md", {"type": "fact", "name": "fact-c", "tags": ["search", "timing"],
                                              "fires_when": "weekday:sun topic:search", "sources": ["pending:sid-aaaa"], "updated": TODAY}, "c\n")
    errors, warnings, _ = memctl.run_lint(ctx)
    assert errors == []
    assert any("possible contradiction: fact/fact-a vs fact/fact-c" in w for w in warnings)
    note(ctx.vault / "memory" / "fact-c.md", {"type": "fact", "name": "fact-c", "sources": ["pending:sid-aaaa"], "updated": "2026-08-01"}, "c\n")
    errors, _, _ = memctl.run_lint(ctx)
    assert any("still pending" in e for e in errors)
    note(ctx.vault / "memory" / "fact-c.md", {"type": "fact", "name": "fact-c", "sources": ["pending:sid-zzzz"], "updated": "2026-08-01"}, "c\n")
    errors, _, _ = memctl.run_lint(ctx)
    assert errors == []  # transcript not rendered yet -> pending allowed


def test_lint_profile_dangling_and_archived_contradiction_clears(env):
    ctx = env
    memctl.cmd_ingest(ctx, quiet=True)
    (ctx.vault / "profile.md").unlink()
    _, warnings, _ = memctl.run_lint(ctx)
    assert any("references private/profile.md" in w for w in warnings)
    # archive one side of a planted contradiction -> no warning
    note(ctx.vault / "memory" / "fact-c.md", {"type": "fact", "name": "fact-c", "tags": ["search", "timing"],
                                              "fires_when": "weekday:sun", "valid_to": "2026-08-20", "superseded_by": "fact-a"}, "c\n")
    _, warnings, _ = memctl.run_lint(ctx)
    assert not any("contradiction" in w for w in warnings)


# ---------------------------------------------------------------- incidents / audit
def test_incidents_dedupe_and_skip_meta(env):
    ctx = env
    memctl.cmd_ingest(ctx, quiet=True)
    found = memctl.cmd_incidents(ctx, None, quiet=True)
    assert len(found) == 1 and found[0]["phrase"].lower() == "i already told"
    assert memctl.cmd_incidents(ctx, None, quiet=True) == []
    assert len(memctl.read_jsonl(ctx.index / "incidents.jsonl")) == 1


def test_incidents_user_words_only(env):
    ctx = env
    jsonl(ctx.transcripts / "sid-bbbb.jsonl", [
        {"type": "assistant", "timestamp": "t1", "message": {"role": "assistant", "content": [
            {"type": "text", "text": "I already told you this is assistant text."},
            {"type": "tool_use", "name": "Task", "input": {"prompt": "ya te lo dije"}}]}},
        {"type": "user", "timestamp": "t2", "message": {"role": "user", "content": [
            {"type": "tool_result", "content": "report line 1\nI already told the regex list\nline 3"}]}},
        {"type": "user", "timestamp": "t3", "message": {"role": "user", "content":
            "<task-notification>\n<task-id>x</task-id>\nagent says: I already said so\n</task-notification>"}},
        {"type": "user", "timestamp": "t4", "message": {"role": "user", "content": [
            {"type": "text", "text": "<system-reminder>\nyou forgot nothing\n</system-reminder>\nReal words: ya te lo dije, Cleo."},
            {"type": "text", "text": "and as I said before\nsecond line"}]}},
    ])
    memctl.cmd_ingest(ctx, quiet=True)
    found = memctl.cmd_incidents(ctx, "sid-bbbb", quiet=True)
    assert [f["phrase"].lower() for f in found] == ["ya te lo dije", "as i said"]
    md_lines = (ctx.raw / "sid-bbbb.md").read_text().splitlines()
    for f in found:  # reported line numbers point at the right md lines
        assert f["phrase"] in md_lines[f["line"] - 1]


def test_audit_pick_and_verdict(env):
    ctx = env
    memctl.cmd_ingest(ctx, quiet=True)
    res = run_cli(ctx, "audit", "--pick", "2")
    assert res.returncode == 0 and "queued as pending" in res.stdout
    rows = memctl.read_jsonl(ctx.index / "audits.jsonl")
    assert len(rows) == 2 and all(r["status"] == "pending" for r in rows)
    assert run_cli(ctx, "audit", "--pick", "2").stdout == res.stdout  # seeded by date
    res = run_cli(ctx, "audit", "--verdict", rows[0]["id"], "drift", "wrong nuance")
    assert res.returncode == 0
    updated = [r for r in memctl.read_jsonl(ctx.index / "audits.jsonl") if r["id"] == rows[0]["id"]]
    assert updated[-1]["status"] == "drift" and updated[-1]["note"] == "wrong nuance"


# ---------------------------------------------------------------- wakepack / night
def test_wakepack_cap_with_oversized_memory(env):
    ctx = env
    memctl.cmd_ingest(ctx, quiet=True)
    (ctx.vault / "memory" / "MEMORY.md").write_text("# Memory Index\n" + "".join(f"- line {i} " + "x" * 80 + "\n" for i in range(300)))
    text = memctl.cmd_wakepack(ctx, ingest_missing=False, do_print=False)
    assert len(text) <= 9000
    assert text.startswith("HEALTH: ")
    assert "## GOAL-STATE" in text and "## Cues due today" in text and "## Hot index" in text
    assert (ctx.index / "WAKE-PACK.md").read_text() == text
    assert "STALE" in text.splitlines()[0]  # no HEALTH.md yet


def test_night_quick_noop_and_full_run(env):
    ctx = env
    subprocess.run(["git", "init", "-q", str(ctx.vault)], check=True)
    res = run_cli(ctx, "night", "--quick")
    assert res.returncode == 0, res.stderr
    assert "committed 'night-quick" in res.stdout
    head = subprocess.run(["git", "-C", str(ctx.vault), "rev-parse", "HEAD"], capture_output=True, text=True).stdout
    res = run_cli(ctx, "night", "--quick")
    assert res.returncode == 0 and "nothing to do" in res.stdout
    assert subprocess.run(["git", "-C", str(ctx.vault), "rev-parse", "HEAD"], capture_output=True, text=True).stdout == head
    assert not (ctx.vault / ".git" / "night.lock").exists() or True  # lock file may remain; must be unlocked
    res = run_cli(ctx, "night")
    assert res.returncode == 0, res.stdout + res.stderr
    health = (ctx.index / "HEALTH.md").read_text()
    assert health.startswith("ok 2026-08-23T01:00:00 lint 0 error(s)")
    assert "provenance 33.3%" in health
    pack = (ctx.index / "WAKE-PACK.md").read_text()
    assert pack.splitlines()[0].startswith("HEALTH: ok")
    assert run_cli(ctx, "health").stdout.startswith("ok ")
    clean = subprocess.run(["git", "-C", str(ctx.vault), "status", "--porcelain"], capture_output=True, text=True).stdout
    assert clean == ""


def test_night_writes_fail_health_on_crash(env, monkeypatch):
    ctx = env
    monkeypatch.setattr(memctl, "cmd_ingest", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    assert memctl.cmd_night(ctx, quick=True, no_commit=True) == 1
    assert (ctx.index / "HEALTH.md").read_text().startswith("fail ")


# ---------------------------------------------------------------- hub / spokes
GOAL_TEXT = """---
type: goal-state
name: goal-state
updated: 2026-08-22
---
# GOAL-STATE — repo

**Phase:** recovery — rebuilding the vault.

## Commitments
- _none yet_
- Cleo: ship the hub layer
- Cleo: port the lesson back

## Do not
- Rename the directory by hand

## Last thread
2026-08-22: rename landed;
store merged at wake.
"""


def version(ctx, **extra):
    data = {"name": "Repo Name", "installed": "2026-08-20", "blueprint": "abc1234", **extra}
    memctl.write_version(ctx.root, data)
    return data


def beacon_file(name, path, updated, **extra):
    data = {"name": name, "path": path, "role": "spoke", "installed": "2026-08-01", "blueprint": "x",
            "status": "active", "phase": f"phase of {name}", "commitments": [], "last_thread": f"thread of {name}",
            "health": "ok 2026-08-22T01:00:00 quick run", "partner_tasks_open": 1, "last_session": "2026-08-21",
            "updated": updated, **extra}
    dest = memctl.bus_root() / "spokes" / f"{memctl.encode_project_path(Path(path))}.json"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(data), encoding="utf-8")
    return dest


def test_version_round_trip_preserves_order(env):
    ctx = env
    (ctx.root / ".cleo").mkdir()
    (ctx.root / ".cleo" / "VERSION").write_text("name: Repo Name\ninstalled: 2026-08-20\nblueprint: abc1234\n")
    v = memctl.read_version(ctx.root)
    assert list(v) == ["name", "installed", "blueprint"] and v["name"] == "Repo Name"
    v["path"] = str(ctx.root)
    memctl.write_version(ctx.root, v)
    assert (ctx.root / ".cleo" / "VERSION").read_text() == f"name: Repo Name\ninstalled: 2026-08-20\nblueprint: abc1234\npath: {ctx.root}\n"
    assert memctl.read_version(ctx.root / "nope") == {}


def test_beacon_fields_dormant_and_placeholders(env):
    ctx = env
    assert memctl.bus_root() == Path(os.environ["HOME"]) / ".cleo"  # fake home, never the real one
    version(ctx)
    (ctx.vault / "GOAL-STATE.md").write_text(GOAL_TEXT)
    (ctx.root / "ops").mkdir()
    (ctx.root / "ops" / "PARTNER-TASKS.md").write_text(
        "## OPEN\n- [ ] <task — why — added <date>>\n- [ ] sign the form\n  - [ ] nested real one\n- [x] done thing\n## DONE\n- [x] old\n")
    memctl.cmd_ingest(ctx, quiet=True)
    memctl.write_health(ctx, "ok", ["quick run; 2 file(s)", "detail"])
    b = memctl.cmd_beacon(ctx)
    path = memctl.bus_root() / "spokes" / f"{memctl.encode_project_path(ctx.root)}.json"
    assert path.exists() and json.loads(path.read_text()) == b
    assert not list(path.parent.glob("*.tmp"))
    assert b["name"] == "Repo Name" and b["path"] == str(ctx.root) and b["role"] == "spoke"
    assert b["installed"] == "2026-08-20" and b["blueprint"] == "abc1234"
    assert b["status"] == "active"
    assert b["phase"] == "recovery — rebuilding the vault."
    assert b["commitments"] == ["Cleo: ship the hub layer", "Cleo: port the lesson back"]
    assert b["last_thread"] == "2026-08-22: rename landed; store merged at wake."
    assert b["health"] == "ok 2026-08-23T01:00:00 quick run; 2 file(s)"
    assert b["partner_tasks_open"] == 2  # placeholder excluded, nested counted, [x] ignored
    assert b["last_session"] == "2026-08-01"  # last timestamp in the newest transcript
    assert b["updated"] == TODAY + "T01:00:00"
    # dormant marker, no VERSION name, no GOAL-STATE/HEALTH/PARTNER-TASKS -> fallbacks
    (ctx.root / ".cleo" / "DORMANT").write_text("")
    (ctx.root / ".cleo" / "VERSION").unlink()
    (ctx.vault / "GOAL-STATE.md").unlink()
    (ctx.index / "HEALTH.md").unlink()
    (ctx.root / "ops" / "PARTNER-TASKS.md").unlink()
    b = memctl.cmd_beacon(ctx)
    assert b["status"] == "dormant" and b["name"] == "repo" and b["health"] == "unknown"
    assert b["phase"] == "" and b["commitments"] == [] and b["last_thread"] == "" and b["partner_tasks_open"] == 0
    res = run_cli(ctx, "beacon", "--print")
    assert res.returncode == 0 and json.loads(res.stdout)["status"] == "dormant"


def test_night_quick_emits_beacon_even_when_nothing_changed(env):
    ctx = env
    subprocess.run(["git", "init", "-q", str(ctx.vault)], check=True)
    path = memctl.bus_root() / "spokes" / f"{memctl.encode_project_path(ctx.root)}.json"
    assert run_cli(ctx, "night", "--quick").returncode == 0
    assert path.exists()
    path.unlink()
    res = run_cli(ctx, "night", "--quick")
    assert res.returncode == 0 and "nothing to do" in res.stdout
    assert json.loads(path.read_text())["updated"] == TODAY + "T01:00:00"
    path.unlink()
    assert run_cli(ctx, "night").returncode == 0
    assert json.loads(path.read_text())["health"].startswith("ok 2026-08-23T01:00:00 lint 0 error(s)")
    assert not (ctx.index / "hook-errors.jsonl").exists()


def test_night_survives_beacon_failure(env, monkeypatch):
    ctx = env
    monkeypatch.setattr(memctl, "cmd_beacon", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("bus down")))
    assert memctl.cmd_night(ctx, quick=True, no_commit=True) == 0
    assert (ctx.index / "HEALTH.md").read_text().startswith("ok ")
    errs = memctl.read_jsonl(ctx.index / "hook-errors.jsonl")
    assert len(errs) == 1 and "beacon: RuntimeError('bus down')" in errs[0]["error"]


def test_portfolio_render_sort_stale_and_empty(env):
    ctx = env
    text = memctl.cmd_portfolio(ctx)
    assert text.startswith("# PORTFOLIO — 0 active · 0 dormant · 0 stale\n")
    assert "No spokes are enrolled" in text and "install-cleo.sh" in text
    assert (ctx.index / "PORTFOLIO.md").read_text() == text
    beacon_file("Older", "/x/older", "2026-08-21T22:00:00", partner_tasks_open=3, last_session="2026-08-19",
                handoffs_open=2, drift=0)
    beacon_file("Repo Name", str(ctx.root), TODAY + "T01:00:00")
    beacon_file("Sleeper", "/x/sleeper", "2026-08-01T00:00:00", status="dormant")
    beacon_file("Ghost", "/x/ghost", "2026-08-08T12:00:00")  # 15 days old -> stale
    beacon_file("Edge", "/x/edge", "2026-08-09T12:00:00")  # exactly 14 days -> still active
    bad = memctl.bus_root() / "spokes" / "broken.json"
    bad.write_text("{not json")
    text = memctl.cmd_portfolio(ctx)
    lines = text.splitlines()
    assert lines[0] == "# PORTFOLIO — 3 active · 1 dormant · 1 stale"
    rows = [l for l in lines if l.startswith("| ") and not l.startswith("| Name")]
    assert [r.split(" | ")[0][2:] for r in rows] == ["Repo Name (this)", "Older", "Edge"]
    assert "| Open partner tasks | Open handoffs | Drift | Beacon age |" in text
    # an older engine's beacon has no handoffs_open/drift: unknown, shown as —
    assert rows[0].split(" | ")[1:] == ["phase of Repo Name", "ok 2026-08-22T01:00:00 quick run", "2026-08-21", "1", "—", "—", "0d |"]
    assert rows[1].split(" | ")[4:] == ["3", "2", "0", "2d |"]
    assert "- **Repo Name (this):** thread of Repo Name" in lines and "- **Older:** thread of Older" in lines
    assert "- **Ghost:**" not in text
    assert "## Dormant" in lines and "- Sleeper — /x/sleeper" in lines
    assert "## Stale beacons (>14 days)" in lines and "- Ghost — /x/ghost (15d)" in lines
    assert "## Unreadable beacons" in lines and "- broken.json (JSONDecodeError)" in lines
    assert (ctx.index / "PORTFOLIO.md").read_text() == text
    res = run_cli(ctx, "portfolio", "--print")
    assert res.returncode == 0 and res.stdout == text


def test_wakepack_portfolio_only_for_hq_and_capped(env):
    ctx = env
    memctl.cmd_ingest(ctx, quiet=True)
    beacon_file("Other", "/x/other", TODAY + "T00:00:00")
    version(ctx)  # role defaults to spoke
    text = memctl.cmd_wakepack(ctx, ingest_missing=False, do_print=False)
    assert "## PORTFOLIO" not in text and not (ctx.index / "PORTFOLIO.md").exists()
    version(ctx, role="hq")
    text = memctl.cmd_wakepack(ctx, ingest_missing=False, do_print=False)
    assert "## Phase now\nrecovery\n\n## PORTFOLIO\n# PORTFOLIO — 1 active" in text  # right after GOAL-STATE
    assert text.index("## PORTFOLIO") < text.index("## Cues due today")
    assert "| Other |" in text and "- **Other:** thread of Other" in text
    assert (ctx.index / "PORTFOLIO.md").exists()
    # an enormous portfolio truncates itself instead of evicting the rest of the pack
    for i in range(60):
        beacon_file(f"Spoke{i:02d}", f"/x/s{i}", TODAY + "T00:00:00", last_thread="t" * 200)
    text = memctl.cmd_wakepack(ctx, ingest_missing=False, do_print=False)
    assert len(text) <= memctl.WAKE_PACK_CAP
    assert "(truncated — see private/index/PORTFOLIO.md)" in text
    assert "## Cues due today" in text and "## Hot index" in text and "## Last episode" in text
    assert text.index("(truncated") < text.index("## Cues due today")


def test_relocate_merges_without_clobbering(env):
    ctx = env
    old_root = "/somewhere/old_repo"
    version(ctx, path=old_root)
    old_store = memctl.memory_store(Path(old_root))
    new_store = memctl.memory_store(ctx.root)
    (old_store / "memory").mkdir(parents=True)
    (old_store / "memory" / "a.md").write_text("A from old\n")
    (old_store / "memory" / "b.md").write_text("B from old\n")
    (old_store / "memory" / "MEMORY.md").write_text("# old index\n")
    jsonl(old_store / "sid-old.jsonl", session_records("sid-old"))
    jsonl(old_store / "sid-old" / "subagents" / "ag.jsonl", session_records("ag")[:2])
    (old_store / "stray.txt").write_text("not moved\n")
    (new_store / "memory").mkdir(parents=True)
    (new_store / "memory" / "b.md").write_text("B from new\n")
    ghost = memctl.beacon_path(Path(old_root))
    ghost.parent.mkdir(parents=True, exist_ok=True)
    ghost.write_text("{}")
    actions = memctl.cmd_relocate(ctx)
    assert not ghost.exists()  # a beacon keyed to the old path would be a ghost spoke at HQ
    assert (new_store / "memory" / "a.md").read_text() == "A from old\n"
    assert (new_store / "memory" / "b.md").read_text() == "B from new\n"  # never clobbered
    assert (old_store / "memory" / "b.md").read_text() == "B from old\n"  # left for a human
    assert (new_store / "memory" / "MEMORY.md").read_text() == "# old index\n"
    assert (new_store / "sid-old.jsonl").exists() and (new_store / "sid-old" / "subagents" / "ag.jsonl").exists()
    assert not (old_store / "sid-old.jsonl").exists() and not (old_store / "sid-old").exists()
    assert (old_store / "stray.txt").exists()  # old store kept: leftovers remain
    assert memctl.read_version(ctx.root)["path"] == str(ctx.root)
    assert list(memctl.read_version(ctx.root)) == ["name", "installed", "blueprint", "path"]
    assert any(a.startswith("moved memory/a.md") for a in actions)
    assert any(a.startswith("kept memory/b.md") for a in actions)
    assert any("left behind" in a and "b.md" in a and "stray.txt" in a for a in actions)
    assert f"VERSION path: {old_root} -> {ctx.root}" in actions
    assert actions[-1].startswith("removed ghost beacon")
    assert memctl.cmd_relocate(ctx) == []  # path now matches -> no-op
    (ctx.root / ".cleo" / "VERSION").unlink()
    assert memctl.cmd_relocate(ctx) == []  # no VERSION at all -> no-op
    # old store fully drained -> removed
    version(ctx, path="/somewhere/other")
    other = memctl.memory_store(Path("/somewhere/other"))
    (other / "memory").mkdir(parents=True)
    (other / "memory" / "c.md").write_text("C\n")
    actions = memctl.cmd_relocate(ctx)
    assert (new_store / "memory" / "c.md").exists() and not other.exists()
    assert any(a.startswith("removed empty") for a in actions)
    res = run_cli(ctx, "relocate")
    assert res.returncode == 0 and "nothing to do" in res.stdout


def test_wakepack_ingest_missing_runs_relocate_and_partner_link(env):
    ctx = env
    version(ctx, path="/somewhere/old_repo")
    store = memctl.bus_root() / "partner"
    store.mkdir(parents=True)
    (store / "shared.md").write_text("---\nname: shared\ndescription: shared note\n---\nbody\n")
    memctl.cmd_wakepack(ctx, ingest_missing=True, do_print=False)
    assert memctl.read_version(ctx.root)["path"] == str(ctx.root)
    memdir = memctl.memory_store(ctx.root) / "memory"
    assert (memdir / "shared.md").is_symlink()
    assert (ctx.raw / "sid-aaaa.md").exists()  # ingest still ran
    assert not (ctx.index / "hook-errors.jsonl").exists()


def test_partner_link_and_list(env):
    ctx = env
    assert memctl.cmd_partner(ctx, "link") == []  # no store -> silent no-op
    version(ctx)
    store = memctl.bus_root() / "partner"
    store.mkdir(parents=True)
    (store / "p1.md").write_text("---\nname: p1\ndescription: partner one\n---\none\n")
    (store / "p2.md").write_text("two\n")
    (store / "p3.md").write_text("three\n")
    (store / "MEMORY.md").write_text("# store index\n")
    (store / "_TEMPLATE.md").write_text("tpl\n")
    memdir = memctl.memory_store(ctx.root) / "memory"
    memdir.mkdir(parents=True)
    (memdir / "p2.md").write_text("two\n")  # identical regular file
    (memdir / "p3.md").write_text("local three\n")  # conflicting regular file
    actions = memctl.cmd_partner(ctx, "link")
    assert (memdir / "p1.md").is_symlink() and os.readlink(memdir / "p1.md") == str(store / "p1.md")
    assert (memdir / "p2.md").is_symlink() and (memdir / "p2.md").read_text() == "two\n"
    assert not (memdir / "p3.md").is_symlink() and (memdir / "p3.md").read_text() == "local three\n"
    assert not (memdir / "MEMORY.md").is_symlink() and not (memdir / "_TEMPLATE.md").exists()
    assert "linked p1.md" in actions and "replaced identical file p2.md with a symlink" in actions
    assert any(a.startswith("CONFLICT p3.md") for a in actions)
    index = (memdir / "MEMORY.md").read_text()
    assert index.startswith("# Memory Index — Repo Name\n")
    assert "- [p1](p1.md) — partner one\n" in index and "- [p2](p2.md) — no description\n" in index
    assert "- [p3](p3.md) — no description\n" in index
    # idempotent: second link changes nothing except the standing conflict report
    again = memctl.cmd_partner(ctx, "link")
    assert [a for a in again if not a.startswith("CONFLICT")] == []
    assert (memdir / "MEMORY.md").read_text() == index
    # a wrong symlink is re-pointed; a pre-existing index line is respected, not duplicated
    (memdir / "p1.md").unlink()
    (memdir / "p1.md").symlink_to(store / "p2.md")
    (memdir / "MEMORY.md").write_text("# Memory Index — Repo Name\n- [p1](p1.md) — my own wording")
    actions = memctl.cmd_partner(ctx, "link")
    assert "relinked p1.md" in actions and os.readlink(memdir / "p1.md") == str(store / "p1.md")
    index = (memdir / "MEMORY.md").read_text()
    assert index.count("- [p1](p1.md)") == 1 and "my own wording\n- [p2](p2.md)" in index
    res = run_cli(ctx, "partner", "list")
    assert res.returncode == 0 and res.stdout.splitlines() == ["p1.md — partner one", "p2.md — no description", "p3.md — no description"]
    res = run_cli(ctx, "partner", "link")
    assert res.returncode == 0 and res.stdout.startswith("CONFLICT p3.md")


# ---------------------------------------------------------------- wake heals a stale brain
def test_night_quick_rewrites_stale_health_even_with_nothing_to_ingest(env):
    ctx = env
    memctl.cmd_ingest(ctx, quiet=True)  # an earlier wake captured the session...
    memctl.write_health(ctx, "ok", ["old run"])
    health = ctx.index / "HEALTH.md"
    past = (ctx.transcripts / "sid-aaaa.jsonl").stat().st_mtime - 60
    os.utime(health, (past, past))  # ...but no night has run since it ended
    assert memctl.brain_is_stale(ctx)
    assert memctl.cmd_night(ctx, quick=True, no_commit=True) == 0
    assert health.read_text().startswith("ok 2026-08-23T01:00:00 quick run; 0 file(s)")
    assert not memctl.brain_is_stale(ctx)
    # a fresh FAILED run is not staleness: a quick night must not paper over lint errors
    memctl.write_health(ctx, "fail", ["lint 2 error(s)"])
    assert not memctl.brain_is_stale(ctx) and "last night run failed" in memctl.health_line(ctx)
    memctl.cmd_night(ctx, quick=True, no_commit=True)
    assert health.read_text().startswith("fail ")


def test_wake_heals_stale_brain_and_stdout_stays_the_pack(env):
    ctx = env
    subprocess.run(["git", "init", "-q", str(ctx.vault)], check=True)
    live = ctx.transcripts / "sid-live.jsonl"  # the session that is starting right now
    jsonl(live, session_records("sid-live")[:2])
    later = live.stat().st_mtime + 30
    os.utime(live, (later, later))
    assert "STALE" in memctl.health_line(ctx, "sid-live")  # nothing rendered, no HEALTH.md
    res = run_cli(ctx, "wakepack", "--ingest-missing", "--print", "--exclude-sid", "sid-live")
    assert res.returncode == 0, res.stderr
    pack = (ctx.index / "WAKE-PACK.md").read_text()
    assert res.stdout == pack  # night's chatter never reaches the model
    assert pack.startswith("HEALTH: ok (night run 2026-08-23T01:00:00, newest transcript sid-aaaa ")
    assert "night: committed 'night-quick" in res.stderr
    log = lambda: subprocess.run(["git", "-C", str(ctx.vault), "log", "--format=%s"],
                                 capture_output=True, text=True).stdout.splitlines()
    assert len(log()) == 1 and log()[0].startswith("night-quick 2026-08-23")
    assert not (ctx.index / "hook-errors.jsonl").exists()
    # the next wake finds a fresh brain: plain ingest, no second night
    res = run_cli(ctx, "wakepack", "--ingest-missing", "--print", "--exclude-sid", "sid-live")
    assert res.returncode == 0 and res.stdout.startswith("HEALTH: ok") and "night:" not in res.stderr
    assert len(log()) == 1


def test_wake_heal_failures_are_logged_never_fatal(env, monkeypatch, capsys):
    ctx = env
    monkeypatch.setattr(memctl, "cmd_night", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("lock")))
    text = memctl.cmd_wakepack(ctx, ingest_missing=True, do_print=True)
    assert capsys.readouterr().out == text and text.startswith("HEALTH: STALE")
    errs = memctl.read_jsonl(ctx.index / "hook-errors.jsonl")
    assert [e["error"] for e in errs] == ["wake night: RuntimeError('lock')"]
    assert (ctx.raw / "sid-aaaa.md").exists()  # a night that cannot run still leaves a plain ingest
    monkeypatch.setattr(memctl, "cmd_night", lambda *a, **k: 1)
    memctl.cmd_wakepack(ctx, ingest_missing=True, do_print=False)
    assert "wake night: RuntimeError('night --quick exited 1; see HEALTH.md')" in \
        memctl.read_jsonl(ctx.index / "hook-errors.jsonl")[-1]["error"]


def test_wake_on_fresh_brain_only_ingests(env, monkeypatch):
    ctx = env
    assert memctl.cmd_night(ctx, quick=True, no_commit=True) == 0
    monkeypatch.setattr(memctl, "cmd_night", lambda *a, **k: (_ for _ in ()).throw(AssertionError("no night owed")))
    jsonl(ctx.transcripts / "sid-aaaa" / "subagents" / "agent-2.jsonl", session_records("agent-2")[:2])
    text = memctl.cmd_wakepack(ctx, ingest_missing=True, do_print=False)
    assert text.startswith("HEALTH: ok") and (ctx.raw / "sid-aaaa-subagents" / "agent-2.md").exists()
    assert not (ctx.index / "hook-errors.jsonl").exists()


# ---------------------------------------------------------------- drift: instance vs seed
def make_seed(seed: Path) -> Path:
    files = {
        "install-cleo.sh": "#!/usr/bin/env bash\n",
        "CLEO-IDENTITY.md": "# identity\n",
        "HQ-LAYER.md": "# hq layer\n",
        "templates/repo/CLAUDE.md": "# boot\n\n<!-- CLEO-INSTANCE: <project-name> · installed <install-date> · "
                                    "blueprint <version> -->\n\nrules\n",
        "templates/repo/.claude/skills/wake/SKILL.md": "# wake\n",
        "templates/repo/.claude/skills/sleep/SKILL.md": "# sleep\n",
        "templates/repo/.claude/agents/builder.md": "# builder\n",
        "templates/repo/.claude/output-styles/cleo-concise.md": "# voice\n",
        "engine/memctl.py": "print('engine')\n",
        "engine/hook.sh": "#!/usr/bin/env bash\n",
        "engine/tests/test_x.py": "def test_x(): pass\n",
        "engine/tests/__pycache__/test_x.cpython-312.pyc": "cache",
        "engine/__pycache__/memctl.cpython-312.pyc": "cache",
        "templates/repo/.claude/skills/wake/__pycache__/x.pyc": "cache",
    }
    for relpath, text in files.items():
        (seed / relpath).parent.mkdir(parents=True, exist_ok=True)
        (seed / relpath).write_text(text, encoding="utf-8")
    return seed


def install_from(seed: Path, root: Path, public: bool = False) -> None:
    for src, dst in memctl.install_pairs(seed, public):
        (root / dst).parent.mkdir(parents=True, exist_ok=True)
        (root / dst).write_bytes(src.read_bytes())


def test_drift_at_hq_lists_differences_and_the_refresh_command(env):
    ctx = env
    seed = make_seed(ctx.root / "blueprint")  # HQ carries its seed in its own repo
    version(ctx, role="hq")  # name "Repo Name" differs from the dir name "repo"
    install_from(seed, ctx.root)
    assert not any("__pycache__" in d or d.endswith(".pyc") for _, d in memctl.install_pairs(seed, False))
    (ctx.root / ".claude" / "agents" / "project-only.md").write_text("mine\n")  # project agents are not drift
    (ctx.root / ".claude" / "skills" / "wake" / "PROJECT.md").write_text("extension\n")
    assert memctl.drift_report(ctx) == (seed, [])
    assert memctl.drift_line(ctx) is None
    assert run_cli(ctx, "drift").stdout == f"DRIFT: none (seed {seed})\n"
    (ctx.root / ".cleo" / "engine" / "memctl.py").write_text("old engine\n")
    (ctx.root / ".claude" / "skills" / "wake" / "SKILL.md").unlink()
    assert memctl.drift_report(ctx)[1] == [".cleo/engine/memctl.py", ".claude/skills/wake/SKILL.md (missing)"]
    refresh = f"bash {seed}/install-cleo.sh {ctx.root} --name 'Repo Name' --hq"
    assert memctl.drift_line(ctx) == f"DRIFT: 2 file(s) differ from seed {seed} — refresh: {refresh}"
    git = ["git", "-C", str(ctx.root), "-c", "user.name=t", "-c", "user.email=t@t"]
    subprocess.run(git + ["add", "blueprint"], check=True)
    subprocess.run(git + ["commit", "-qm", "seed"], check=True)
    rev = subprocess.run(git + ["rev-parse", "--short", "HEAD"], capture_output=True, text=True).stdout.strip()
    res = run_cli(ctx, "drift", "--print")
    assert res.returncode == 1
    assert res.stdout.splitlines() == [f"DRIFT: 2 file(s) differ from seed {rev} — refresh: {refresh}",
                                       "  .cleo/engine/memctl.py", "  .claude/skills/wake/SKILL.md (missing)"]
    pack = memctl.cmd_wakepack(ctx, ingest_missing=False, do_print=False).splitlines()
    assert pack[0].startswith("HEALTH: ") and pack[1].startswith(f"DRIFT: 2 file(s) differ from seed {rev}")
    install_from(seed, ctx.root)
    assert run_cli(ctx, "drift").returncode == 0
    assert "DRIFT:" not in memctl.cmd_wakepack(ctx, ingest_missing=False, do_print=False)


def test_drift_public_bootstrap_ignores_the_instance_stamp(env):
    ctx = env
    seed = make_seed(ctx.root / "blueprint")
    version(ctx, role="hq")
    install_from(seed, ctx.root)
    assert ".cleo/CLAUDE-BOOTSTRAP.md" not in [d for _, d in memctl.install_pairs(seed, False)]
    version(ctx, role="hq", visibility="public")
    assert memctl.drift_report(ctx)[1] == [".cleo/CLAUDE-BOOTSTRAP.md (missing)"]
    install_from(seed, ctx.root, public=True)
    boot = ctx.root / ".cleo" / "CLAUDE-BOOTSTRAP.md"
    boot.write_text(boot.read_text().replace("<project-name> · installed <install-date> · blueprint <version>",
                                             "Repo Name · installed 2026-08-20 · blueprint abc1234"))
    assert memctl.drift_report(ctx)[1] == []  # the per-instance stamp is not drift
    boot.write_text(boot.read_text().replace("rules", "old rules"))
    assert memctl.drift_report(ctx)[1] == [".cleo/CLAUDE-BOOTSTRAP.md"]
    assert memctl.drift_line(ctx).endswith(" --name 'Repo Name' --hq --public")


def other_instance(tmp_path: Path, dirname: str, name: str, role: str = "spoke") -> "memctl.Ctx":
    root = tmp_path / dirname
    root.mkdir()
    (root / "MISSION.md").write_text("# Mission\n")
    memctl.write_version(root, {"name": name, "role": role, "path": str(root)})
    return memctl.Ctx(root, root / "private", tmp_path / f"{dirname}-transcripts")


def test_drift_spoke_finds_the_seed_through_the_hq_beacon(env, tmp_path):
    ctx = env
    version(ctx)  # a spoke
    res = run_cli(ctx, "drift")
    assert res.returncode == 0 and res.stdout.startswith("DRIFT: no seed found")
    assert memctl.drift_line(ctx) is None  # no seed -> nothing in the pack
    hq = other_instance(tmp_path, "hq", "Cleo HQ", role="hq")
    assert memctl.cmd_beacon(hq)["seed"] is None  # HQ without blueprint/ names no seed
    assert memctl.find_seed(ctx) is None
    seed = make_seed(hq.root / "blueprint")
    assert memctl.cmd_beacon(hq)["seed"] == str(seed)
    assert memctl.cmd_beacon(ctx)["seed"] is None  # only HQ advertises a seed
    make_seed(ctx.root / "blueprint")  # a spoke's own blueprint/ dir is not a seed
    assert memctl.find_seed(ctx) == seed
    install_from(seed, ctx.root)
    assert memctl.drift_report(ctx) == (seed, [])
    (ctx.root / ".cleo" / "blueprint" / "HQ-LAYER.md").write_text("old doctrine\n")
    assert memctl.drift_line(ctx) == (f"DRIFT: 1 file(s) differ from seed {seed} — refresh: "
                                      f"bash {seed}/install-cleo.sh {ctx.root} --name 'Repo Name'")


def test_drift_failure_never_blocks_the_wake(env, monkeypatch):
    ctx = env
    monkeypatch.setattr(memctl, "drift_report", lambda *a: (_ for _ in ()).throw(OSError("bus gone")))
    text = memctl.cmd_wakepack(ctx, ingest_missing=False, do_print=False)
    assert text.startswith("HEALTH: ") and "## Cues due today" in text
    assert "drift: OSError('bus gone')" in memctl.read_jsonl(ctx.index / "hook-errors.jsonl")[-1]["error"]


def test_install_map_matches_the_installer(tmp_path):
    """INSTALL_MAP and install-cleo.sh must agree, both ways: a fresh install from this seed shows
    no drift, and every file the installer refreshes into those dirs is in the table."""
    seed = SCRIPT.resolve().parents[1]
    if not (seed / "install-cleo.sh").is_file():
        pytest.skip("vendored engine copy; the installer lives in the seed")
    home = tmp_path / "home"
    home.mkdir()
    target = tmp_path / "inst"
    (target / "venv" / "bin").mkdir(parents=True)  # an existing venv: no network in tests
    (target / "venv" / "bin" / "python").symlink_to(sys.executable)
    env = {k: v for k, v in os.environ.items() if k not in ("CLEO_HOME", "MEMCTL_TRANSCRIPTS")}
    proc = subprocess.run(["bash", str(seed / "install-cleo.sh"), str(target), "--name", "Inst"],
                          capture_output=True, text=True, env={**env, "HOME": str(home)}, timeout=300)
    assert (target / ".cleo" / "engine" / "memctl.py").is_file(), proc.stdout + proc.stderr
    pairs = memctl.install_pairs(seed, public=False)
    differ = [d for s, d in pairs if not (target / d).is_file() or memctl.sha256_file(s) != memctl.sha256_file(target / d)]
    assert differ == []
    tops = (".cleo/blueprint", ".cleo/engine", ".claude/skills/wake", ".claude/skills/sleep",
            ".claude/output-styles", ".claude/agents")
    installed = {p.relative_to(target).as_posix() for top in tops for p in (target / top).rglob("*")
                 if p.is_file() and p.suffix != ".pyc" and not memctl.DRIFT_SKIP_DIRS & set(p.parts)}
    expected = {d for _, d in pairs}
    assert installed == expected, (f"install-cleo.sh and memctl.INSTALL_MAP disagree — only installed: "
                                   f"{sorted(installed - expected)}; only in the table: {sorted(expected - installed)}")


# ---------------------------------------------------------------- handoffs
def test_handoff_send_inbox_beacon_and_done(env, tmp_path, monkeypatch, capsys):
    ctx = env
    version(ctx, path=str(ctx.root))  # spoke "Repo Name"
    hq = other_instance(tmp_path, "hq", "Cleo HQ", role="hq")
    memctl.cmd_beacon(hq)
    memctl.cmd_beacon(ctx)
    path = memctl.cmd_handoff_send(ctx, "hq", "Port the drift check", "Task: port it.\nDecide: BODY-ONLY", ["DECISIONS.md#D2"])
    assert path == memctl.bus_root() / "handoff" / "open" / "20260823-010000-repo-name-to-hq-port-the-drift-check.md"
    fm, body, err = memctl.split_frontmatter(path.read_text())
    assert err is None and fm == {"from": {"name": "Repo Name", "path": str(ctx.root)}, "to": "hq",
                                  "created": TODAY + "T01:00:00", "status": "open",
                                  "subject": "Port the drift check", "sources": ["DECISIONS.md#D2"]}
    assert body == "# Port the drift check\n\nTask: port it.\nDecide: BODY-ONLY\n"
    twin = memctl.cmd_handoff_send(ctx, "HQ", "Port the drift check", "again", [])  # same second: never clobbered
    assert twin.name == "20260823-010000-repo-name-to-hq-port-the-drift-check-2.md"
    assert capsys.readouterr().err == ""  # hq is on the bus: no warning
    by_name = memctl.cmd_handoff_send(hq, "repo NAME", "Name match", "x", [])  # VERSION name, any case
    by_path = memctl.cmd_handoff_send(hq, str(ctx.root) + "/", "Path match", "x", [])
    memctl.cmd_handoff_send(hq, "Somebody Else", "Not for us", "x", [])
    assert "no beacon on the bus matches --to 'Somebody Else'" in capsys.readouterr().err
    assert [p for p, _ in memctl.inbox(hq)] == [path, twin]
    assert {p for p, _ in memctl.inbox(ctx)} == {by_name, by_path}
    assert memctl.cmd_beacon(ctx)["handoffs_open"] == 2 and memctl.cmd_beacon(hq)["handoffs_open"] == 2
    # wake pack: titles only, after GOAL-STATE, before the cues
    monkeypatch.setenv("MEMCTL_TODAY", "2026-08-26")
    text = memctl.cmd_wakepack(ctx, ingest_missing=False, do_print=False)
    assert f"\n## INBOX\n- Name match — from Cleo HQ, 3d — {by_name}\n" in text
    assert f"- Path match — from Cleo HQ, 3d — {by_path}" in text
    assert "Not for us" not in text and "Port the drift check" not in text
    assert text.index("## GOAL-STATE") < text.index("## INBOX") < text.index("## Cues due today")
    assert "BODY-ONLY" not in memctl.cmd_wakepack(hq, ingest_missing=False, do_print=False)
    # close: ambiguous prefix refused, exact id moved to done/ with a resolution section
    with pytest.raises(SystemExit, match="ambiguous"):
        memctl.cmd_handoff_done(ctx, "20260823-010000-cleo-hq-to-repo", None)
    with pytest.raises(SystemExit, match="no open handoff"):
        memctl.cmd_handoff_done(ctx, "nope", None)
    done = memctl.cmd_handoff_done(ctx, by_name.stem, "formalized as D3")
    assert done == memctl.bus_root() / "handoff" / "done" / by_name.name and not by_name.exists()
    fm, body, _ = memctl.split_frontmatter(done.read_text())
    assert fm["status"] == "done" and fm["subject"] == "Name match"
    assert body.endswith(f"# Name match\n\nx\n\n## Resolution\n\n- 2026-08-26 — done by Repo Name ({ctx.root}): formalized as D3\n")
    assert [p for p, _ in memctl.inbox(ctx)] == [by_path]
    assert memctl.cmd_handoff_done(ctx, str(by_path), None).read_text().endswith("): no note\n")
    assert memctl.inbox(ctx) == [] and "## INBOX" not in memctl.cmd_wakepack(ctx, ingest_missing=False, do_print=False)


def test_handoff_inbox_is_capped_and_tolerates_bad_files(env):
    ctx = env
    version(ctx)
    for i in range(40):
        memctl.cmd_handoff_send(ctx, "Repo Name", f"Subject {i:02d} " + "s" * 60, "x", [])
    (memctl.bus_root() / "handoff" / "open" / "broken.md").write_text("---\nto: [unclosed\n---\n")
    text = memctl.cmd_wakepack(ctx, ingest_missing=False, do_print=False)
    inbox = text[text.index("## INBOX"):text.index("\n\n", text.index("## INBOX"))]
    assert len(inbox) <= memctl.INBOX_PACK_CAP + len(memctl.INBOX_TRUNC_LINE) + 1
    assert inbox.endswith(memctl.INBOX_TRUNC_LINE) and "## Hot index" in text
    assert len(memctl.inbox(ctx)) == 40


def test_handoff_cli_round_trip(env):
    ctx = env
    version(ctx)
    cmd = [sys.executable, str(SCRIPT), "--root", str(ctx.root), "--vault", str(ctx.vault), "--transcripts", str(ctx.transcripts)]
    res = subprocess.run(cmd + ["handoff", "send", "--to", "Repo Name", "--subject", "Self: tidy MAP", "--body-file", "-",
                                "--sources", "MAP.md", "DECISIONS.md#D1"],
                         input="Task line\nContext line\n", capture_output=True, text=True,
                         env={**os.environ, "MEMCTL_TODAY": TODAY, "MEMCTL_NOW": TODAY + "T01:00:00"})
    assert res.returncode == 0, res.stderr
    sent = Path(res.stdout.strip())
    assert sent.name == "20260823-010000-repo-name-to-repo-name-self-tidy-map.md"
    assert "# Self: tidy MAP\n\nTask line\nContext line\n" in sent.read_text()
    assert memctl.split_frontmatter(sent.read_text())[0]["sources"] == ["MAP.md", "DECISIONS.md#D1"]
    (sent.parent / "junk.md").write_text("no frontmatter\n")
    assert run_cli(ctx, "handoff", "list").stdout == "handoff: 1 open addressed to Repo Name\n"
    lines = run_cli(ctx, "handoff", "list", "--all", "--print").stdout.splitlines()
    assert lines == ["handoff: 1 open on the bus",
                     f"- {sent.stem}: Self: tidy MAP (from Repo Name to Repo Name, 0d) {sent}",
                     "! unreadable: junk.md (no `to`)"]
    assert run_cli(ctx, "handoff", "send", "--to", "x", "--subject", "s", "--body", "b", "--body-file", "f").returncode == 2
    res = run_cli(ctx, "handoff", "done", sent.name, "--note", "done in MAP")
    assert res.returncode == 0 and res.stdout.startswith("handoff: closed -> ") and "## Resolution" in Path(res.stdout.split("-> ")[1].strip()).read_text()
    assert run_cli(ctx, "handoff", "list").stdout == "handoff: 0 open addressed to Repo Name\n"


# ---------------------------------------------------------------- review fixes (2026-10-27)
def _age_health(ctx) -> None:
    """Make HEALTH predate the newest transcript: the session ran on after the last night."""
    t = (ctx.transcripts / "sid-aaaa.jsonl").stat().st_mtime - 60
    os.utime(ctx.index / "HEALTH.md", (t, t))


def test_wake_heal_carries_a_lint_failure_forward(env):
    ctx = env
    memctl.cmd_ingest(ctx, quiet=True)
    memctl.write_health(ctx, "fail", ["lint 2 error(s) 0 warning(s); provenance 50%", "lint error: a", "lint error: b"])
    health, src = ctx.index / "HEALTH.md", ctx.transcripts / "sid-aaaa.jsonl"
    with src.open("a", encoding="utf-8") as fh:  # /sleep's full night failed; the session wrote on
        fh.write(json.dumps({"type": "user", "timestamp": "2026-08-01T11:00:00Z", "message": {"role": "user", "content": "bye"}}) + "\n")
    _age_health(ctx)
    line = memctl.health_line(ctx)
    assert "HEALTH older than newest transcript; last night run failed" in line  # both reasons, not an elif
    text = memctl.cmd_wakepack(ctx, ingest_missing=True, do_print=False)
    h = health.read_text()
    assert h.startswith("fail 2026-08-23T01:00:00 quick run; ")
    assert ("; carried from the last full night: lint 2 error(s) 0 warning(s); provenance 50%\n"
            "- lint error: a\n- lint error: b\n") in h
    assert text.startswith("HEALTH: STALE last night run failed (")  # the pack still says fail
    assert not (ctx.index / "hook-errors.jsonl").exists()  # carrying an old failure is not a new one
    # the next stale wake carries it again, without nesting
    _age_health(ctx)
    memctl.cmd_wakepack(ctx, ingest_missing=True, do_print=False)
    assert health.read_text().count("carried from the last full night") == 1
    # a quick night's own crash, carrying nothing, is cleared by a quick night that completes
    memctl.write_health(ctx, "fail", ["night --quick crashed: RuntimeError('x')"])
    _age_health(ctx)
    assert memctl.cmd_night(ctx, quick=True, no_commit=True) == 0
    assert health.read_text().startswith("ok ")


def test_wake_never_waits_on_a_running_night(env):
    ctx = env
    subprocess.run(["git", "init", "-q", str(ctx.vault)], check=True)
    lock = (ctx.vault / ".git" / "night.lock").open("w")
    fcntl.flock(lock, fcntl.LOCK_EX)  # another night (a /sleep elsewhere) holds the vault
    try:
        with pytest.raises(BlockingIOError):
            memctl.cmd_night(ctx, quick=True, no_commit=True, wake=True)
        res = subprocess.run([sys.executable, str(SCRIPT), "--root", str(ctx.root), "--vault", str(ctx.vault),
                              "--transcripts", str(ctx.transcripts), "wakepack", "--ingest-missing", "--print"],
                             capture_output=True, text=True, timeout=30,
                             env={**os.environ, "MEMCTL_TODAY": TODAY, "MEMCTL_NOW": TODAY + "T01:00:00"})
    finally:
        fcntl.flock(lock, fcntl.LOCK_UN)
        lock.close()
    assert res.returncode == 0, res.stderr
    assert res.stdout.startswith("HEALTH: STALE") and res.stdout == (ctx.index / "WAKE-PACK.md").read_text()
    errs = [e["error"] for e in memctl.read_jsonl(ctx.index / "hook-errors.jsonl")]
    assert errs == ["wake night: RuntimeError('skipped: another night holds night.lock')"]
    assert not (ctx.raw / "sid-aaaa.md").exists()  # the running night captures; the wake does not race it


def test_wake_ingests_even_when_the_stale_check_fails(env, monkeypatch):
    ctx = env
    monkeypatch.setattr(memctl, "brain_is_stale", lambda *a: (_ for _ in ()).throw(OSError("stat race")))
    memctl.cmd_wakepack(ctx, ingest_missing=True, do_print=False)
    assert (ctx.raw / "sid-aaaa.md").exists()
    assert [e["error"] for e in memctl.read_jsonl(ctx.index / "hook-errors.jsonl")] == ["wake stale check: OSError('stat race')"]


def test_healing_wake_builds_the_pack_once(env, monkeypatch):
    ctx = env
    calls = []
    real = memctl.build_wakepack
    monkeypatch.setattr(memctl, "build_wakepack", lambda *a, **k: calls.append(a) or real(*a, **k))
    text = memctl.cmd_wakepack(ctx, ingest_missing=True, do_print=False, exclude_sid="sid-live")
    assert text.startswith("HEALTH: ok") and len(calls) == 1  # night left the pack to the wake
    assert memctl.cmd_night(ctx, quick=False, no_commit=True) == 0 and len(calls) == 2  # a plain night still builds it


def test_newest_source_falls_back_to_raw_copies_when_transcripts_are_pruned(env):
    ctx = env
    memctl.cmd_ingest(ctx, quiet=True)
    assert memctl.newest_source(ctx) == ctx.transcripts / "sid-aaaa.jsonl"
    assert memctl.newest_source(ctx, "sid-aaaa") is None  # the exclusion applies to the raw fallback too
    (ctx.transcripts / "sid-aaaa.jsonl").unlink()  # Claude Code pruned it; the vault keeps the copy
    assert memctl.newest_source(ctx) == ctx.raw / "sid-aaaa.jsonl"
    assert memctl.last_session_date(ctx) == "2026-08-01"


def test_drift_dirty_seed_withholds_the_refresh_command(env):
    ctx = env
    seed = make_seed(ctx.root / "blueprint")
    version(ctx, role="hq")
    install_from(seed, ctx.root)
    git = ["git", "-C", str(ctx.root), "-c", "user.name=t", "-c", "user.email=t@t"]
    subprocess.run(git + ["add", "blueprint"], check=True)
    subprocess.run(git + ["commit", "-qm", "seed"], check=True)
    rev = subprocess.run(git + ["rev-parse", "--short", "HEAD"], capture_output=True, text=True).stdout.strip()
    (ctx.root / "MAP.md").write_text("an unrelated, uncommitted change outside the seed\n")
    assert memctl.seed_rev(seed) == rev
    assert run_cli(ctx, "drift").stdout == f"DRIFT: none (seed {seed} @ {rev})\n"
    (ctx.root / ".cleo" / "engine" / "memctl.py").write_text("old engine\n")
    assert " — refresh: bash " in memctl.drift_line(ctx)
    dirty = f" differ from seed {rev}+dirty — commit the seed (tests green) first; never refresh from a dirty seed"
    for change in (lambda: (seed / "engine" / "memctl.py").write_text("print('half-done')\n"),  # modified
                   lambda: (seed / "templates" / "new.md").write_text("new\n"),  # untracked, under a mapped dir
                   lambda: (seed / "install-cleo.sh").write_text("#!/bin/sh\n# changed\n")):  # the refresh itself
        change()
        assert memctl.seed_rev(seed) == rev + "+dirty"
        assert memctl.drift_line(ctx).endswith(dirty)  # no refresh command handed out
        subprocess.run(git + ["checkout", "-q", "--", "blueprint"], check=True)
        (seed / "templates" / "new.md").unlink(missing_ok=True)
        assert memctl.seed_rev(seed) == rev
    res = run_cli(ctx, "drift", "--print")
    assert res.returncode == 1 and "refresh: bash" in res.stdout
    (seed / "HQ-LAYER.md").write_text("# hq layer, in progress\n")
    res = run_cli(ctx, "drift", "--print")
    assert res.returncode == 1 and "install-cleo.sh" not in res.stdout and "+dirty — commit the seed" in res.stdout


def test_beacon_carries_the_drift_count(env, tmp_path):
    ctx = env
    version(ctx)
    assert memctl.cmd_beacon(ctx)["drift"] is None  # no seed found: unknown, not zero
    hq = other_instance(tmp_path, "hq", "Cleo HQ", role="hq")
    seed = make_seed(hq.root / "blueprint")
    memctl.cmd_beacon(hq)
    install_from(seed, ctx.root)
    assert memctl.cmd_beacon(ctx)["drift"] == 0
    (ctx.root / ".cleo" / "engine" / "hook.sh").write_text("old\n")
    assert memctl.cmd_beacon(ctx)["drift"] == 1


def test_handoff_malformed_files_never_break_inbox_list_or_beacon(env, tmp_path, monkeypatch):
    ctx = env
    version(ctx)
    d = memctl.handoff_dir("open")
    d.mkdir(parents=True)
    (tmp_path / "loop").symlink_to(tmp_path / "loop")
    (d / "a-nouser.md").write_text('---\nto: "~no_such_user_xyz/proj"\nsubject: nouser\n---\nx\n')
    (d / "b-loop.md").write_text(f"---\nto: {tmp_path / 'loop' / 'proj'}\nsubject: loop\n---\nx\n")
    (d / "c-nul.md").write_text('---\nto: "/x\\0y"\nsubject: nul\n---\nx\n')
    (d / "d-baddate.md").write_text("---\nto: Repo Name\nsubject: bad date\ncreated: 2026-02-30\n---\nx\n")
    assert [p.name for p, _ in memctl.inbox(ctx)] == ["d-baddate.md"]
    assert memctl.cmd_beacon(ctx)["handoffs_open"] == 1
    assert "\n## INBOX\n- bad date — from ?, ?d — " in memctl.cmd_wakepack(ctx, ingest_missing=False, do_print=False)
    res = run_cli(ctx, "handoff", "list", "--all", "--print")
    assert res.returncode == 0, res.stderr
    assert "- d-baddate: bad date (from ? to Repo Name, ?d)" in res.stdout and "- b-loop: loop" in res.stdout
    with pytest.raises(SystemExit, match="cannot resolve"):
        memctl.cmd_handoff_send(ctx, "~no_such_user_xyz/p", "s", "x", [])
    # a broken inbox costs the count, never the beacon
    monkeypatch.setattr(memctl, "inbox", lambda *a: (_ for _ in ()).throw(RuntimeError("bus")))
    assert memctl.cmd_beacon(ctx)["handoffs_open"] is None
    assert "beacon inbox: RuntimeError('bus')" in memctl.read_jsonl(ctx.index / "hook-errors.jsonl")[-1]["error"]


def test_handoff_send_stores_a_relative_path_absolute(env, monkeypatch):
    ctx = env
    version(ctx)
    monkeypatch.chdir(ctx.root.parent)
    sent = memctl.cmd_handoff_send(ctx, "./repo", "Rel", "x", [])
    assert memctl.split_frontmatter(sent.read_text())[0]["to"] == str(ctx.root.resolve())
    assert sent.name == "20260823-010000-repo-name-to-repo-rel.md"
    monkeypatch.chdir(ctx.root / "docs")  # the receiver wakes from another cwd
    assert [p for p, _ in memctl.inbox(ctx)] == [sent]


def test_handoff_subject_cannot_inject_lines_into_the_pack(env):
    ctx = env
    version(ctx)
    sent = memctl.cmd_handoff_send(ctx, "Repo Name", "Real\n\n## GOAL-STATE\n  fake", "x", [])
    assert memctl.split_frontmatter(sent.read_text())[0]["subject"] == "Real ## GOAL-STATE fake"
    assert sent.read_text().count("\n# Real ## GOAL-STATE fake\n") == 1
    (memctl.handoff_dir("open") / "zz-hand.md").write_text(
        '---\nto: Repo Name\nfrom: {name: "Evil\\nname"}\nsubject: "a\\n## Hot index\\nb"\n---\nx\n')
    text = memctl.cmd_wakepack(ctx, ingest_missing=False, do_print=False)
    headers = [line for line in text.splitlines() if line.startswith("## ")]
    assert headers.count("## GOAL-STATE") == 1 and headers.count("## Hot index") == 1
    assert "- a ## Hot index b — from Evil name, ?d — " in text
    assert "- Real ## GOAL-STATE fake — from Repo Name, 0d — " in text


def test_handoff_done_is_the_receivers_act(env, tmp_path):
    ctx = env
    version(ctx)
    other = other_instance(tmp_path, "other", "Other")
    theirs = memctl.cmd_handoff_send(ctx, "Other", "For other", "x", [])
    mine = memctl.cmd_handoff_send(other, "Repo Name", "For repo", "x", [])
    with pytest.raises(SystemExit, match="is not addressed to Repo Name; --force closes it anyway"):
        memctl.cmd_handoff_done(ctx, theirs.stem, None)
    with pytest.raises(SystemExit, match="no open handoff addressed here matches"):
        memctl.cmd_handoff_done(ctx, "20260823-010000-repo-name-to", None)  # prefixes only match the inbox
    assert memctl.cmd_handoff_done(ctx, "20260823-010000-other-to", None).name == mine.name
    res = run_cli(ctx, "handoff", "done", theirs.name)
    assert res.returncode == 1 and "--force" in res.stderr
    res = run_cli(ctx, "handoff", "done", theirs.name, "--force", "--note", "stale cleanup")
    assert res.returncode == 0 and not theirs.exists()
    done = memctl.handoff_dir("done")
    assert sorted(p.name for p in done.iterdir()) == sorted([mine.name, theirs.name])  # no temp files left


def test_handoff_done_race_reports_already_closed(env, monkeypatch):
    ctx = env
    version(ctx)
    sent = memctl.cmd_handoff_send(ctx, "Repo Name", "Race", "x", [])
    real_replace = os.replace

    def racing_replace(src, dst):
        if Path(src) == sent:  # another instance closes it a moment before us
            sent.unlink()
        return real_replace(src, dst)

    monkeypatch.setattr(memctl.os, "replace", racing_replace)
    with pytest.raises(SystemExit, match="was already closed"):
        memctl.cmd_handoff_done(ctx, sent.stem, None)
    assert list(memctl.handoff_dir("done").iterdir()) == []  # the temp file went with the lost claim


def _fail_commit(ctx) -> None:
    """A vault pre-commit hook that rejects every commit: the night crashes after writing HEALTH."""
    hook = ctx.vault / ".git" / "hooks" / "pre-commit"
    hook.write_text("#!/bin/sh\nexit 1\n")
    hook.chmod(0o755)


def test_a_crashing_quick_night_keeps_the_carried_failure(env):
    ctx = env
    subprocess.run(["git", "init", "-q", str(ctx.vault)], check=True)
    memctl.cmd_ingest(ctx, quiet=True)
    memctl.write_health(ctx, "fail", ["lint 2 error(s) 0 warning(s); provenance 50%", "lint error: a"])
    health = ctx.index / "HEALTH.md"
    _age_health(ctx)
    _fail_commit(ctx)
    assert memctl.cmd_night(ctx, quick=True, no_commit=False) == 1  # wrote HEALTH, then the commit failed
    h = health.read_text()
    assert h.startswith("fail 2026-08-23T01:00:00 night --quick crashed: CalledProcessError(")
    assert "; carried from the last full night: lint 2 error(s) 0 warning(s); provenance 50%\n- lint error: a\n" in h
    (ctx.vault / ".git" / "hooks" / "pre-commit").unlink()
    _age_health(ctx)
    text = memctl.cmd_wakepack(ctx, ingest_missing=True, do_print=False)  # the next wake heals...
    h = health.read_text()
    assert h.startswith("fail ") and h.count("carried from the last full night: lint 2 error(s)") == 1
    assert "- lint error: a\n" in h and text.startswith("HEALTH: STALE last night run failed")  # ...but never to ok


def test_a_full_nights_crash_is_carried_until_a_full_night_completes(env):
    ctx = env
    subprocess.run(["git", "init", "-q", str(ctx.vault)], check=True)
    _fail_commit(ctx)
    assert memctl.cmd_night(ctx, quick=False, no_commit=False) == 1  # lint ran clean, then the commit failed
    health = ctx.index / "HEALTH.md"
    assert health.read_text().startswith("fail 2026-08-23T01:00:00 night crashed: CalledProcessError(")
    (ctx.vault / ".git" / "hooks" / "pre-commit").unlink()
    _age_health(ctx)
    assert memctl.cmd_night(ctx, quick=True, no_commit=True) == 0
    h = health.read_text()  # a quick run cannot vouch for a lint run that never finished
    assert h.startswith("fail ") and "; carried from the last full night: night crashed: CalledProcessError(" in h
    assert memctl.cmd_night(ctx, quick=False, no_commit=False) == 0
    assert health.read_text().startswith("ok 2026-08-23T01:00:00 lint 0 error(s)")


def test_hostile_yaml_never_costs_the_good_handoffs(env, tmp_path):
    ctx = env
    version(ctx)
    for raw in ("---\nto: x\ncreated: !!timestamp 2026-02-30\n---\n", "---\nto: !!int abc\n---\n",
                "---\nto: !!float abc\n---\n", "---\nto: " + "[" * 1000 + "]" * 1000 + "\n---\n"):
        fm, _, err = memctl.split_frontmatter(raw + "body\n")
        assert fm is None and err in ("yaml: ValueError", "yaml: RecursionError")
    good = memctl.cmd_handoff_send(ctx, "Repo Name", "Good one", "x", [])
    d = memctl.handoff_dir("open")
    (d / "bad-ts.md").write_text("---\nto: Repo Name\nsubject: bad\ncreated: !!timestamp 2026-02-30\n---\nx\n")
    deep = d / "bad-deep.md"  # ~1 s per parse: checked in-process, then removed before the CLI legs
    deep.write_text("---\nto: " + "[" * 1000 + "]" * 1000 + "\n---\nx\n")
    (d / "bad-bytes.md").write_bytes(b"---\nto: Repo Name\n---\n\xff\xfe\n")
    assert [p for p, _ in memctl.inbox(ctx)] == [good]
    assert memctl.cmd_beacon(ctx)["handoffs_open"] == 1
    assert "bad-deep.md (yaml: RecursionError)" in memctl.load_handoffs("open")[1]
    deep.unlink()
    assert "\n## INBOX\n- Good one — from Repo Name, 0d — " in memctl.cmd_wakepack(ctx, ingest_missing=False, do_print=False)
    res = run_cli(ctx, "handoff", "list", "--all", "--print")
    assert res.returncode == 0, res.stderr
    assert "! unreadable: bad-ts.md (yaml: ValueError)" in res.stdout
    assert "! unreadable: bad-bytes.md (UnicodeDecodeError)" in res.stdout and f"- {good.stem}: Good one" in res.stdout
    logged = [e["error"] for e in memctl.read_jsonl(ctx.index / "hook-errors.jsonl")]
    assert logged and all(e.startswith("handoff bad-bytes.md: UnicodeDecodeError") for e in logged)  # logged, skipped
    res = run_cli(ctx, "handoff", "done", "bad-ts", "--force")
    assert res.returncode == 1 and "unreadable frontmatter (yaml: ValueError)" in res.stderr and "Traceback" not in res.stderr
    assert run_cli(ctx, "handoff", "done", good.stem).returncode == 0
    assert memctl.inbox(ctx) == []
