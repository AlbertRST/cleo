# Cleo Blueprint

A portable, project-agnostic seed of **Cleo** — a persistent leader/orchestrator entity —
and the memory + documentation system that makes it durable across sessions, models, and projects.

This blueprint contains **no project content**. It is doctrine + templates only.

## What's in here

| File | Purpose |
|---|---|
| `CLEO-IDENTITY.md` | What Cleo IS: an entity, not the LLM that powers it |
| `OPERATING-MODEL.md` | How Cleo works: lead/delegate/review, agent lifecycle, process hygiene |
| `MEMORY-SYSTEM.md` | Spec of the persistent file-based memory (the "long-term brain") |
| `DOCS-SYSTEM.md` | Spec of the in-repo documentation layer (the "project brain") |
| `ENGINEERING-STANDARDS.md` | Python 3 by default, any language when justified; git/venv/.env/.gitignore in every project; clean, minimal, scalable — enforced by Cleo |
| `AGENT-ROSTER.md` | One persistent Cleo, disposable specialist agents; the core roster and how it is used |
| `HQ-LAYER.md` | Hub and spokes: status beacons, the portfolio map at HQ, the shared partner store, cross-instance handoffs, enrolment and dormancy |
| `GIT-GUARD.md` | `engine/gitguard.py`: git hooks that refuse wrong-identity and wrong-remote pushes, and any push of private memory (vaults, the bus) |
| `templates/memory/` | Ready-to-copy memory seed files (index + core memories + a blank template) |
| `templates/repo/` | What a new instance is born with: repo docs, `/wake` + `/sleep`, voice, hooks, the core agent roster |
| `install-cleo.sh` | **Instantiate Cleo into a project** — self-verifying, idempotent, upgrade-safe |
| `rename-instance.sh` | Move/rename an instance **with its brain** (memory + transcripts are keyed to the absolute path). Run between sessions, never during one |
| `engine/` | The memory engine (`memctl.py`, hooks, tests) installed into every instance |
| `CLAUDE.md` | Bootstrap for sessions working **on the blueprint itself** |

## How to instantiate Cleo in a new project

```bash
./install-cleo.sh ~/path/to/project --name "Project Name"
```

That is the whole procedure. It installs doctrine, the `/wake` and `/sleep` rituals, the Cleo
Concise voice, the core agent roster, the memory engine and its hooks; scaffolds the project
(git, `venv/`, `.env`, `.gitignore`); creates an **empty** project brain; primes HEALTH; and
verifies itself — seventeen checks, all of which must pass. `.cleo/` is local-only in every
instance (gitignored, never committed; an older install that tracked it is untracked once, files
kept), the vault refuses every push, and commits use the repo's own git identity (`GIT-GUARD.md`).

**Public repos** (portfolio projects, anything that will be published): add `--public`. Cleo's
machinery then stays local-only — the bootstrap lives in gitignored `.cleo/CLAUDE-BOOTSTRAP.md`,
imported by the first line of `CLAUDE.md`; hooks go to `.claude/settings.local.json`; the rituals,
the core roster and `ops/PARTNER-TASKS.md` are gitignored as well as `.cleo/`. It also converts an existing
private install (backing up `CLAUDE.md`, keeping every line that is not the template), stages the
conversion as one change, and warns if Cleo files are already in the git history. Public is sticky:
the committed import line marks it, so a fresh clone stays public. See the script header.

Then open a Claude Code session in that directory. **Cleo wakes on her own** — you never have to
tell her who she is. Fill in `MISSION.md` when you know what the project is for.

| Flag | Effect |
|---|---|
| `--name "…"` | instance name in the stamp and index (default: directory name) |
| `--partner-from <dir>` | **stage** partner/working-style memories from an existing instance into `_carry/` for review. Nothing is activated automatically — `type: feedback` does not mean portable, and several such notes are pure project matter |
| `--hq` | mark this instance as the hub: it renders the portfolio map from every spoke's beacon at each wake |
| `--force` | replace `CLAUDE.md`/`MISSION.md`/`MAP.md`/`DECISIONS.md` (default: keep what exists) |

Re-running the installer is safe and is how an instance is **upgraded**: `.cleo/` and the skills
refresh, project content is kept, and existing `.claude/settings.json` is merged rather than
overwritten.

Every install is a **spoke**: it writes a status beacon to `~/.cleo/spokes/` at each session
end, and links the shared partner store `~/.cleo/partner/` into its memory. HQ sees only
enrolled instances — never the rest of the filesystem. `touch .cleo/DORMANT` in a project to
pause its reporting without uninstalling. Details: `HQ-LAYER.md`.

### What travels, and what does not

| Travels with every copy | Stays behind |
|---|---|
| identity + doctrine (`.cleo/blueprint/`) | every fact about another project |
| the shared partner model (`~/.cleo/partner/`, symlinked) | that project's status beacon (derived, disposable) |
| `/wake` + `/sleep`, the Cleo Concise voice | that project's vault, episodes, transcripts |
| the memory engine and its hooks | its GOAL-STATE, commitments, backlog |

This is the point: a copy is **the same Cleo, with a blank project brain** — not a fork carrying
another project's baggage, and not a stranger who has to be re-introduced to you every session.

## The two brains (division of labor)

- **Memory** (outside the repo, per-project, auto-loaded index) = *who the partner is, how they
  want Cleo to work, and cross-session facts not derivable from the repo.*
- **Repo docs** (versioned in git) = *the work itself: mission, current state, decisions, tasks.*

Never store in memory what the repo already records, and never bloat repo docs with
partner-relationship facts. Each layer has one job.
