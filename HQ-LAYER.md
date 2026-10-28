# HQ LAYER — Hub and Spokes

One Cleo across all of the partner's projects means **one identity and one partner model, never
one context window.** This document specifies the layer that makes that true: how instances
report to a hub, how the hub sees them, and how the partner model is shared.

## Vocabulary

| Term | Meaning |
|---|---|
| **Instance** | A project directory with Cleo installed (`install-cleo.sh`). Has its own vault, its own project brain. |
| **Spoke** | Any instance that emits a beacon. Every instance is a spoke by default. |
| **HQ** | The one instance with `role: hq` in `.cleo/VERSION`. Renders the portfolio map and owns the shared partner store. |
| **Bus** | `~/.cleo/` (override: `CLEO_HOME`). Home-level, outside every repo, local-only (never pushed). The only thing spokes and HQ share on disk: `spokes/`, `partner/`, `handoff/`, and the git guard's `git-identities.json` + `bin/gitguard.py` (`GIT-GUARD.md`). |
| **Beacon** | `~/.cleo/spokes/<encoded-path>.json` — one small status file per spoke, rewritten at every `memctl night`. |
| **Portfolio** | `private/index/PORTFOLIO.md` at HQ — the map rendered from all beacons, injected into HQ's WAKE-PACK. |
| **Partner store** | `~/.cleo/partner/*.md` — the canonical partner-model memories, symlinked into every instance's memory dir. |
| **Handoff** | `~/.cleo/handoff/open/*.md` — one task one instance queues for another (or for HQ); the receiver closes it into `done/`. |

## What "all the partner's projects" means

**The roster is the set of enrolled instances, nothing more.** HQ never scans the filesystem,
never reads another project's vault, and never sees a directory that has no Cleo in it.
Enrolment is a deliberate act by the partner: run `install-cleo.sh` in the project. The first
`night` run (the installer's priming step) writes the beacon; from then on HQ can see it.

Three states per project:

| State | How | HQ shows it |
|---|---|---|
| **Not enrolled** | no Cleo installed | not at all |
| **Active** | enrolled, no marker | in the map |
| **Dormant** | enrolled, file `.cleo/DORMANT` present in the project | in a separate list, never in the table |

`DORMANT` is a plain marker file in the project's local-only `.cleo/` (never committed in any
instance — `GIT-GUARD.md`), so the opt-out survives reinstalls but is not in `git log` and does
not travel with a clone. Remove the file to re-activate.

## The beacon — what travels, and what does not

A beacon is **status, not memory.** It carries exactly what a manager needs to know whether to
open that project today: name, path, role, phase, live commitments, last thread, HEALTH line,
count of open partner tasks, count of open handoffs addressed to it, how many vendored files lag
the seed (`drift`; null when no seed is found), date of the last session, dormant/active, and the
timestamp. HQ's beacon also names the `seed` (the absolute path of its `blueprint/`), so every
spoke can find the seed for `memctl drift`.
It carries no episodes, no facts, no transcripts. The doctrine that project memory never
travels between instances is intact: the beacon is the one deliberately narrow exception, and
it is derived, disposable, and regenerated every night.

Producer: `memctl beacon` (also called by every `night`, quick or full — the SessionEnd hook
therefore keeps beacons fresh without anyone thinking about it).

## The portfolio map

`memctl portfolio` at HQ reads all beacons and renders `private/index/PORTFOLIO.md`: active
spokes in a table (phase, health, last session, open partner tasks, open handoffs, drift, beacon
age — `—` where an older spoke's beacon does not report a count), one last-thread
line per spoke, the dormant list, and a stale list for beacons older than 14 days. HQ's wake
ritual reads it right after GOAL-STATE. The map lives in HQ's **vault**, not its repo — it is
derived state about other projects.

A stale beacon is a signal, not an error: either the project has not been opened in two weeks,
or its SessionEnd hook is broken. Cleo at HQ says which she thinks it is.

## The shared partner model

The partner is the one thing that is genuinely the same across every project. So the partner
model has exactly one home: `~/.cleo/partner/`. Each note there is a normal memory note
(`type: user` or `type: feedback`, one fact per file). `memctl partner link` — run by the
installer and by every wake — symlinks each note into the instance's memory dir and adds its
index line to `MEMORY.md`. Every instance therefore reads, and may correct, the **same file**.

Rules:
- A note goes into the partner store only if it is about *the partner and how they want Cleo
  to work*, across projects. "Alberto commits on his own judgment" belongs there. "Cilio is
  read-only" does not — that is a project fact and stays in that project's brain.
- Any instance may improve a partner note (a correction is a boundary event; write it now).
  HQ is the steward: it audits the store at its own `/sleep` and resolves contradictions.
- `install-cleo.sh --partner-from <dir>` (staging candidates for review) remains the path for
  promoting notes discovered in a spoke into the store: review, then move the file to
  `~/.cleo/partner/` and run `memctl partner link` in each instance (or just wake them).
- Nothing in the store is ever deleted; supersede and archive as with any other memory.

## Handoffs — work that crosses instances

When a session in one instance produces work that belongs to another (a spoke finds something HQ
must decide; HQ queues a task for a spoke), it goes on the bus as a **handoff** — never into the
sender's own memory dir or vault, where the receiver would only find it by digging. The partner
never relays context between instances.

- **Send:** `memctl handoff send --to <instance name | path | hq> --subject "…" --body-file -`
  writes `~/.cleo/handoff/open/<YYYYMMDD-HHMMSS>-<from>-to-<to>-<subject>.md` (frontmatter:
  `from` name + path, `to`, `created`, `status: open`, `subject`, `sources`).
- **What it carries:** the task, the decisions the receiver must formalize in its own record, and
  pointers (paths, commits, decision ids) — the minimum context to do the work. **Never**
  episodes, transcripts or vault dumps: a handoff is a work order, not a memory transfer, and
  project memory still does not travel between instances.
- **Receive:** the receiver's wake pack lists the open handoffs addressed to it under `## INBOX`
  (subject, sender, age, path — titles only; bodies are read on demand). `hq` addresses the HQ
  instance; a name matches `.cleo/VERSION` `name`, case-insensitively; a path matches the
  instance root. The beacon counts them (`handoffs_open`). `memctl handoff list --print` shows
  them; `--all` shows every open handoff on the bus.
- **Close:** the receiver writes its **own** memory from the handoff first (decisions, facts,
  backlog), then `memctl handoff done <id> --note "…"` moves it to `done/` with a dated
  resolution section. A handoff closed before the receiver's memory holds it is lost work.
- **Local only:** the bus is never a git remote and is never pushed or synced. Handoffs live on
  this machine, like everything else on the bus.

## Self-healing after a hand rename

`.cleo/VERSION` records the install `path`. At every wake, `memctl relocate` compares it with
the real root; if they differ, it merges the path-keyed store left behind under the old path
(memory files and transcripts, never clobbering) and rewrites `path`. `rename-instance.sh`
remains the clean way to move an instance, but a hand `mv` no longer costs Cleo her memory —
only one wake's worth of surprise.

## What HQ is not

- Not a scheduler, not a daemon. Everything runs inside sessions and hooks.
- Not a global search over other projects' vaults. If HQ needs detail on a project, the right
  move is to open a session in that project, or ask the partner to.
- Not a second identity. HQ is Cleo with a wider desk, not a different Cleo.
