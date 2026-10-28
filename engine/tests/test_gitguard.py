"""gitguard tests: throwaway repos, a local bare remote, fake HOME / CLEO_HOME / global git config."""
import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "gitguard.py"
SEED = SCRIPT.parents[1]
spec = importlib.util.spec_from_file_location("gitguard", SCRIPT)
gitguard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gitguard)

EMAIL = "1+acme@users.noreply.github.com"
OLD = "old@example.com"
VAULT_NOTE = "---\nname: n\nfires_when: always\nsources: [raw:abc#L1-L2]\n---\nbody\n"
SESSION_FIRST = "---\nname: n\nfires_when: topic:x\nsources:\n  - private/sessions/2026-10-01-x.md\n  - pending:abc\n---\n"
DOC_EXAMPLE = "# Spec\n\n```yaml\nfires_when: always\nsources: [raw:<sid>#L1-L2]\n```\n"
REWRITE_WORDS = ("rebase", "--exec", "--reset-author", "filter-branch")


def run(*cmd, cwd=None, input=None, env=None, check=True):
    proc = subprocess.run([str(c) for c in cmd], cwd=cwd, input=input, env=env, capture_output=True, text=True)
    if check and proc.returncode != 0:
        raise AssertionError(f"{cmd} -> {proc.returncode}\n{proc.stdout}\n{proc.stderr}")
    return proc


def git(repo, *args, **kw):
    return run("git", "-C", repo, *args, **kw)


def gg(*args, **kw):
    return run(sys.executable, SCRIPT, *args, **kw)


def commit(repo, name, text="x\n", email=EMAIL, force=False, no_verify=False, env=None, message=None):
    """Stage one file and commit it; email=None commits with whatever the repo config says."""
    path = Path(repo) / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    git(repo, "add", *(["-f"] if force else []), "--", name)
    ident = ["-c", f"user.email={email}", "-c", "user.name=acme"] if email else []
    return git(repo, *ident, "commit", "-q", *(["--no-verify"] if no_verify else []), "-m", message or f"add {name}",
               env=env, check=False)


def write_hook(path, body):
    path.write_text("#!/bin/sh\n" + body)
    path.chmod(0o755)


def fix_of(stderr):
    return next(line.split("fix: ", 1)[1] for line in stderr.splitlines() if line.startswith("  fix: "))


def refused_without_rewrite(res):
    assert res.returncode != 0 and "history rewrite needed" in res.stderr, res.stderr
    assert not any(word in res.stderr for word in REWRITE_WORDS), res.stderr
    return res


@pytest.fixture
def world(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    gitconfig = tmp_path / "gitconfig"
    gitconfig.write_text(f"[user]\n\temail = {OLD}\n\tname = Old\n[init]\n\tdefaultBranch = main\n")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(gitconfig))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("CLEO_HOME", str(tmp_path / "bus"))
    for var in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_AUTHOR_NAME", "GIT_AUTHOR_EMAIL",
                "GIT_COMMITTER_NAME", "GIT_COMMITTER_EMAIL", "MEMCTL_TRANSCRIPTS"):
        monkeypatch.delenv(var, raising=False)
    remote = tmp_path / "remote.git"
    run("git", "init", "-q", "--bare", remote)
    repo = tmp_path / "proj"
    run("git", "init", "-q", repo)
    git(repo, "remote", "add", "origin", remote)
    gg("account", "--name", "acme", "--email", EMAIL, "--ssh-host", "github.com-acme")
    return SimpleNamespace(tmp=tmp_path, repo=repo, remote=remote, bus=tmp_path / "bus",
                           hooks=repo / ".git" / "hooks")


def guard(w, visibility="public", *extra):
    gg("register", "--repo", w.repo, "--account", "acme", "--remote", w.remote, "--visibility", visibility, *extra)
    gg("install", "--repo", w.repo)


def push(w, *refs, repo=None):
    return git(repo or w.repo, "push", "-q", "origin", *refs, check=False)


def deny(w, *terms):
    (w.bus / "guard-denylist.txt").write_text("# client names\n\n" + "".join(t + "\n" for t in terms))


# --------------------------------------------------------------------------- registry + unit rules
def test_happy_path_public_push(world):
    guard(world)
    assert commit(world.repo, "README.md", "# proj\n").returncode == 0
    assert commit(world.repo, ".env.example", "KEY=\n").returncode == 0
    res = push(world, "main")
    assert res.returncode == 0, res.stderr
    assert git(world.remote, "log", "--format=%ae %ce", "main").stdout.split() == [EMAIL] * 4


def test_register_sets_local_identity_and_writes_registry(world):
    guard(world)
    assert git(world.repo, "config", "--local", "user.email").stdout.strip() == EMAIL
    assert git(world.repo, "config", "--local", "user.name").stdout.strip() == "acme"
    reg = json.loads((world.bus / "git-identities.json").read_text())
    assert reg["repos"][str(world.repo.resolve())] == {"account": "acme", "remote": str(world.remote),
                                                       "visibility": "public"}
    assert reg["accounts"]["acme"] == {"email": EMAIL, "user_name": "acme", "ssh_host": "github.com-acme"}
    assert (world.bus / "bin" / "gitguard.py").read_bytes() == SCRIPT.read_bytes()


