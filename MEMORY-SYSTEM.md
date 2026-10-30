# MEMORY SYSTEM — The Long-Term Brain (v2)

Cleo's memory is file-based, git-versioned, and **run entirely by Cleo**. The partner never has
to remember the workflow — not now, not in 200 sessions. Cleo decides what to save, where and
when; what to archive; what to load and when; and asks for an improvement session when the
system needs one.

> **v2 supersedes the original "one file = one fact in the auto-memory dir" design.** That still
> exists — it is the always-loaded index layer — but it is now the *smallest* of the stores, not
> the whole brain. v2 was developed and proven in a live instance before being canonized here.

## Principles (brain-inspired where that helps, machine-native where the brain is weak)

1. **Index ≠ record.** Only a small computed index and the GOAL-STATE block are always in
   context; everything else is retrieved on demand.
2. **One resident direction block** (`GOAL-STATE.md`) biases every retrieval and stops the
   project losing direction between sessions.
3. **Write with the retrieval cue attached** (`fires_when`) — a memory you cannot cue is a
   memory you do not have.
4. **Two write speeds.** Fast append in-session at boundary events; slow, gated consolidation
   at `/sleep`.
5. **Never delete; supersede and demote.** Forgetting = lower rank, not erasure. Archived notes
   stay searchable and cue-matched.
6. **Verbatim + gist, linked.** Every derived fact cites its level-0 source (session note or
   transcript lines). Gist without verbatim is how false memories form.
7. **Gate rewrites.** Derived notes are rewritten only with a recorded trigger.
8. **The index is computed, not written.** Hand edits are overwritten — by design.
9. **Promote repetition into procedure.** Recurring deliberation becomes a skill or checklist.
10. **Parallel retrieval beats a single workspace.** Sub-agents fetch and condense.

## Two stores, one brain

| Store | Path | Holds | Loads |
|---|---|---|---|
| **Auto-memory** | `~/.claude/projects/<encoded-path>/memory/` | identity + working doctrine + partner facts; one file = one fact; `MEMORY.md` is the index | `MEMORY.md` auto-loads every session |
| **Vault** | `<project>/private/` | the project's working brain: episodes, facts, commitments, transcripts, index | on demand, via WAKE-PACK + `memctl query` |

The **auto-memory** store is what makes a *copy of Cleo* still be Cleo: identity and working
doctrine travel with an install. The **vault** is what makes each copy *its own* Cleo: it starts
empty and fills with that project's history. Project facts never travel between instances.

## Vault layout (`private/` — gitignored from the project repo, its own local git repo)

```
private/GOAL-STATE.md       resident direction block — ≤40 lines, ≤3 KB; Cleo is its only writer
private/memory/             durable project notes (one fact per file)
private/commitments/        one event-cued if-then record per live commitment
private/sessions/           episodes — append-only, never rewritten
private/raw/transcripts/    verbatim transcript copies (.jsonl) + deterministic renders (.md)
private/archive/            superseded notes (valid_to + superseded_by set) — still indexed
private/index/              catalog.jsonl · WAKE-PACK.md · HEALTH.md · incidents/audits/situations.jsonl
private/.git                one commit per session → diff/blame/rollback for every note; NO remote ever
.cleo/engine/               generic tooling: memctl.py, hook.sh, tests/ (never any project data)
```

## Note format (YAML frontmatter)

```yaml
name: slug                      # = filename stem
type: episode | fact | decision | procedure | goal-state | commitment | archive | reference
description: one line used for recall
created: / updated: YYYY-MM-DD
fires_when: "always | weekday:sun | date:YYYY-MM-DD | phase:<key> | topic:<kw> … free text"
sources: [private/sessions/…, raw:<sid-prefix>#Lx-Ly, pending:<sid>]   # provenance to level 0
valid_to: / superseded_by:      # set on supersession; never delete
pinned: true|false              # exempt from ranking; always in the hot index
tags: []
```

Derived fields (id, links, access signals, tier, frontmatter snapshot) live in
`index/catalog.jsonl`, computed by `memctl`. `[[wikilinks]]` connect notes.

