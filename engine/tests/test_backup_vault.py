"""backup_vault.sh tests: a fake instance in tmp_path, CLEO_BACKUP_DIR pointed at tmp (never the real one)."""
import fcntl
import os
import shutil
import subprocess
import tarfile
import time
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "backup_vault.sh"
DAY = 86400


def instance(tmp_path, folder="proj", name="Acme HQ"):
    """<folder>/.cleo/engine/backup_vault.sh + .cleo/VERSION (name: …) + a small private/ vault."""
    root = tmp_path / folder
    (root / ".cleo" / "engine").mkdir(parents=True)
    shutil.copy(SCRIPT, root / ".cleo" / "engine" / "backup_vault.sh")
    if name is not None:
        (root / ".cleo" / "VERSION").write_text(f"name: {name}\ninstalled: 2026-10-29\nrole: spoke\n")
    (root / "private" / "notes").mkdir(parents=True)
    (root / "private" / "notes" / "a.md").write_text("note\n")
    return root


def backup(root, dest, *args, cwd=None):
    """Run the vendored copy; by default as the SessionEnd hook does (instance root, relative path)."""
    script = ".cleo/engine/backup_vault.sh" if cwd is None else str(root / ".cleo" / "engine" / "backup_vault.sh")
    env = {**os.environ, "CLEO_BACKUP_DIR": str(dest)}
    return subprocess.run(["bash", script, *args], cwd=cwd or root, env=env, capture_output=True, text=True)


def snapshots(folder):
    return sorted(p.name for p in folder.glob("vault-*.tar.gz"))


def fake_snapshots(folder, count, age_days=1):
    """count dummy snapshots, the i-th aged (age_days + i) days; returns their names newest first."""
    folder.mkdir(parents=True, exist_ok=True)
    names = []
    for i in range(count):
        path = folder / f"vault-2020-01-{i + 1:02d}_0000.tar.gz"
        path.write_bytes(b"old")
        stamp = time.time() - (age_days + i) * DAY
        os.utime(path, (stamp, stamp))
        names.append(path.name)
    return names


@pytest.mark.parametrize("from_elsewhere", [False, True])
def test_writes_nonempty_archive_of_private(tmp_path, from_elsewhere):
    root, dest = instance(tmp_path), tmp_path / "backups"
    proc = backup(root, dest, cwd=tmp_path if from_elsewhere else None)
    assert proc.returncode == 0, proc.stderr
    sub = dest / "acme-hq"
    [name] = snapshots(sub)
    archive = sub / name
    assert archive.stat().st_size > 0
    with tarfile.open(archive) as tar:
        assert "private/notes/a.md" in tar.getnames()
    assert sorted(p.name for p in sub.iterdir()) == [name]  # no temp file left behind
    assert [p.name for p in dest.iterdir()] == ["acme-hq"]


def test_missing_private_fails_and_writes_nothing(tmp_path):
    root, dest = instance(tmp_path), tmp_path / "backups"
    shutil.rmtree(root / "private")
    proc = backup(root, dest)
    assert proc.returncode != 0
    assert "private" in proc.stderr
    assert not dest.exists() or not any(dest.rglob("*"))


@pytest.mark.skipif(os.geteuid() == 0, reason="root can read a chmod-000 file")
def test_tar_failure_leaves_no_archive_and_skips_rotation(tmp_path):
    root, dest = instance(tmp_path), tmp_path / "backups"
    sub = dest / "acme-hq"
    old = fake_snapshots(sub, 9)
    unreadable = root / "private" / "notes" / "locked.md"
    unreadable.write_text("x\n")
    unreadable.chmod(0)
    try:
        proc = backup(root, dest)
    finally:
        unreadable.chmod(0o644)
    assert proc.returncode != 0
    assert "tar failed" in proc.stderr
    assert sorted(p.name for p in sub.iterdir()) == sorted(old)  # nothing new, nothing rotated


@pytest.mark.parametrize("age_days, skipped", [(0, True), (10, False)])
def test_gate_reads_own_subfolder(tmp_path, age_days, skipped):
    root, dest = instance(tmp_path), tmp_path / "backups"
    [own] = fake_snapshots(dest / "acme-hq", 1, age_days=age_days)
    proc = backup(root, dest, "--if-older-than", "7")
    assert proc.returncode == 0, proc.stderr
    assert ("skipped" in proc.stdout) is skipped
    assert len(snapshots(dest / "acme-hq")) == (1 if skipped else 2)
    assert own in snapshots(dest / "acme-hq")


def test_gate_ignores_other_instances_and_legacy_files(tmp_path):
    root, dest = instance(tmp_path), tmp_path / "backups"
    fake_snapshots(dest / "other", 1, age_days=0)
    fake_snapshots(dest, 1, age_days=0)  # legacy flat layout
    proc = backup(root, dest, "--if-older-than", "7")
    assert proc.returncode == 0, proc.stderr
    assert "skipped" not in proc.stdout
    assert len(snapshots(dest / "acme-hq")) == 1


def test_rotation_keeps_eight_in_own_subfolder_only(tmp_path):
    root, dest = instance(tmp_path), tmp_path / "backups"
    own = fake_snapshots(dest / "acme-hq", 10)
    other = fake_snapshots(dest / "other", 10)
    legacy = fake_snapshots(dest, 10)
    proc = backup(root, dest)
    assert proc.returncode == 0, proc.stderr
    kept = snapshots(dest / "acme-hq")
    assert len(kept) == 8
    assert set(own[:7]) < set(kept)  # the new snapshot + the 7 newest old ones
    assert snapshots(dest / "other") == sorted(other)
    assert snapshots(dest) == sorted(legacy)


def test_slug_falls_back_to_folder_name(tmp_path):
    root, dest = instance(tmp_path, folder="My_Proj", name=None), tmp_path / "backups"
    proc = backup(root, dest)
    assert proc.returncode == 0, proc.stderr
    assert [p.name for p in dest.iterdir()] == ["my-proj"]
    assert len(snapshots(dest / "my-proj")) == 1


def test_waits_for_night_lock_then_backs_up(tmp_path, monkeypatch):
    """A running `night` holds private/.git/night.lock: the backup must not read the vault until it is free."""
    root, dest = instance(tmp_path), tmp_path / "backups"
    (root / "private" / ".git").mkdir()
    monkeypatch.setenv("CLEO_BACKUP_LOCK_WAIT", "1")
    with open(root / "private" / ".git" / "night.lock", "a") as held:
        fcntl.flock(held, fcntl.LOCK_EX)
        busy = backup(root, dest)
    assert busy.returncode != 0 and "busy" in busy.stderr
    assert not snapshots(dest / "acme-hq") and not list((dest / "acme-hq").glob(".vault-*"))
    free = backup(root, dest)
    assert free.returncode == 0, free.stderr
    assert len(snapshots(dest / "acme-hq")) == 1
