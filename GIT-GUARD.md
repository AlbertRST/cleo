# GIT-GUARD — no wrong-identity, wrong-remote or private-memory push by accident

Three rules, all the partner's, made structural instead of remembered:

1. **Private memory is never pushed to any remote.** Every vault (`<project>/private/`) and the
   bus (`~/.cleo/`) stay on this machine. `.cleo/` (Cleo's doctrine, engine, `VERSION`,
   `DORMANT`) is local-only in **every** instance, private or public. The Cleo *system*
   (blueprint, engine) may be public.
2. **Each repo commits as its hosting account.** A public repo must use that account's GitHub
   noreply email.
3. **Pushes go to the registered remote, over the account's SSH host alias, and nowhere else.**
   An https push would authenticate as whichever `gh` account happens to be active. The only
   accepted remote forms are the scp form (`git@<alias>:owner/repo.git`) or `ssh://` through
   the account's alias, plus an absolute local path or `file://` (local mirrors and tests).
   Every other `scheme://` (`https`, `git`, `git+ssh`, `ssh+git`, …) and every `<transport>::`
   helper form is refused.

`engine/gitguard.py` enforces all three with git hooks. It needs Python 3.9+ and only the
standard library, so it runs in repos with no venv. Every shim calls the machine-wide copy at
`$CLEO_HOME/bin/gitguard.py` (default `~/.cleo/`), which every `install` refreshes. Hooks never
depend on any project's `.cleo/`.

**The guard blocks. It never rewrites history, and it never prints a command that does.**

## What it blocks

| Hook | Refuses when |
|---|---|
| pre-push | the repo is local-only: on `never_push`, the bus, or a vault (`…/private` whose parent has `.cleo/VERSION`; a symlinked `private/` is recognised by its logical path when pushed from its root) |
| | the repo is not registered (**fail closed**), its account is missing, or its registered remote is not an accepted form (rule 3) |
| | the repo is public (registry or `.cleo/VERSION`) and its account's email is not a GitHub noreply address |
| | the push URL is not the registered remote (`git push <url>` and `--mirror` included) |
| | a destination is `refs/remotes/*` or `refs/stash` (what `git push --mirror` sends) |
| | any pushed commit's author **or** committer email is not the account email |
| | a pushed commit adds or changes a path under `private/`, `.cleo/`, `.env*` (except `.env.example`), `*.pem`, `*.key`, `id_rsa*`, `id_ed25519*` or `*.pgpass` |
| | a pushed version of a file is a vault note, or a commit or annotated-tag message reads as one |
| | public repo only: an added line, a file name, a commit message or a tag message contains a denylist term |
| pre-commit | the same path classes in the staged diff (they get in through `git add -f`) |
| | registered repo: the commit's author or committer email is not the account email, or (public) that email is not a noreply address |
| | registered repo: a staged file is a vault note; public: a staged added line or file name has a denylist term |

**The pushed commits.** For an update, these are `remote_sha..local_sha`. For a new ref, or a
remote tip git has not fetched, they are every commit not already on a tracking ref of the
registered remote. A configured remote counts as the registered remote only when **all** its
fetch URLs and **all** its push URLs are the registered URL, because tracking refs are filled from
the fetch URL. So a remote that fetches from a mirror or an upstream and pushes to the
registered remote (a triangular setup) is not trusted, and a new branch pushed through it is
checked against its whole history. A push by bare URL trusts no tracking refs either. Deletions send no
commits. Root commits and merges are always included, whatever `log.showRoot` or
`log.showSignature` say. For a merge, the guard checks what the merge itself introduces (the
combined diff). Commits in local-only repos are never checked, because nothing leaves them.

**What counts as a vault note.** A file is a vault note if its own YAML frontmatter (first line
`---`) has a `fires_when:` key and a `sources:` key naming `raw:`, `pending:` or
`private/sessions/` provenance anywhere in its value, as an inline list or as list items. A
format example in the middle of a document is not a note. A note citing only
`private/sessions/…` sessions is. Blobs with a UTF-16 BOM, or NUL-interleaved ASCII, are
decoded as UTF-16 first. The
guard reads the staged blob and every pushed version of each changed file, so a note that was
added and later deleted is still caught. Messages are checked for the same two keys anywhere.

**Visibility** is the stricter of the registry entry and the instance's `.cleo/VERSION`
`visibility:` line.

**The denylist** reads every added line with `--text`, so files git treats as binary
(`*.csv -diff`, UTF-16 exports) are read too. NUL bytes are dropped before matching, so ASCII
terms in UTF-16 text match; a non-ASCII term in a UTF-16 file can be missed. File names (staged
and pushed) are matched too, and a path whose own name holds a term is printed as
`<a path whose name is denylisted>`.

**Path rules** apply at any depth (`docs/private/` too), the same as the machine-wide
`~/.gitignore_global`. `.cleo/` is refused in every registered repo.

**Refusals and fixes.** Every refusal prints what was blocked and why, then a `fix:` line, and
exits non-zero. Only safe fixes are printed as commands: `git config user.email …`,
`git rm --cached` on a staged file, `git push <registered-remote-name>`, and gitguard's own
`register`/`account`/`install`. A problem already in history gets the commits and the paths or
identities involved, plus: *history rewrite needed — back up affected files first; rewrite
only these commits (git filter-repo); never --no-verify; or ask Cleo*. If the guard is missing,
python3 is older than 3.9, or the guard crashes, the hook refuses too.