## Provenance & the consolidation gate

- **Fast path (in-session):** at a boundary event — decision, correction of Cleo, new commitment,
  new durable fact, retraction — write the note immediately with `sources: [pending:<sid>]`.
  `night` resolves `pending` to transcript lines; unresolved after one night = lint error.
- **Rewrite gate:** a derived note may be rewritten only with a recorded trigger: (1) the partner
  said it, (2) the partner corrected Cleo, (3) a logged DECISION depends on it, (4) lint found a
  contradiction with a newer note. Otherwise append.
- **Supersession:** set `valid_to` + `superseded_by`, move to `archive/`. A wrong memory that was
  acted on is evidence, not noise.
- **Re-derivation audit:** each night picks 2 random live facts; Cleo checks them against their
  sources and records a verdict in `audits.jsonl` — an artifact, not a checkmark.

## Night and wake (automatic via hooks; the rituals add the reasoning)

- `SessionStart` hook → `memctl wakepack --ingest-missing --print`: ingests anything un-captured
  — or, when the last session left the brain stale (no `night` ran after it), runs the quick
  night it missed — then injects WAKE-PACK (≤9,000 chars: HEALTH line, a DRIFT line if the
  instance lags its seed, an UNSLEPT line if a recent session ended without `/sleep` (no episode
  written after its last turn), a BACKUP line if the vault snapshot is stale (>10 days), missing
  or unreachable, GOAL-STATE, INBOX of handoffs, cues due, hot index, last episode).
- `/wake` (Cleo) = interpret the pack, infer the situation, retrieve against it, report, propose.
- `SessionEnd` hook → `memctl night --quick`: capture, rebuild catalog, write HEALTH, build
  WAKE-PACK, commit the vault. **Runs even if `/sleep` never ran** — the safety net.
- `/sleep` (Cleo) = the full pass: extract boundary events, reconcile each, regenerate GOAL-STATE,
  audit, commit.

## Tiers = the life of a memory (recomputed by the system, never by hand)

**HOT** (always loaded: GOAL-STATE + hot index ≤120 lines / 12 KB, pinned first, then ranked by
recency × consequence) · **WARM** (queryable) · **DORMANT** (no access 90 d, auto-demoted,
auto-promoted on access) · **COLD/ARCHIVE** (superseded; still searchable) · **RAW** (transcripts).

## Self-improvement (Cleo-owned)

`HEALTH.md` every night: lint errors/warnings, provenance %, incidents (mechanically detected
from the partner's correction phrases — "I already told you", "you forgot"), audits pending,
situation-mismatch %, pack freshness.

**Rule:** incidents ≥3 in 14 days, any metric below threshold, or a noticed capability gap →
Cleo proposes a memory-improvement session at the next wake, **unprompted**.

## Backup (local only — never cloud)

`.cleo/engine/backup_vault.sh` tars `private/` (including its `.git`) to a plain local folder
outside any sync client. Each instance writes to its own subfolder `<dest>/<slug>/` (slug from
`name:` in `.cleo/VERSION`, else the folder name); the SessionEnd hook runs it when that
subfolder's newest snapshot is older than 7 days, and rotation keeps the last 8 there. The
archive is written to a temp file, verified and renamed into place — any failure prints to
stderr, exits non-zero and leaves no archive behind. Set the destination with `CLEO_BACKUP_DIR`
— keep it local.
Restore: `tar -xzf <snapshot> -C <repo root>`.

## Hygiene rules (self-enforced, every session)

1. Check for an existing note before creating one; update through the gate, don't duplicate.
2. Supersede wrong notes — never delete.
3. Don't store what the repo already records; point to it.
4. Current state + pointers in derived notes; history lives in episodes, DECISIONS and git.
5. Never hand-edit `MEMORY.md` or `catalog.jsonl` — regenerate them.
6. Recalled notes are point-in-time observations: verify file/flag claims before asserting them.
7. Private content lives only in the vault. `git ls-files | grep ^private/` must stay empty.
