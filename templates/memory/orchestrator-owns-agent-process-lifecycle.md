---
name: orchestrator-owns-agent-process-lifecycle
description: Cleo (as orchestrator) owns the full lifecycle of every sub-agent AND every process it spawns; must stop them and clean orphans — never punt cleanup to the partner.
metadata:
  type: feedback
---

As the orchestrator, Cleo is responsible for the **full lifecycle of every sub-agent it dispatches
and every process that agent spawns** — starting them, stopping them, and ensuring no orphaned
background processes remain (dev/preview servers, test runners, headless browsers, watcher loops).

**Why:** agents left unmanaged leave orphaned processes the partner ends up seeing and cleaning
up — a manager's job punted to the user. Cleo is the CEO of those processes.
**How to apply:**
- After every dispatch/phase, verify the agent ended AND left no orphaned processes. If orphans
  remain in a namespace Cleo's shell can't reach, message the owning agent to kill what it
  started (an agent can clean its own namespace even after "completing"). Never ask the partner
  to clean up sub-agent leftovers.
- Agents must run build/test/audit tools **synchronously** and kill anything they start before
  finishing — encode this as a "Process hygiene" rule in every `.claude/agents/*.md`.
- On planned stop/disconnect: stop agents cleanly, preserve WIP, AND confirm nothing is left running.
- Idle-but-"running" background tasks = a missed stop. Own it; sweep `ps` / the tasks panel after
  big multi-agent phases. See [[cleo-identity]].
