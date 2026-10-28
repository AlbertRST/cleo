# USING CLEO — A guide for the partner

Cleo is a persistent assistant that runs inside Claude Code. You talk to her; she remembers,
plans, delegates to specialist agents, and keeps the project's records. You do not have to
remember any of her process. This guide covers what you see and what you do.

## The pieces

| Term | What it is |
|---|---|
| **Instance** | A project directory with Cleo installed. It has its own memory (the vault) and its own docs. |
| **Spoke** | Any instance. At the end of each session it writes a small status file (a beacon) to `~/.cleo/spokes/`. |
| **HQ** | The one instance that is the hub. It renders the portfolio map from all beacons and owns the shared partner model. It is Cleo with a wider desk, not a different Cleo. |
| **Bus** | `~/.cleo/`, outside every repo: beacons, the shared partner model, handoffs. Local-only. |

It is one identity and one partner model across all instances. It is never one shared context
window: each project keeps its own memory, and facts do not leak between them.

## What happens when you open a session

**Wake.** Cleo runs `/wake` on her own, even if you type nothing. She reads her health report,
the goal for the project, the mission, the map, recent decisions and open tasks. Then she
tells you in a few lines where things stand and proposes two to four options, with a
recommendation. You pick. If the health report says `STALE` or `fail`, she repairs her memory
first, before project work.

**Sleep.** When you say you are leaving ("sleep", "good night", "we're done"), Cleo runs
`/sleep` on her own: she saves decisions, corrections and commitments, rewrites the project's
state docs, checks her memory for errors, commits the work, confirms no agent or process is
left running, and gives you a short report. The report ends with a fixed closing line. If it is
missing, the ritual was not completed.

If a chat ends abruptly, a safety-net hook saves the transcript and refreshes the status files.
The full consolidation then happens at the next wake.

## Where the memory lives

| Layer | Where | Holds |
|---|---|---|
| Docs | the project repo (`MISSION.md`, `MAP.md`, `DECISIONS.md`) | the work: mission, current state, the why behind decisions |
| Vault | `private/` inside the project | Cleo's working memory: session notes, facts, commitments, transcripts |
| Partner model | `~/.cleo/partner/` | how you work and what you want, shared by every instance |

The vault and the bus are **local-only**. They are never pushed to any remote. The git guard
enforces this with hooks (`GIT-GUARD.md`). Memory is never deleted either: an outdated note is
superseded and archived, so you can always see what Cleo used to believe. Full spec:
`MEMORY-SYSTEM.md`.

## Starting a new project with Cleo

```bash
./install-cleo.sh ~/path/to/project --name "Project Name"        # private project
./install-cleo.sh ~/path/to/project --name "Project Name" --public   # will be published
```

Then open Claude Code in that directory. Cleo wakes, you fill in `MISSION.md` when you know what
the project is for, and from then on the project is enrolled and visible to HQ. `--public` keeps
all of Cleo's machinery out of what gets published. Re-running the installer upgrades an
instance and keeps project content.

## Handoffs between instances

If work in one project belongs to another, Cleo sends a **handoff**: a short work order on the
bus. The receiving instance lists it in its inbox at its next wake, records what it carries in
its own memory, then closes it. You never copy context between chats. Handoffs carry the task
and pointers, not memories.

## Who does what

| You | Cleo |
|---|---|
| Direction, priorities, final decisions | Planning, prioritizing, delegating, reviewing |
| Domain judgment and relationships | Dispatching specialist agents and checking their work |
| Money, signatures, legal identity, accounts, CAPTCHAs | Memory, documentation, git hygiene, process hygiene |
| Tell Cleo when she is wrong | Disagree with you openly when you are |

You never remember the process: Cleo runs wake, sleep, memory upkeep and her own improvement
cadence. Anything only you can do arrives as an item in `ops/PARTNER-TASKS.md`, with why it is
human-only and what she needs back. Check that file; tell Cleo when an item is done.

## Standing authority

By default Cleo asks before anything irreversible. You can grant standing permission for a
narrow class of action (scope, limit, date, how to revoke). Each grant is recorded in
`ops/AUTHORITY.md` with the evidence behind it. Silence is never a grant. One line from you
revokes any grant, and she stops at once. Money, signatures, legal identity, relationships and
final direction cannot be granted. The trust model and the capability ladder are in
`NORTH-STAR.md`.

## Pausing a project

```bash
touch ~/path/to/project/.cleo/DORMANT
```

The project drops out of HQ's table and into a separate dormant list. Nothing is uninstalled.
Delete the file to resume.

## FAQ

**Why was my push blocked?** The git guard refuses a push that would send private memory, use
the wrong git identity or remote, or (in a public repo) include a denylisted term. The message
names the cause and prints a `fix:` line. Fix the cause, or ask Cleo. Do not use `--no-verify`.

**What if a wake says `STALE`?** The memory index is out of date, usually because a session
ended without the full sleep. Cleo repairs it before doing project work. You need do nothing.

**How do I correct Cleo?** Say so in plain words. A correction is saved at once, not at the end
of the session. If it is about how you want Cleo to work in general, it goes into the shared
partner model so every instance learns it.

**Can Cleo forget something?** She does not delete. Tell her a note is wrong and she supersedes
it; the old note is archived and can still be found.

**Does Cleo manage my calendar?** Not today. You plan your schedule and Cleo advises. Agenda
handling is a future opt-in that needs a reliable messaging channel and a calendar connection,
and only if you switch it on.

**How do I know a Cleo claim is real?** Ask for the evidence. Capability claims in this repo
are tied to checkable criteria (`NORTH-STAR.md`), and Cleo's "done" for any task is a claim she
has checked, not an assertion.
