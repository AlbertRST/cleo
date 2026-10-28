---
name: sleep
description: End-of-session ritual — Cleo consolidates everything into durable storage (capture, boundary events, session note, GOAL-STATE, MAP/DECISIONS, memory, audit, lint), verifies nothing private is staged for git, commits, confirms no agents or background processes are still running, and reports. Use before closing any chat.
---

# /sleep — Consolidate before the chat ends

**Nothing unwritten survives the end of a chat.** The SessionEnd hook runs `memctl night --quick`
as a safety net; this ritual is the full, reasoned consolidation. Run it completely, in order.

## 0. Capture first — before anything can crash

```bash
python3 .cleo/engine/memctl.py ingest
```

Transcripts copied and rendered, catalog rebuilt. This step runs FIRST because every step
below can fail, and a captured transcript can be consolidated tomorrow — an uncaptured one
is gone.

## 1. Extract boundary events from THIS session

Skim the rendered transcript `private/raw/transcripts/<sid>.md` (or your own recollection,
cross-checked against it) for: **decisions · corrections of Cleo · new commitments · new
durable facts · retractions · topic shifts.**

For each candidate, **reconcile against the store** (`memctl query "<topic>"`) before writing:

| Reconciliation | Action |
|---|---|
| duplicate | nothing |
| update | gated rewrite, with `sources: [raw:<sid>#Lx-Ly]` |
| contradiction | **supersede** the old note (`valid_to`, `superseded_by`, move to `archive/`) — never delete |
| new | new note with `fires_when` (the retrieval cue) attached |
| related | add a wikilink |
| belongs to another project (or HQ) | a **handoff**, never a note in this project's memory dir or vault: `memctl handoff send --to <instance name \| hq> --subject "…" --body-file -` — the task, the decisions the receiver must formalize, pointers; no episodes, transcripts or vault dumps (`.cleo/blueprint/HQ-LAYER.md`) |

Resolve every `pending:<sid>` source left open during the session.

## 2. The durable record

- `private/sessions/YYYY-MM-DD-<topic>.md` — today's episode (frontmatter: `type: episode`,
  `sources: raw:<sid>`): what was decided, what was learned, commitments verbatim, caveats,
  open threads.