def register(w, remote, visibility="public", account="acme", repo=None):
    return gg("register", "--repo", repo or w.repo, "--account", account, "--remote", remote,
              "--visibility", visibility, check=False)


def test_register_refusals(world):
    res = register(world, "git@github.com-other:acme/p.git")
    assert res.returncode == 1 and "git@github.com-acme:acme/p.git" in res.stderr
    res = register(world, "https://github.com/acme/p.git")  # https would push as whichever gh account is active
    assert res.returncode == 1 and "not an SSH URL" in res.stderr and "git@github.com-acme:acme/p.git" in res.stderr
    assert register(world, "nobody", account="nobody").returncode == 1
    gg("account", "--name", "plain", "--email", "me@example.com", "--ssh-host", "github.com-plain")
    res = register(world, "git@github.com-plain:plain/p.git", account="plain")
    assert res.returncode == 1 and "noreply" in res.stderr
    assert register(world, "git@github.com-plain:plain/p.git", "private", account="plain").returncode == 0
    assert register(world, "git@github.com-acme:acme/p.git").returncode == 0
    vault = world.tmp / "inst" / "private"
    run("git", "init", "-q", vault)
    gg("install", "--never-push", vault)
    res = register(world, world.remote, "private", repo=vault)
    assert res.returncode == 1 and "never registered" in res.stderr


def test_remote_urls_compare_by_meaning():
    n = gitguard.norm_url
    assert n("git@github.com-acme:acme/p.git") == n("ssh://git@github.com-acme/acme/p") == n("git@github.com-acme:acme/p/")
    assert n("git@github.com-acme:acme/p") != n("git@github.com-acme:acme/q")
    assert gitguard.ssh_host("git@github.com-acme:acme/p.git") == "github.com-acme"
    assert gitguard.ssh_host("ssh://git@h:22/x") == "h"
    assert gitguard.ssh_host("https://github.com/acme/p") is None and gitguard.ssh_host("/tmp/r.git") is None
    acct = {"ssh_host": "github.com-acme"}
    assert gitguard.ssh_url_for("https://github.com/acme/p", acct) == "git@github.com-acme:acme/p.git"
    for ok in ("git@github.com-acme:acme/p.git", "ssh://git@github.com-acme/acme/p.git", "/tmp/r.git", "file:///tmp/r"):
        assert gitguard.remote_problem(ok, acct) is None, ok
    for bad in ("https://github.com/acme/p", "git+ssh://github.com-acme/acme/p", "ssh+git://github.com-acme/acme/p",
                "git://github.com-acme/acme/p", "ext::ssh github.com-acme %S acme/p", "https::github.com/acme/p",
                "../relative.git", "git@github.com:acme/p.git", "github.com-acme::acme/p", "github.com-acme://acme/p"):
        assert gitguard.remote_problem(bad, acct), bad


def test_forbidden_path_classes():
    f = gitguard.forbidden
    assert f("a/private/b.md", False) == ("a/private/", f("a/private/b.md", False)[1], True)
    assert f("private/b.md", False)[2] is False  # an instance's own vault can never be waived
    assert f(".cleo/VERSION", True)[0] == ".cleo/" and f(".cleo/VERSION", False) is None
    assert f("deploy/.ENV.prod", False)[0] == "deploy/.ENV.prod" and f("id_ed25519.pub", False)
    assert f(".env.example", True) is None and f("src/privateer.py", True) is None and f("private", True) is None


def test_vault_note_is_judged_by_the_files_own_frontmatter():
    v = gitguard.reads_as_vault_note
    assert v(VAULT_NOTE) and v(SESSION_FIRST)
    assert v("﻿---\nfires_when: always\nsources: ['pending:abc']\n---\n")
    assert not v(DOC_EXAMPLE)  # a format example in the middle of a doc is not a note
    assert not v("---\nfires_when: always\n---\n") and not v("---\nsources: [raw:abc]\n---\n")
    assert v("---\nfires_when: always\nsources: [private/sessions/x.md]\n---\n")  # session-only provenance
    assert not v("---\nfires_when: always\nsources: [docs/guide.md]\n---\n")
    utf16 = VAULT_NOTE.encode("utf-16")
    assert v(gitguard.decode_text(utf16)) and v(gitguard.decode_text(utf16[2:]))  # with and without a BOM


# --------------------------------------------------------------------------- pre-push refusals
def test_unregistered_repo_is_refused_fail_closed(world):
    gg("install", "--repo", world.repo)
    commit(world.repo, "a.txt")
    res = push(world, "main")
    assert res.returncode != 0
    assert "not registered" in res.stderr and "register --repo" in res.stderr and "--account <acme>" in res.stderr
    assert git(world.remote, "rev-parse", "-q", "--verify", "main", check=False).returncode != 0