## Registry: `$CLEO_HOME/git-identities.json` (local-only, never pushed)

```json
{
  "accounts": {"octo-dev": {"email": "1234567+octo-dev@users.noreply.github.com",
                            "user_name": "octo-dev", "ssh_host": "github.com-octo"}},
  "repos": {"/home/me/work/proj": {"account": "octo-dev",
            "remote": "git@github.com-octo:octo-dev/proj.git", "visibility": "public",
            "allow_paths": ["tests/fixtures/*.pem"]}},
  "never_push": ["/home/me/work/proj/private"]
}
```

Repo keys are the absolute, resolved paths of the main worktree, so linked worktrees share one
entry. Which account hosts which repo is data: change the registry, not the code.

`allow_paths` is optional. It holds `fnmatch` globs matched against the whole repo path (`*`
crosses `/`). A match waives the path and secret rules, and nothing else: an instance's own
top-level `private/` or `.cleo/` can never be waived, and neither can the vault, bus,
never-push, identity, remote or content rules.

`$CLEO_HOME/guard-denylist.txt` is optional and maintained by Cleo. It holds one literal per
line, matched case-insensitively; blank lines and `#` lines are skipped. Refusals name the
denylist line number and never echo the term.

## Commands

```bash
GG="python3 ~/.cleo/bin/gitguard.py"     # or <project>/.cleo/engine/gitguard.py before the first install
$GG account  --name NAME --email EMAIL [--user-name N] --ssh-host HOST   # add/update an account
$GG register --repo PATH --account NAME --remote SSH_URL --visibility public|private [--allow-path GLOB]...
             # writes the registry and sets the repo's local user.name/user.email.
             # Refuses: an https remote or another host alias (prints the SSH form), a public repo
             # without a noreply email, and local-only repos. Re-registering replaces the entry
             # (allow_paths included).
$GG identity --repo PATH         # re-apply the registered identity (the installer runs it before committing)
$GG install  --repo PATH         # pre-commit + pre-push shims (respects core.hooksPath)
$GG install  --template DIR      # shims for every new git init/clone; prints the
                                 # `git config --global init.templateDir DIR` to run (never runs it),
                                 # and warns if init.templateDir already points somewhere else
$GG install  --never-push PATH   # always-refuse pre-push, and PATH joins never_push
$GG check   [--repo PATH]        # dry run: guard, hooks, registration, SSH remote, noreply, user.email, remote; non-zero on a problem
$GG status                       # every registered and never-push repo with its state; non-zero on a problem
$GG hook pre-commit | hook pre-push <remote> <url>   # what the shims call
```

`install` is idempotent. A hook already there that is not a gitguard shim (a pre-commit-framework
hook, for example) moves to `<hooks>/local/<hook>`. It keeps its basename, so a dispatcher that
reads `$(basename "$0")` still works, and a relative symlink is re-pointed so it still resolves.
The shim runs it after the guard passes: arguments are passed on, pre-push stdin is passed
through, and its exit code is returned. If both `<hook>` and `local/<hook>` already exist, install
A tool may rewrite the hook after an install (`pre-commit install -f`, husky). Re-running
`install` then keeps the hook it replaced as `local/<hook>.prev` (`.prev.1`, … if that exists),
chains the tool's new hook as `local/<hook>`, and restores the shim. That is the fix
`check` prints. A dispatcher that finds files relative to its own directory (husky v9's `_/h`)
does not survive the move: call the guard from that dispatcher instead.

`install-cleo.sh` does three things here:
- installs `--never-push` on every new vault;
- gitignores `.cleo/`, untracking it once if an older install tracked it. That change is
  committed only when `.gitignore` matched `HEAD` before the run and nothing else is staged;
  otherwise it stays staged and the installer says so;
- makes its commits with the repo's own identity, or the registered account's when gitguard
  knows the repo.

When the project already has a remote, it prints the `register` command.

**Tools can drop the shim without a word.** `pre-commit install -f`, husky's `prepare` script,
or anything else that rewrites `.git/hooks/` can replace it. `gitguard status` (in every
`/sleep`) reports such a repo as `✗ hooks`. Re-run `install --repo` and the tool's hook is
chained again.

## Known limits

- The pushed diff and the blobs are streamed, one line or one blob at a time. The commit list,
  its `(commit, path)` pairs and its messages are held in memory, which is fine for any
  realistic push. A first push of a huge history costs time, not correctness.
- Blobs over 1 MiB are not read for vault-note frontmatter. Their added lines still go through
  the denylist.
- A symlinked `private/` is recognised by its logical path only when the push runs from the
  vault root. Vaults made by the installer are on `never_push`, which is compared resolved, so
  they are covered either way.
- Tag objects' tagger emails are not checked. Annotated-tag messages are.

## A guardrail, not a security boundary

`git commit --no-verify` and `git push --no-verify` skip hooks. That is how git is designed, and
no hook can prevent it. Deleting a shim, or pushing from a clone without one, also gets past the
guard. The guard stops accidents. It does not stop someone who decides to bypass it.

**Cleo never uses `--no-verify`, and never tells a sub-agent or the partner to use it.** A
refusal is fixed at its cause: the identity, the remote, the registry, or (with a backup first)
the history. If the guard is wrong, the fix goes into the registry or into `gitguard.py`, with
a test.