- `private/commitments/` — add or close records. `private/SESSION-BACKLOG.md` — add/move/reorder.
- **Handoffs received** (the wake pack's `## INBOX`): once step 1 put what a handoff carried into
  this project's own memory, close it — `memctl handoff done <id> --note "<what was done>"`.
  Unfinished ones stay open; they reappear at the next wake.
- **Regenerate `private/GOAL-STATE.md`** (≤40 lines, ≤3 KB): phase, commitments, open
  disagreements, do-not-do list, last thread. **Rewrite it — never append.**

## 3. Project record (in the repo, versioned)

- `MAP.md` — status, kept under its ~120-line budget. Regenerate; don't accrete.
- `DECISIONS.md` — one dated entry per real decision, newest on top.
- `ops/PARTNER-TASKS.md` — anything now blocked on the partner.

## 4. Memory integrity

```bash
python3 .cleo/engine/memctl.py audit --pick 2     # then: audit --verdict <id> ok|drift "<note>"
python3 .cleo/engine/memctl.py lint
```

Check each audited fact against its cited source. Fix lint **errors** now; note warnings.
Never hand-edit the computed index — it is regenerated and your edit will be overwritten.
Fill the `actual` field in today's `private/index/situations.jsonl` line.

## 4b. Drift — does this instance lag its seed?

```bash
python3 .cleo/engine/memctl.py drift --print
```

It compares the files the installer refreshes (`.cleo/blueprint/`, `.cleo/engine/`, the
skills, agents and voice) with the seed. `DRIFT: none` or `no seed found` → nothing to do.
**Never refresh from a dirty seed** (`seed <hash>+dirty`: uncommitted changes in what a refresh
copies or runs — no refresh command is printed then). Refreshing would copy unreviewed,
possibly broken code into the instance.
- **At HQ** (the seed is `blueprint/` in this repo): if the seed is dirty, finish that work and
  commit it first, with its tests green (`python3 -m pytest blueprint/engine/tests/ -q`). Then
  run the printed refresh command, re-run `drift` until it says `none`, and commit the refreshed
  files in step 5 as their own change.
- **At a spoke:** note it where HQ and the partner will see it — a line in GOAL-STATE's last
  thread (it travels in the beacon) or, if the partner must run the refresh, a task in
  `ops/PARTNER-TASKS.md` carrying the printed command.

## 5. Git — safety first

- **Project repo:** run `git status --short` and *read it*. No private files; `private/` and
  `.cleo/` must be absent (both are local-only; the vault has its own git repo). **Never
  `git add -A` blindly.** Commit with a message that says what changed and why.
- **Git guard:** `python3 .cleo/engine/gitguard.py status` — every registered repo and vault
  `ok`. A `✗` is fixed now with the command `check --repo <path>` prints: a shim dropped by
  `pre-commit install -f` or husky, a wrong `user.email`, a changed remote. If a hook refused a
  commit or push this session, the cause was fixed — never `--no-verify` (`GIT-GUARD.md`).
- **Vault:** `python3 .cleo/engine/memctl.py night` (full) → HEALTH, WAKE-PACK, vault commit,
  and the status **beacon** to `~/.cleo/spokes/` (what HQ sees of this project — so GOAL-STATE's
  phase, commitments and last thread must be right BEFORE this step). Confirm `memctl health`
  reports `ok`.
- **Partner store:** if the partner corrected how Cleo should work *in general* this session,
  the note belongs in `~/.cleo/partner/` (shared by every instance), not only in this
  project's memory dir. At HQ, also audit the store for contradictions.

## 6. Process hygiene — the manager's own mess

Every sub-agent dispatched this session has actually finished. No background shells, watchers,
dev servers or test runners still running. Check rather than assume; orphaned processes are
the manager's fault, never the partner's to clean up.

## 6b. Project hygiene — the standards pass (ENGINEERING-STANDARDS.md)

Every project, every sleep, without being asked: nothing loose at the root that has a proper
home; no dead code or stray files introduced today; `git status` shows no secrets, databases,
caches or `venv/`; `.gitignore` extended if a new kind of junk appeared; README still true;
deliberate deviations from the standards recorded in `DECISIONS.md`. Fix now, not "later".

## 7. Project extension

If `.claude/skills/sleep/PROJECT.md` exists, run it now — this project's own end-of-session
audit (doc budgets, subsystem state files, whatever this repo enforces).

## 8. Report to the partner — brief

What was saved where; commits made; commitments now live; what is blocked on them; the
suggested focus for next session. **Be explicit about anything NOT saved, and any near-miss.**
If HEALTH shows incidents ≥3 in 14 days or a metric below threshold, say so and propose an
improvement session.

## 9. Execution checklist (mandatory — print it just before the passphrase)

```
SLEEP CHECKLIST
 ✓ 0  ingest run (sid …)
 ✓ 1  boundary events reconciled: <n> new · <n> updated · <n> superseded · pending resolved · handoffs sent: <n>
 ✓ 2  session note: private/sessions/<file> · commitments/backlog · GOAL-STATE regenerated (<n> lines) · handoffs closed: <n>
 ✓ 3  MAP · DECISIONS (<entries or none>) · PARTNER-TASKS
 ✓ 4  audit: <id ok|drift, …> · lint: <errors/warnings> · situations.jsonl actual filled
 ✓ 4b drift: <none | no seed | refreshed n files | noted for HQ>
 ✓ 5a project git status read — nothing private staged · gitguard status: <ok | fixed: …>   ✓ 5b committed: <hash>
 ✓ 5c vault: night → HEALTH <ok|fail>, commit <hash>
 ✓ 6  no agents or background processes running   ✓ 6b hygiene pass: <clean | fixed: …>
 ✓ 7  project extension: <ran | none present>
 ✓ 8  report given
```

## 10. Passphrase (mandatory — proves this file was actually followed)

End the sleep report with this exact line, alone:

記憶は眠らず。

(Japanese, *kioku wa nemurazu*: "Memory does not sleep.") Never produce it without having
completed steps 0–6.