def test_wrong_remote_is_refused_and_the_registered_one_suggested(world):
    guard(world)
    other = world.tmp / "other.git"
    run("git", "init", "-q", "--bare", other)
    commit(world.repo, "a.txt")
    res = git(world.repo, "push", "-q", other, "main", check=False)
    assert res.returncode != 0 and "registered remote" in res.stderr
    assert fix_of(res.stderr) == f"git -C {world.repo.resolve()} push origin"


@pytest.mark.parametrize("who", ["author", "committer", "both"])
def test_foreign_identity_in_range_is_refused_with_its_commits(world, who):
    guard(world)
    commit(world.repo, "a.txt")
    if who == "author":
        (world.repo / "b.txt").write_text("b\n")
        git(world.repo, "add", "b.txt")
        git(world.repo, "commit", "-q", "--no-verify", "--author", f"Old <{OLD}>", "-m", "b")
    else:
        opts = {"committer": dict(env={**os.environ, "GIT_COMMITTER_EMAIL": OLD}), "both": dict(email=OLD)}[who]
        assert commit(world.repo, "b.txt", no_verify=True, **opts).returncode == 0
    bad = git(world.repo, "rev-parse", "HEAD").stdout.strip()[:12]
    res = refused_without_rewrite(push(world, "main"))
    assert f"commits by {OLD}, not {EMAIL}: {bad}" in res.stderr


@pytest.mark.parametrize("path", ["private/memory/note.md", "docs/private/x.md", ".env", ".env.local",
                                  "certs/server.pem", "tls.key", "id_rsa", "id_ed25519.pub", "db/.pgpass"])
def test_forbidden_paths_in_range_are_refused(world, path):
    commit(world.repo, "README.md")
    commit(world.repo, path, force=True)  # made before the guard existed: history from elsewhere
    bad = git(world.repo, "rev-parse", "HEAD").stdout.strip()[:12]
    guard(world)
    res = refused_without_rewrite(push(world, "main"))
    assert "BLOCKED push refs/heads/main" in res.stderr and bad in res.stderr


@pytest.mark.parametrize("visibility", ["public", "private"])
def test_cleo_machinery_is_refused_in_every_registered_repo(world, visibility):
    commit(world.repo, ".cleo/VERSION", "name: x\n", force=True)
    guard(world, visibility)
    assert "Cleo's machinery" in refused_without_rewrite(push(world, "main")).stderr


@pytest.mark.parametrize("visibility", ["public", "private"])
def test_vault_notes_in_range_are_refused_in_every_registered_repo(world, visibility):
    commit(world.repo, "notes/n.md", VAULT_NOTE)
    commit(world.repo, "notes/gone.md", SESSION_FIRST)
    git(world.repo, "rm", "-q", "notes/gone.md")
    git(world.repo, "commit", "-q", "-m", "drop it")  # deleted at the tip, still in the pushed history
    commit(world.repo, "notes/half.md", "fires_when: always\n")
    commit(world.repo, "docs/spec.md", DOC_EXAMPLE)
    guard(world, visibility)
    res = refused_without_rewrite(push(world, "main"))
    assert "notes/n.md reads as a Cleo vault note" in res.stderr and "notes/gone.md reads as" in res.stderr
    assert "half.md" not in res.stderr and "spec.md" not in res.stderr


def test_vault_note_in_a_commit_message_is_refused(world):
    commit(world.repo, "a.txt", message="note\n\n" + VAULT_NOTE)
    guard(world, "private")
    assert "commit message of" in refused_without_rewrite(push(world, "main")).stderr


def test_denylist_scans_files_commit_and_tag_messages_in_public_repos(world):
    deny(world, "AcmeSecretCo")
    commit(world.repo, "README.md")
    commit(world.repo, "old.md", "we work for acmesecretco\n")
    guard(world)
    res = refused_without_rewrite(push(world, "main"))
    assert "old.md has a denylisted term (line 3 of" in res.stderr
    assert "acmesecretco" not in res.stderr.lower()  # the term itself is never echoed
    git(world.repo, "reset", "-q", "--hard", "HEAD~1")
    commit(world.repo, "fine.md", message="ship ACMESECRETCO build")
    assert "a commit message has a denylisted term" in push(world, "main").stderr
    git(world.repo, "reset", "-q", "--hard", "HEAD~1")
    commit(world.repo, "fine.md")
    assert push(world, "main").returncode == 0
    git(world.repo, "tag", "-a", "v1", "-m", "release for AcmeSecretCo")
    assert "a tag message has a denylisted term" in refused_without_rewrite(push(world, "v1")).stderr


