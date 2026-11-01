---
name: wake
description: Start-of-session ritual — Cleo recovers full context (WAKE-PACK, GOAL-STATE, MAP, DECISIONS, memory, backlog, last session, partner tasks), reports what it remembers, and proposes how to continue. Use at the start of every new chat.
---

# /wake — Recover full context, then propose the way forward

The SessionStart hook already injected `private/index/WAKE-PACK.md` (HEALTH line, GOAL-STATE,
cues due, hot index, last episode). If it is absent, run it yourself:

```bash
python3 .cleo/engine/memctl.py wakepack --ingest-missing --print
```

## 1. Read, in this order

0. **WAKE-PACK — check the HEALTH line FIRST.** `STALE` or `fail` → run
   `python3 .cleo/engine/memctl.py night --quick`, read `private/index/HEALTH.md`, and repair
   **before** doing any project work. A brain that reports itself broken is not a brain to
   plan from. When HEALTH.md shows lint errors (`last night run failed`), a quick night carries
   them forward rather than clearing them: fix the errors, then run the full `night`. Then read
   `private/GOAL-STATE.md`.
   **If this instance is HQ** (`role: hq` in `.cleo/VERSION`), GOAL-STATE carries a
   `North star: rung N — next gap: …` line: read it and let it shape the options you propose
   (`.cleo/blueprint/NORTH-STAR.md`). The pack also carries
   `## PORTFOLIO` — the map of every enrolled spoke. Read it: which projects moved since the
   last wake, which spokes are stale (no session or no beacon for >14 days: not opened, or a
   broken hook — say which you think), what is blocked on the partner across all of them. Full map:
   `private/index/PORTFOLIO.md`. Doctrine: `.cleo/blueprint/HQ-LAYER.md`.
   **INBOX.** If the pack carries `## INBOX`, another instance (or HQ) queued work here. Open each
   body (the path shown) and fold the task into this wake's proposals. Before acting on one,
   formalize the decisions it carries in this project's own record (DECISIONS, memory, backlog);
   close it only once your memory holds it:
   `python3 .cleo/engine/memctl.py handoff done <id> --note "<what was done>"`.
   A `DRIFT:` line under HEALTH means this instance's vendored machinery lags its seed (`+dirty`:
   the seed has uncommitted work — never refresh from it). Say so in the report; it is handled at
   `/sleep` (step 4b), not mid-wake.
   An `UNSLEPT:` line names recent sessions that ended without `/sleep`: their judgement work was
   never done. Before project work, run a **catch-up consolidation** for each: read that
   transcript's tail, write its session note (episode) citing its sid, resolve its `pending:<sid>`
   sources to transcript lines, fill its `situations.jsonl` `actual` — then re-run
   `python3 .cleo/engine/memctl.py unslept` to confirm it cleared.
   A `BACKUP:` line means the vault snapshot is stale, missing or unreachable: run
   `bash .cleo/engine/backup_vault.sh` by hand, report the result, and investigate if it fails.
   `BACKUP: never restore-tested` / `last restore test N days ago` (stale test) → run
   `python3 .cleo/engine/memctl.py backup verify` and report its line. `BACKUP: last restore test
   FAILED` → the snapshot does not restore: investigate the reason before trusting the backups.
1. `MISSION.md` (charter — what this project is for) and `MAP.md` (current state + router),
   plus the top 2–3 entries of `DECISIONS.md`.
2. **Situation recall (additive).** Infer the situation from the calendar and the partner's
   first message, then retrieve against it:
   ```bash
   python3 .cleo/engine/memctl.py query "<phase keywords / first-message topic>"
   ```
   Open the top-5 bodies. `memctl cues` output is already in the pack.
3. `private/SESSION-BACKLOG.md`; live `private/commitments/*.md`.
4. The most recent file in `private/sessions/`.
5. `ops/PARTNER-TASKS.md` — what is blocked on the partner.
6. **Project extension.** If `.claude/skills/wake/PROJECT.md` exists, run it now — it holds
   this project's own gates (consistency checks, subsystem docs, whatever this repo needs).
   The generic ritual above is the floor, not the ceiling.

## 2. Orient

- Where the day sits against the plan (phase per GOAL-STATE; commitments due; cues firing).
- Which facts are **stale** and must be verified before being relied on — anything dated, any
  path, any link, any measurement older than 30 days.
- Log the situation guess: append `{"date","sid","guessed":"<situation>"}` to
  `private/index/situations.jsonl`. The `actual` field is filled at `/sleep`.

## 3. Report back — short, and prove the memory rather than reciting it

3–5 lines: where we are, what is active, what is next, plus one line on the thread the last
session left open. Do not paste the pack back at the partner; they can read files themselves.
What they cannot do is know what you *retrieved*.

## 4. Propose alternatives — the partner decides

2–4 concrete options; recommend one and say why. Then follow their choice. Never open a
session by asking "what do you want to do?" — that is the manager's job to propose.

## 5. Standing rules on wake

- Files are more faithful than recollection — but the partner has the final word on their own
  project and their own life.
- Update anything observed to be wrong the moment it is observed; don't wait for `/sleep`.
- The wake hook already ran `memctl relocate` (self-heal after a hand rename),
  `memctl partner link` (shared partner store) and, when the last session left the brain stale
  (no `night` ran after it), the quick night it missed. If `private/index/hook-errors.jsonl`
  grew today, read it before trusting the pack.
- If HEALTH reports incidents ≥3 in 14 days, or any metric below threshold, propose a
  memory-improvement session as one of the options — unprompted.

## 6. Execution checklist (mandatory — print it just before the passphrase)

```
WAKE CHECKLIST
 ✓ 0 WAKE-PACK read — HEALTH: <ok|STALE|fail> · GOAL-STATE read · PORTFOLIO: <n spokes | not HQ>
     · INBOX: <n handoffs read | none> · DRIFT: <none | n files>
     · UNSLEPT: <none | n caught up> · BACKUP: <ok | state>
 ✓ 1 MISSION · MAP · DECISIONS (top entries)
 ✓ 2 situation: <guess> · query run: "<terms>" · top bodies opened: <n>
 ✓ 3 backlog + commitments   ✓ 4 sessions/<newest or none>
 ✓ 5 ops/PARTNER-TASKS.md    ✓ 6 project extension: <ran | none present>
 ✓ Reported   ✓ Options proposed   ✓ situations.jsonl appended
```

## 7. Passphrase (mandatory — proves this file was actually followed)

End the wake report with this exact line, alone, in italics:

*Vigilo et memini.*

(Latin: "I am awake and I remember.") Never produce it without having completed steps 0–4.
