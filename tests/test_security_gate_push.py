from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from security_gate_push import (  # type: ignore
    PushUpdate,
    ZERO_SHA,
    _isolated_git_env,
    _local_advertised_commits,
    commits_for_updates,
    run,
)


def _git(repo: Path, *args: str) -> str:
    env = {
        **_isolated_git_env(),
        "GIT_AUTHOR_NAME": "Security Gate Test",
        "GIT_AUTHOR_EMAIL": "security-gate@example.invalid",
        "GIT_COMMITTER_NAME": "Security Gate Test",
        "GIT_COMMITTER_EMAIL": "security-gate@example.invalid",
    }
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
        env=env,
    ).stdout.strip()


def _init_repo(path: Path, content: str = "print('base')\n") -> str:
    path.mkdir()
    _git(path, "init", "-q")
    (path / "app.py").write_text(content, encoding="utf-8")
    _git(path, "add", "app.py")
    _git(path, "commit", "-q", "-m", "base")
    return _git(path, "rev-parse", "HEAD")


def _commit(repo: Path, content: str, message: str = "next") -> str:
    (repo / "app.py").write_text(content, encoding="utf-8")
    _git(repo, "add", "app.py")
    _git(repo, "commit", "-q", "-m", message)
    return _git(repo, "rev-parse", "HEAD")


def test_git_helper_isolates_outer_repository_local_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    outer = tmp_path / "outer"
    outer_head = _init_repo(outer, "print('outer')\n")
    clean_env = dict(os.environ)

    def outer_state() -> tuple[str, str, bytes, str, str]:
        def inspect(*args: str) -> str:
            return subprocess.run(
                ["git", "-C", str(outer), *args], check=True,
                capture_output=True, text=True, env=clean_env,
            ).stdout.strip()

        return (
            inspect("rev-parse", "HEAD"),
            inspect("branch", "--show-current"),
            (outer / ".git" / "index").read_bytes(),
            inspect("status", "--porcelain=v1"),
            inspect("show-ref"),
        )

    before = outer_state()
    monkeypatch.setenv("GIT_DIR", str(outer / ".git"))
    monkeypatch.setenv("GIT_WORK_TREE", str(outer))
    monkeypatch.setenv("GIT_INDEX_FILE", str(outer / ".git" / "index"))

    inner = tmp_path / "inner"
    inner_before = _init_repo(inner, "print('inner')\n")
    inner_head = _commit(inner, "print('inner advanced')\n", "inner advanced")
    update = PushUpdate("HEAD", inner_head, "refs/heads/main", inner_before)

    assert inner_head != inner_before
    assert inner_head != outer_head
    assert run(inner, [update], "", tmp_path / "report") == 0
    assert outer_state() == before


def test_git_helper_fails_closed_when_local_env_query_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_run = subprocess.run

    def fail_query(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        if args[0] == ["git", "rev-parse", "--local-env-vars"]:
            raise subprocess.CalledProcessError(128, args[0])
        return original_run(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(subprocess, "run", fail_query)
    with pytest.raises(subprocess.CalledProcessError):
        _git(tmp_path, "status")


def test_local_advertised_tips_are_unique_commits_only(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    commit = _init_repo(repo)
    blob = _git(repo, "hash-object", "-w", "app.py")
    unknown = "1" * 40

    assert _local_advertised_commits(
        repo, [unknown, commit, blob, commit, unknown],
    ) == [commit]


def test_annotated_tag_tip_peels_to_safe_new_branch_exclusion(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    remote = tmp_path / "remote.git"
    base = _init_repo(repo)
    _git(repo, "tag", "-a", "baseline", "-m", "advertised baseline", base)
    tag_object = _git(repo, "rev-parse", "refs/tags/baseline")
    assert tag_object != base
    assert _git(repo, "cat-file", "-t", tag_object) == "tag"
    assert _local_advertised_commits(repo, [tag_object]) == [tag_object]

    _git(tmp_path, "init", "--bare", "-q", str(remote))
    _git(repo, "push", "-q", str(remote), "refs/tags/baseline")
    tip = _commit(repo, "print('new branch tip')\n")
    update = PushUpdate("refs/heads/new", tip, "refs/heads/new", ZERO_SHA)

    assert commits_for_updates(repo, [update], str(remote)) == [tip]


def test_new_branch_all_unknown_remote_tips_are_conservatively_scanned(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    remote = tmp_path / "remote.git"
    local_tip = _init_repo(repo, "print('local clean')\n")
    _git(tmp_path, "init", "--bare", "-q", str(remote))

    other = tmp_path / "other"
    remote_tip = _init_repo(other, "print('remote only')\n")
    _git(other, "push", "-q", str(remote), f"{remote_tip}:refs/heads/main")

    update = PushUpdate("refs/heads/new", local_tip, "refs/heads/new", ZERO_SHA)
    assert commits_for_updates(repo, [update], str(remote)) == [local_tip]
    assert run(repo, [update], str(remote), tmp_path / "report") == 0


def test_new_branch_mixed_known_and_unknown_remote_tips(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    remote = tmp_path / "remote.git"
    base = _init_repo(repo)
    _git(tmp_path, "init", "--bare", "-q", str(remote))
    _git(repo, "push", "-q", str(remote), f"{base}:refs/heads/main")
    local_tip = _commit(repo, "print('local next')\n")

    other = tmp_path / "other"
    _git(tmp_path, "clone", "-q", str(remote), str(other))
    remote_only = _commit(other, "print('remote only')\n", "remote only")
    _git(other, "push", "-q", str(remote), f"{remote_only}:refs/heads/other")

    update = PushUpdate("refs/heads/new", local_tip, "refs/heads/new", ZERO_SHA)
    assert commits_for_updates(repo, [update], str(remote)) == [local_tip]


def test_new_branch_with_remote_advertising_no_refs_scans_history(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    remote = tmp_path / "empty.git"
    base = _init_repo(repo)
    tip = _commit(repo, "print('tip')\n")
    _git(tmp_path, "init", "--bare", "-q", str(remote))

    update = PushUpdate("refs/heads/new", tip, "refs/heads/new", ZERO_SHA)
    assert commits_for_updates(repo, [update], str(remote)) == [tip, base]


def test_existing_branch_does_not_query_remote_location(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    base = _init_repo(repo)
    tip = _commit(repo, "print('tip')\n")
    update = PushUpdate("refs/heads/main", tip, "refs/heads/main", base)

    assert commits_for_updates(repo, [update], "not-a-real-remote") == [tip]


def test_unknown_remote_tip_does_not_weaken_p0_blocking(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    remote = tmp_path / "remote.git"
    tip = _init_repo(repo, "password = 'actual-secret-value'\n")
    _git(tmp_path, "init", "--bare", "-q", str(remote))
    other = tmp_path / "other"
    remote_tip = _init_repo(other, "print('unknown remote')\n")
    _git(other, "push", "-q", str(remote), f"{remote_tip}:refs/heads/main")
    update = PushUpdate("refs/heads/new", tip, "refs/heads/new", ZERO_SHA)

    assert run(repo, [update], str(remote), tmp_path / "report") == 1


def test_advertised_tip_probe_propagates_git_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = tmp_path / "repo"
    _init_repo(repo)
    original_run = subprocess.run

    def fail_batch(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        command = args[0]
        if isinstance(command, list) and "cat-file" in command:
            raise subprocess.CalledProcessError(128, command)
        return original_run(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(subprocess, "run", fail_batch)
    with pytest.raises(subprocess.CalledProcessError):
        _local_advertised_commits(repo, ["1" * 40])