def test_denylist_reads_binary_looking_and_utf16_files(world):
    deny(world, "AcmeSecretCo")
    guard(world)
    assert commit(world.repo, ".gitattributes", "*.csv -diff\n").returncode == 0
    files = {"data.csv": "id,client\n1,AcmeSecretCo\n".encode(), "export.txt": "client: AcmeSecretCo\n".encode("utf-16"),
             "raw.txt": "client: AcmeSecretCo\n".encode("utf-16-le")}
    for name, data in files.items():
        (world.repo / name).write_bytes(data)
        git(world.repo, "add", name)
        res = git(world.repo, "commit", "-q", "-m", name, check=False)
        assert res.returncode != 0 and f"{name} adds a denylisted term" in res.stderr
        git(world.repo, "commit", "-q", "--no-verify", "-m", name)
    res = refused_without_rewrite(push(world, "main"))
    assert all(f"{name} has a denylisted term" in res.stderr for name in files)


def test_denylist_matches_file_names_without_echoing_them(world):
    deny(world, "AcmeSecretCo")
    guard(world)
    res = commit(world.repo, "reports/acmesecretco-q3.md", "fine\n")
    assert res.returncode != 0 and "a staged file name has a denylisted term" in res.stderr
    assert "acmesecretco" not in res.stderr.lower()
    git(world.repo, "commit", "-q", "--no-verify", "-m", "report")
    res = refused_without_rewrite(push(world, "main"))
    assert "a file name has a denylisted term" in res.stderr and "acmesecretco" not in res.stderr.lower()


def test_fix_lines_name_the_real_path(world):
    deny(world, "AcmeSecretCo")
    guard(world)
    res = commit(world.repo, 'we"ird.md', "AcmeSecretCo\n")
    assert res.returncode != 0 and fix_of(res.stderr).startswith("edit 'we\"ird.md' to remove it")


def test_public_repo_with_a_non_noreply_account_is_blocked_everywhere(world):
    gg("account", "--name", "plain", "--email", "me@example.com", "--ssh-host", "github.com-plain")
    register(world, world.remote, "private", account="plain")
    gg("install", "--repo", world.repo)
    assert commit(world.repo, "a.txt", email="me@example.com").returncode == 0
    (world.repo / ".cleo").mkdir()
    (world.repo / ".cleo" / "VERSION").write_text("name: proj\nvisibility: public\n")
    res = commit(world.repo, "b.txt", email="me@example.com")
    assert res.returncode != 0 and "noreply" in res.stderr
    assert "noreply" in push(world, "main").stderr
    res = gg("check", "--repo", world.repo, check=False)
    assert res.returncode == 1 and "✗ noreply" in res.stdout


def test_new_branch_checks_only_commits_not_on_the_registered_remote(world):
    commit(world.repo, "legacy.txt", email=OLD)
    git(world.repo, "push", "-q", "origin", "main")  # published before the guard existed
    guard(world)
    git(world.repo, "switch", "-q", "-c", "feat")
    commit(world.repo, "feat.txt")
    assert push(world, "feat").returncode == 0  # new ref: the legacy commit is on origin/main
    commit(world.repo, "more.txt")
    assert push(world, "feat").returncode == 0  # update: origin/feat..feat
    git(world.repo, "switch", "-q", "-c", "feat2")
    commit(world.repo, "bad.txt", email=OLD, no_verify=True)
    res = refused_without_rewrite(push(world, "feat2"))
    assert f"commits by {OLD}" in res.stderr and "legacy" not in res.stderr


def test_commits_only_on_another_remote_are_still_checked(world):
    backup = world.tmp / "backup.git"
    run("git", "init", "-q", "--bare", backup)
    git(world.repo, "remote", "add", "backup", backup)
    commit(world.repo, "private/n.md", force=True)
    git(world.repo, "push", "-q", "backup", "main")  # on backup/main, never on the registered remote
    guard(world)
    git(world.repo, "switch", "-q", "-c", "feat")
    commit(world.repo, "feat.txt")
    assert "private/ is a Cleo vault" in refused_without_rewrite(push(world, "feat")).stderr


def test_push_by_bare_url_trusts_no_tracking_refs(world):
    commit(world.repo, "legacy.txt", email=OLD)
    git(world.repo, "push", "-q", "origin", "main")
    guard(world)
    git(world.repo, "switch", "-q", "-c", "feat")
    commit(world.repo, "feat.txt")
    refused_without_rewrite(git(world.repo, "push", "-q", world.remote, "feat", check=False))
    assert push(world, "feat").returncode == 0


def test_a_remote_that_fetches_elsewhere_is_not_trusted(world):
    backup = world.tmp / "backup.git"
    run("git", "init", "-q", "--bare", backup)
    commit(world.repo, "README.md")
    commit(world.repo, "private/n.md", force=True)
    git(world.repo, "push", "-q", backup, "main")  # only the private mirror has it
    git(world.repo, "config", "remote.origin.url", backup)
    git(world.repo, "config", "remote.origin.pushurl", world.remote)
    git(world.repo, "fetch", "-q", "origin")  # origin/* now mirrors backup, not the registered remote
    guard(world)
    git(world.repo, "switch", "-q", "-c", "feat")
    commit(world.repo, "feat.txt")
    assert "private/ is a Cleo vault" in refused_without_rewrite(push(world, "feat")).stderr


