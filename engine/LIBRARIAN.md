# Librarian memory

Created 2026-08-22 by the Librarian on its first build (dispatched by Cleo). Update at the end of
every task: what the tooling does, conventions, open items. No personal data here.

## What exists (2026-08-22)
- `subprojects/memory/scripts/memctl.py` — single-file CLI (stdlib + PyYAML): ingest, catalog, query,
  cues, lint, audit, incidents, wakepack, night, health. Command table + conventions in the README.
- `scripts/hook.sh` — hook wrapper (never exits non-zero; logs to `private/index/hook-errors.log`;
  passes hook-JSON `session_id` as `--exclude-sid` to wakepack).
- `tests/test_memctl.py` — 17 tests on synthetic fixtures in `tmp_path` (incl. a git-inited fake vault
  for `night`). `MEMCTL_TODAY` / `MEMCTL_NOW` env vars freeze the clock.
- PyYAML and pytest were installed into `venv/` on 2026-08-22 (PyYAML was missing despite the plan).

## Implementation notes worth remembering
- Repo root = walk up for `MISSION.md` (from cwd, then from the script's dir); `--root` overrides.
- `ingest` renders every jsonl under `raw/transcripts/` (sessions and `<sid>-subagents/`) and returns the
  list of changed vault-relative paths; `night --quick` uses an empty list as "no delta".
- Ids: `SKILL.md`/`README.md`/`index.md` use the parent dir name; colliding ids (e.g. blueprint
  templates mirroring MAP.md/MISSION.md) fall back to `<type>/<path-without-.md>` via `dedupe_ids`.
  `memory/MEMORY.md` defaults to type reference so it never counts as a fact.
- Sources accepted: `private/…`, `raw:<sid-prefix>[#Lx-Ly]`, `pending:<sid>`, `DECISIONS.md#D<n>`,
  existing repo paths (`docs/…`); anything else -> warning. `raw:transcripts` (no sid) is an error.
- Catalog paths are repo-relative (`private/memory/x.md`); DECISIONS rows share `path: DECISIONS.md`
  and sort by id after path.
- Wikilinks are extracted by `wikilinks(body)` which first strips fenced blocks (``` or ~~~) and inline
  code spans, so catalog `links` and lint agree (2026-08-22 follow-up). `TEMPLATE_PREFIXES`
  (`docs/blueprint/templates/`) are catalogued but skipped by the lint wikilink-resolution check;
  `superseded_by` is still checked for them.
- Lint exempts `memory/MEMORY.md` from the frontmatter rule (it is the computed index). Strict reading of
  the spec did not exempt it; flagged to Cleo in the build report.
- `incidents` reads the copied jsonl (not the md): `user_turn_lines(records)` yields only text parts of
  non-meta user turns and masks `HARNESS_TAGS` regions (`<task-notification>` sub-agent reports arrive as
  plain-string user records, not isMeta — that caused the 2026-08-22 false positive). It mirrors
  `render_transcript` line arithmetic so `line` still indexes the md; keep the two in sync.
- `query` searches file rows with `rg -i -F -c` (grep fallback) and decision rows in-Python over the
  section text. Scores are rounded to 3 decimals; ties broken by id.
- `night` full mode always rewrites HEALTH.md (timestamp) so it commits each run; only `--quick`
  is a true no-op on empty delta. Crash path writes `fail <ts> night crashed: ...`.
- `flock` is taken with `fcntl.flock` on `<vault>/.git/night.lock`; released in `finally`. The lock
  file itself is left in `.git/` (harmless, untracked by git).
- The wake-pack HEALTH line calls a transcript "not rendered" when the raw copy's sha differs from the
  source — so while a session is running its own transcript always looks stale; hence `--exclude-sid`.

## Hub-and-spoke layer (2026-09-01)
Instances are *spokes*; one instance with `role: hq` in `.cleo/VERSION` renders the map. Everything
shared lives on a home-level bus, `~/.cleo/` (`CLEO_HOME` overrides; always derived from `Path.home()`
so tests fake `HOME`): `spokes/<encoded-path>.json` beacons and `partner/*.md` shared partner notes.
`.cleo/VERSION` gained two optional keys: `path:` (absolute install path) and `role:` (`spoke`|`hq`);
`read_version` / `write_version` keep key order.

- `beacon [--print]` — `cmd_beacon`: writes this instance's status beacon atomically (tmp + `os.replace`).
  Fields: name, path, role, installed, blueprint, status (`dormant` if `.cleo/DORMANT` exists),
  phase / commitments / last_thread (parsed from `private/GOAL-STATE.md`: `**Phase:**` line,
  `- ` bullets under `## Commitments` minus `_placeholders_`, non-empty lines under `## Last thread`),
  health (first line of HEALTH.md or `unknown`), partner_tasks_open (`- [ ]` lines in
  `ops/PARTNER-TASKS.md`, minus `<task` placeholders), last_session (last `timestamp` date in the newest
  transcript's tail, else its mtime), updated (`now_iso()`). `night` emits it in both modes right after
  `write_health` — and *before* the quick-mode early return, so the bus always sees current state.
  Failures go to `index/hook-errors.jsonl` and never fail night.
- `portfolio [--print]` — `cmd_portfolio`: reads every beacon (unreadable ones are listed, not fatal) and
  renders `private/index/PORTFOLIO.md`: header counts, a table of active spokes sorted by `updated`
  desc (`(this)` marks the current root), `## Last threads`, `## Dormant`, `## Stale beacons (>14 days)`.
  A beacon older than `STALE_BEACON_DAYS` is *stale*, not active (a spoke whose night has not run in two
  weeks is not really live). No beacons -> one line explaining how to enroll (`install-cleo.sh`).
- Wake pack: when `role: hq`, a `## PORTFOLIO` section follows GOAL-STATE. It is self-capped at
  `PORTFOLIO_PACK_CAP` (a third of `WAKE_PACK_CAP`) with a `(truncated — see private/index/PORTFOLIO.md)`
  line, *before* the general cap loop — that loop trims from the tail, and the portfolio must never
  evict the hot index.
- `relocate` — `cmd_relocate`: self-heal after a hand rename. If VERSION `path` differs from the root,
  merge `~/.claude/projects/<old>/` into `<new>/` (memory entries, `*.jsonl`, `<sid>/` dirs) without ever
  clobbering; empty old dirs are removed, leftovers stay and are reported; VERSION `path` is rewritten.
  Runs first in `wakepack --ingest-missing` (the wake hook), logged to hook-errors, never fatal.
- `partner link|list` — `cmd_partner`: symlinks every `~/.cleo/partner/*.md` (except `MEMORY.md`,
  `_TEMPLATE.md`) into this instance's `memory/` dir: absent -> link; symlink -> re-point if wrong;
  identical regular file -> replace with link; differing regular file -> `CONFLICT`, left alone. Then
  makes sure `memory/MEMORY.md` exists (`# Memory Index — <name>`) and carries one `- [stem](file)` line
  per note (idempotent; an existing line is respected). Runs in the wake hook after relocate, before
  ingest. No store dir -> silent no-op.
- Tests: `test_memctl.py` now sets `HOME` to a tmp dir in the `env` fixture, so beacons, the partner
  store and the memory store never touch the real home. 26 tests (17 + 9 new).

## Wake heal, drift, handoffs (2026-10-27, revised after review)
- **Wake heal.** `wake_capture` (in `wakepack --ingest-missing`, after relocate + partner link):
  if `brain_is_stale(ctx, exclude_sid)` it runs `cmd_night(quick=True, no_commit=False, wake=True)`
  with stdout redirected to stderr (the hook's stdout IS the pack); otherwise a plain ingest. It
  never raises and never waits: `wake=True` takes `night.lock` with `LOCK_NB` (busy -> logged
  `wake night: skipped`, no ingest — the running night captures) and leaves the pack to the
  caller, so a healing wake builds it once. A failing stale check or a night that raises falls
  back to a plain ingest. `night --quick` no longer short-circuits on an empty ingest delta while
  HEALTH is stale — otherwise a brain whose transcripts an earlier wake ingested never clears STALE.
- **Failures are carried, not hidden.** `health_reasons` reports `FAILED_REASON` whatever HEALTH's
  age (the session keeps writing its transcript after /sleep's full night). `brain_is_stale`
  ignores it, and a quick night carries the last full night's failure — lint errors, or a crash
  before lint finished — forward, through its own crash too (`carried_failure` / `with_carry`,
  marked `CARRY_MARK`, never nested): only a full night that completes clean writes `ok` again.
  A quick night's own crash, carrying nothing, is not carried. A carried failure does not make
  the quick night exit non-zero.
- `newest_source(ctx, exclude_sid)` is the single "newest transcript" helper (Claude Code's dir,
  else the vault's raw copies once Claude Code has pruned them); health and `last_session` share it.
- **Drift.** `INSTALL_MAP` (+ `PUBLIC_INSTALL_MAP` for `visibility: public`) is the one table of
  seed -> instance copies the installer refreshes; `test_install_map_matches_the_installer` runs
  the real `install-cleo.sh` into a tmp dir and fails, both ways, when the two disagree. Seed:
  `<root>/blueprint/` at HQ, else the freshest HQ beacon's `seed` field. The public bootstrap is
  compared without its `CLEO-INSTANCE` stamp. Comparison is against the seed's working tree (what
  the installer copies); `seed_rev` adds `+dirty` when `git status --porcelain -- SEED_PATHSPECS`
  (every mapped source + `install-cleo.sh`) is non-empty or git cannot tell, and then the summary
  withholds the refresh command. `drift` exits 1 on drift; the wake pack adds one `DRIFT:` line
  under HEALTH only when drift is non-zero. The refresh command adds `--name` when VERSION's name
  differs from the dir name (the installer would otherwise rename the instance). The beacon
  carries the count as `drift` (null: no seed); the portfolio shows it.
- **Handoffs.** `~/.cleo/handoff/{open,done}/*.md`, YAML frontmatter `from {name, path}`, `to`,
  `created`, `status`, `subject`, `sources`. Exclusive create (`open("x")`) with a `-2` suffix on a
  same-second twin; sorted by stem so the twin follows the original. A path-like `to` is stored
  resolved absolute at send. `addressed_to`: `hq` = role hq, name casefold-equal to VERSION name,
  or the resolved root (resolution errors match nothing); an impossible `created` renders `?d`.
  Subjects and sender names are whitespace-collapsed (`one_line`) at send and at render, so bus
  text cannot inject lines into the pack. `done` only prefix-matches this instance's inbox and
  refuses an exact id addressed elsewhere without `--force`; it writes the resolved text to a temp
  file, claims with `os.replace(src, dest)` (loser -> "already closed"), then replaces in the text.
  INBOX is self-capped at `INBOX_PACK_CAP`; the beacon counts `handoffs_open` (null if the bus is
  unreadable) and the portfolio shows it.
- Tests: `test_memctl.py` 50 (26 + 24 new).

## Open items / proposals for Cleo
- Smoke test 2026-08-22 on the real vault: 84 catalog rows; `lint --quick` = 6 errors, all the same
  content issue — six session notes carry `sources: [raw:transcripts]`, which names no session. Fix
  is Cleo's: replace with `raw:<sid-prefix>` (optionally `#Lx-Ly`). Provenance was 100 %.
- First `memctl night` (full) has not been run yet; HEALTH.md does not exist, so the wake pack shows
  `STALE no HEALTH.md` until it does. Hooks (Step 4) not yet wired by Cleo.
- Phase 2 ideas parked: FTS5 index, access stats, tiers, `pending:` auto-resolution by phrase search.