def test_triangular_fetch_upstream_push_fork_checks_what_the_fork_lacks(world):
    upstream = world.tmp / "upstream.git"
    run("git", "init", "-q", "--bare", upstream)
    commit(world.repo, "README.md")
    git(world.repo, "push", "-q", world.remote, "main")
    commit(world.repo, "theirs.txt", email=OLD)  # someone else's commit: upstream has it, the fork does not
    git(world.repo, "push", "-q", upstream, "main")
    git(world.repo, "config", "remote.origin.url", upstream)
    git(world.repo, "config", "remote.origin.pushurl", world.remote)
    git(world.repo, "fetch", "-q", "origin")
    guard(world)
    assert "it fetches from" in gg("check", "--repo", world.repo).stdout
    git(world.repo, "switch", "-q", "-c", "feat")
    commit(world.repo, "feat.txt")
    assert f"commits by {OLD}" in refused_without_rewrite(push(world, "feat")).stderr


def test_mirror_and_remote_tracking_destinations_are_refused(world):
    guard(world)
    commit(world.repo, "a.txt")
    assert push(world, "main").returncode == 0
    res = git(world.repo, "push", "-q", "--mirror", "origin", check=False)
    assert res.returncode != 0 and "remote-tracking refs and the stash" in res.stderr
    assert push(world, "main:refs/remotes/x/main").returncode != 0


def test_root_commit_is_scanned_even_with_log_showroot_false(world):
    git(world.repo, "config", "log.showRoot", "false")
    commit(world.repo, "private/n.md", force=True)
    guard(world)
    assert "private/ is a Cleo vault" in refused_without_rewrite(push(world, "main")).stderr


def test_merge_commits_are_scanned_for_paths_and_content(world):
    deny(world, "AcmeSecretCo")
    guard(world)
    commit(world.repo, "a.txt", "a\n")
    git(world.repo, "switch", "-q", "-c", "side")
    commit(world.repo, "b.txt")
    git(world.repo, "switch", "-q", "main")
    commit(world.repo, "c.txt")
    assert push(world, "main", "side").returncode == 0
    git(world.repo, "merge", "-q", "--no-ff", "--no-commit", "side")
    (world.repo / "private").mkdir()
    (world.repo / "private" / "x.md").write_text("evil\n")
    (world.repo / "a.txt").write_text("a\nfor acmesecretco\n")
    git(world.repo, "add", "-f", "private/x.md", "a.txt")
    git(world.repo, "-c", f"user.email={EMAIL}", "commit", "-q", "--no-verify", "-m", "merge")
    res = refused_without_rewrite(push(world, "main"))
    assert "private/ is a Cleo vault" in res.stderr and "a.txt has a denylisted term" in res.stderr


def test_signed_commits_parse_with_log_showsignature(world):
    if not shutil.which("ssh-keygen"):
        pytest.skip("ssh-keygen not available")
    key = world.tmp / "signing"
    run("ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", key)
    for k, v in (("gpg.format", "ssh"), ("user.signingkey", f"{key}.pub"), ("commit.gpgsign", "true"),
                 ("log.showSignature", "true")):
        git(world.repo, "config", k, v)
    guard(world)
    assert commit(world.repo, "a.txt").returncode == 0
    res = push(world, "main")
    assert res.returncode == 0, res.stderr


def test_branch_deletion_is_not_checked_as_a_range(world):
    commit(world.repo, "legacy.txt", email=OLD)
    git(world.repo, "push", "-q", "origin", "main", "main:legacy")
    guard(world)
    assert push(world, "--delete", "legacy").returncode == 0


def test_hook_refusal_exits_nonzero_with_reason_and_fix(world):
    commit(world.repo, "a.txt")
    sha = git(world.repo, "rev-parse", "HEAD").stdout.strip()
    res = run(sys.executable, SCRIPT, "hook", "pre-push", "origin", world.remote, cwd=world.repo,
              input=f"refs/heads/main {sha} refs/heads/main {'0' * 40}\n", check=False)
    lines = res.stderr.splitlines()
    assert res.returncode == 1 and lines[0].startswith("gitguard: BLOCKED push of") and lines[1].startswith("  fix: ")


def test_missing_guard_fails_closed(world):
    guard(world)
    (world.bus / "bin" / "gitguard.py").unlink()
    res = commit(world.repo, "a.txt")
    assert res.returncode != 0 and "guard is missing" in res.stderr and "install --repo" in res.stderr


def test_old_python_fails_closed(world):
    guard(world)
    fake = world.tmp / "fakebin"
    fake.mkdir()
    write_hook(fake / "python3", "echo 'Python 3.8.10'; exit 1\n")
    res = commit(world.repo, "a.txt", env={**os.environ, "PATH": f"{fake}:{os.environ['PATH']}"})
    assert res.returncode != 0 and "needs python3 >= 3.9" in res.stderr


def test_baked_bus_path_is_quoted_and_used_when_cleo_home_is_unset(world, monkeypatch):
    bus = world.tmp / "odd bus $HOME 'q'"
    monkeypatch.setenv("CLEO_HOME", str(bus))
    gg("account", "--name", "acme", "--email", EMAIL, "--ssh-host", "github.com-acme")
    guard(world)
    env = {k: v for k, v in os.environ.items() if k != "CLEO_HOME"}
    assert commit(world.repo, "a.txt", env=env).returncode == 0
    assert commit(world.repo, "private/x.md", force=True, env=env).returncode != 0  # the guard did run


def test_linked_worktree_shares_the_registration(world):
    guard(world)
    commit(world.repo, "a.txt")
    wt = world.tmp / "wt"
    git(world.repo, "worktree", "add", "-q", "-b", "side", wt)
    assert commit(wt, "b.txt").returncode == 0
    assert push(world, "side", repo=wt).returncode == 0


def test_visibility_is_the_stricter_of_registry_and_version_file(world):
    deny(world, "AcmeSecretCo")
    guard(world, "private")
    assert commit(world.repo, "a.md", "AcmeSecretCo\n").returncode == 0  # a private repo: denylist off
    (world.repo / ".cleo").mkdir()
    (world.repo / ".cleo" / "VERSION").write_text("name: proj\nvisibility: public\n")
    assert "denylisted term" in commit(world.repo, "b.md", "AcmeSecretCo\n").stderr


def test_allow_paths_waive_path_rules_but_never_the_instances_vault(world):
    guard(world, "public", "--allow-path", "tests/fixtures/*", "--allow-path", "private/*")
    entry = json.loads((world.bus / "git-identities.json").read_text())["repos"][str(world.repo.resolve())]
    assert entry["allow_paths"] == ["private/*", "tests/fixtures/*"]
    assert commit(world.repo, "tests/fixtures/server.key", force=True).returncode == 0
    assert commit(world.repo, "private/x.md", force=True).returncode != 0
    git(world.repo, "reset", "-q")
    assert push(world, "main").returncode == 0


# --------------------------------------------------------------------------- pre-commit refusals
def test_pre_commit_refuses_force_added_paths_and_the_fix_works(world):
    guard(world)
    res = commit(world.repo, "private/n.md", force=True)
    assert res.returncode != 0 and "private/ is a Cleo vault" in res.stderr
    run("sh", "-c", fix_of(res.stderr))
    assert commit(world.repo, "a.txt").returncode == 0
    assert git(world.repo, "ls-files", "private").stdout == ""


def test_pre_commit_refuses_wrong_local_email(world):
    guard(world)
    git(world.repo, "config", "user.email", OLD)
    res = commit(world.repo, "a.txt", email=None)
    assert res.returncode != 0 and OLD in res.stderr and f"config user.email {EMAIL}" in res.stderr


@pytest.mark.parametrize("visibility", ["public", "private"])
def test_pre_commit_refuses_vault_notes_in_every_registered_repo(world, visibility):
    guard(world, visibility)
    for name, text in (("notes/n.md", VAULT_NOTE), ("notes/s.md", SESSION_FIRST)):
        res = commit(world.repo, name, text)
        assert res.returncode != 0 and f"{name} reads as a Cleo vault note" in res.stderr
        git(world.repo, "reset", "-q")
    assert commit(world.repo, "docs/spec.md", DOC_EXAMPLE).returncode == 0


# --------------------------------------------------------------------------- install
def test_existing_hooks_are_chained_with_basename_exit_codes_and_stdin(world):
    ran, pushed = world.tmp / "pre-commit-ran", world.tmp / "pre-push-stdin"
    write_hook(world.hooks / "pre-commit", f'basename "$0" > {ran}\n')
    write_hook(world.hooks / "pre-push", f"cat > {pushed}\n")
    guard(world)
    assert gitguard.is_shim(world.hooks / "pre-commit") and (world.hooks / "local" / "pre-commit").exists()
    assert commit(world.repo, "a.txt").returncode == 0 and ran.read_text() == "pre-commit\n"
    assert push(world, "main").returncode == 0 and "refs/heads/main" in pushed.read_text()
    ran.unlink()
    assert commit(world.repo, "private/x.md", force=True).returncode != 0 and not ran.exists()
    git(world.repo, "reset", "-q")
    write_hook(world.hooks / "local" / "pre-commit", "exit 3\n")
    assert run(world.hooks / "pre-commit", cwd=world.repo, check=False).returncode == 3
    write_hook(world.hooks / "local" / "pre-push", "cat >/dev/null; exit 4\n")
    assert run(world.hooks / "pre-push", "origin", world.remote, cwd=world.repo, input="",
               check=False).returncode == 4


def test_relative_symlink_hook_still_resolves_after_the_move(world):
    ran = world.tmp / "ran"
    write_hook(world.hooks / "real-hook", f"touch {ran}\n")
    (world.hooks / "pre-commit").symlink_to("real-hook")
    guard(world)
    assert (world.hooks / "local" / "pre-commit").is_symlink()
    assert commit(world.repo, "a.txt").returncode == 0 and ran.exists()


def test_install_is_idempotent(world):
    write_hook(world.hooks / "pre-commit", "exit 0\n")
    gg("install", "--repo", world.repo)
    hooks = lambda: {p.relative_to(world.hooks).as_posix(): p.read_text() for p in world.hooks.rglob("*")
                     if p.is_file() and not p.name.endswith(".sample")}
    first = hooks()
    out = gg("install", "--repo", world.repo).stdout
    assert out.count("up to date") == 3 and hooks() == first
    assert sorted(first) == ["local/pre-commit", "pre-commit", "pre-push"]
    assert first["local/pre-commit"] == "#!/bin/sh\nexit 0\n"


def test_reinstall_recovers_after_a_tool_rewrites_the_hook(world):
    old_ran, new_ran = world.tmp / "old-ran", world.tmp / "new-ran"
    write_hook(world.hooks / "pre-commit", f"touch {old_ran}\n")
    guard(world)
    write_hook(world.hooks / "pre-commit", f"touch {new_ran}\n")  # what `pre-commit install -f` does
    res = gg("check", "--repo", world.repo, check=False)
    assert res.returncode == 1 and "✗ hooks" in res.stdout
    run("sh", "-c", next(line.split("fix: ", 1)[1] for line in res.stdout.splitlines() if "✗ hooks" in line))
    assert gg("check", "--repo", world.repo).returncode == 0
    assert (world.hooks / "local" / "pre-commit.prev").read_text() == f"#!/bin/sh\ntouch {old_ran}\n"
    assert commit(world.repo, "a.txt").returncode == 0 and new_ran.exists() and not old_ran.exists()


def test_core_hooks_path_is_respected(world):
    git(world.repo, "config", "core.hooksPath", ".githooks")
    out = gg("install", "--repo", world.repo).stdout
    assert gitguard.is_shim(world.repo / ".githooks" / "pre-push") and not (world.hooks / "pre-push").exists()
    assert "not ignored" in out


def test_template_gives_new_repos_the_shims_and_warns_before_replacing_one(world):
    tpl = (world.tmp / "tpl").resolve()
    out = gg("install", "--template", tpl).stdout
    assert f"git config --global init.templateDir {tpl}" in out and "already" not in out
    new = world.tmp / "new"
    run("git", "init", "-q", "--template", tpl, new)
    assert all(gitguard.is_shim(new / ".git" / "hooks" / h) for h in gitguard.HOOKS)
    run("git", "config", "--global", "init.templateDir", "/elsewhere/tpl")
    assert "! init.templateDir is already /elsewhere/tpl" in gg("install", "--template", tpl).stdout
    run("git", "config", "--global", "init.templateDir", tpl)
    assert "already points here" in gg("install", "--template", tpl).stdout


# --------------------------------------------------------------------------- local-only repos
def test_never_push_refuses_every_push(world):
    vault = world.tmp / "inst" / "private"
    run("git", "init", "-q", vault)
    commit(vault, "memory/n.md", VAULT_NOTE)
    git(vault, "remote", "add", "origin", world.remote)
    gg("install", "--never-push", vault)
    res = push(world, "main", repo=vault)
    assert res.returncode != 0 and "never leaves this machine" in res.stderr and "remote remove origin" in res.stderr
    res = git(vault, "push", "-q", world.remote, "main", check=False)  # by bare URL: there is no remote to drop
    assert res.returncode != 0 and fix_of(res.stderr) == "do not push it"
    assert json.loads((world.bus / "git-identities.json").read_text())["never_push"] == [str(vault.resolve())]
    assert "never-push:" not in gg("install", "--never-push", vault).stdout  # unchanged: nothing new to say
    assert gg("check", "--repo", vault, check=False).returncode == 1  # a vault must not even have a remote
    git(vault, "remote", "remove", "origin")
    assert gg("check", "--repo", vault).returncode == 0


@pytest.mark.parametrize("kind", ["vault", "bus"])
def test_vaults_and_the_bus_are_refused_even_when_unlisted(world, kind):
    if kind == "vault":
        target = world.tmp / "inst" / "private"
        (world.tmp / "inst" / ".cleo").mkdir(parents=True)
        (world.tmp / "inst" / ".cleo" / "VERSION").write_text("name: inst\n")
    else:
        target = world.bus
    run("git", "init", "-q", target)
    gg("install", "--repo", target)  # the ordinary shims, as the git template would give
    assert commit(target, "n.md", VAULT_NOTE).returncode == 0  # commits stay free
    git(target, "remote", "add", "origin", world.remote)
    res = push(world, "main", repo=target)
    assert res.returncode != 0 and ("a Cleo vault" if kind == "vault" else "the Cleo bus") in res.stderr
    res = git(target, "push", "-q", world.remote, "main", check=False)
    assert res.returncode != 0 and fix_of(res.stderr) == "do not push it"


def test_symlinked_vault_is_recognised_by_its_logical_path(world):
    inst, real = world.tmp / "inst", world.tmp / "elsewhere" / "vault"
    (inst / ".cleo").mkdir(parents=True)
    (inst / ".cleo" / "VERSION").write_text("name: inst\n")
    run("git", "init", "-q", real)
    (inst / "private").symlink_to(real)
    res = register(world, world.remote, "private", repo=inst / "private")
    assert res.returncode == 1 and "a Cleo vault" in res.stderr
    gg("install", "--repo", inst / "private")
    commit(real, "n.md")
    git(real, "remote", "add", "origin", world.remote)
    res = run("git", "push", "-q", "origin", "main", cwd=inst / "private",
              env={**os.environ, "PWD": str(inst / "private")}, check=False)
    assert res.returncode != 0 and "a Cleo vault" in res.stderr


# --------------------------------------------------------------------------- check / status
def test_check_and_status_report_problems(world):
    res = gg("check", "--repo", world.repo, check=False)
    assert res.returncode == 1 and "✗ registered" in res.stdout and "✗ hooks" in res.stdout
    guard(world)
    res = gg("check", "--repo", world.repo)
    assert "✓ user.email" in res.stdout and "✓ remote: origin" in res.stdout
    git(world.repo, "config", "user.email", OLD)
    res = gg("check", "--repo", world.repo, check=False)
    assert res.returncode == 1 and "✗ user.email" in res.stdout
    res = gg("status", check=False)
    assert res.returncode == 1 and str(world.repo.resolve()) in res.stdout and "✗ user.email" in res.stdout
    git(world.repo, "remote", "set-url", "origin", world.tmp / "elsewhere.git")
    res = gg("check", "--repo", world.repo, check=False)
    assert f"remote add github {world.remote}" in res.stdout and "set-url" not in res.stdout
    reg_path = world.bus / "git-identities.json"
    reg = json.loads(reg_path.read_text())
    reg["repos"][str(world.repo.resolve())]["remote"] = "https://github.com/acme/proj.git"
    reg_path.write_text(json.dumps(reg))
    res = gg("check", "--repo", world.repo, check=False)
    assert "✗ ssh" in res.stdout and "git@github.com-acme:acme/proj.git" in res.stdout


# --------------------------------------------------------------------------- the installer
def install_cleo(target):
    if not (SEED / "install-cleo.sh").is_file():
        pytest.skip("vendored engine copy; the installer lives in the seed")
    (target / "venv" / "bin").mkdir(parents=True, exist_ok=True)  # an existing venv: no network in tests
    if not (target / "venv" / "bin" / "python").exists():
        (target / "venv" / "bin" / "python").symlink_to(sys.executable)
    return run("bash", SEED / "install-cleo.sh", target, "--name", "Inst", check=False)


def test_installer_initial_commit_uses_the_configured_identity(world):
    target = world.tmp / "fresh"
    res = install_cleo(target)
    assert res.returncode == 0, res.stdout + res.stderr
    assert git(target, "log", "--format=%ae").stdout.split() == [OLD]
    assert git(target, "ls-files", ".cleo").stdout == "" and (target / ".cleo" / "VERSION").is_file()
    assert gitguard.is_shim(target / "private" / ".git" / "hooks" / "pre-push")


@pytest.mark.parametrize("edited", [False, True])
def test_installer_untracks_cleo_and_commits_as_the_registered_account(world, edited):
    target = world.repo
    (target / ".gitignore").write_text("venv/\n!.cleo/\n")
    git(target, "add", ".gitignore")
    commit(target, ".cleo/VERSION", "name: Inst\n", email=OLD)  # an instance from before .cleo/ went local-only
    if edited:  # the partner's own unfinished .gitignore edit must never ride along in Cleo's commit
        (target / ".gitignore").write_text("venv/\n!.cleo/\nmine/\n")
    register(world, world.remote, "private")
    git(target, "config", "--unset", "user.email")
    res = install_cleo(target)
    assert res.returncode == 0, res.stdout + res.stderr
    assert git(target, "ls-files", ".cleo").stdout == "" and (target / ".cleo" / "VERSION").is_file()
    ignore = (target / ".gitignore").read_text().splitlines()
    assert ".cleo/" in ignore and "!.cleo/" not in ignore
    last = git(target, "log", "-1", "--format=%ae %s").stdout
    if edited:
        assert last.startswith(f"{OLD} add .cleo/VERSION") and "staged, not committed" in res.stdout
        assert git(target, "diff", "--cached", "--name-only").stdout.split() == [".cleo/VERSION"]
        assert "mine/" in (target / ".gitignore").read_text()
    else:
        assert last.startswith(f"{EMAIL} Untrack .cleo/")
        assert git(target, "status", "--porcelain", "--", ".cleo", ".gitignore").stdout == ""
    assert "register --repo" in res.stdout
