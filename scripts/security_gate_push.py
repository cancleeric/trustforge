"""Scan the exact Git commits transferred by a pre-push hook.

Git passes ref-update tuples on stdin as:
``local_ref local_sha remote_ref remote_sha``.  Scanning the checked-out
working tree is insufficient because callers may push another ref or a range
whose earlier commits contain a secret.  This helper archives and scans every
new commit before the push is allowed to continue.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tarfile
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Iterable, TextIO

from security_gate import ScanResult, scan, write_report


ZERO_SHA = "0" * 40


@dataclass(frozen=True)
class PushUpdate:
    local_ref: str
    local_sha: str
    remote_ref: str
    remote_sha: str


def parse_updates(lines: Iterable[str]) -> list[PushUpdate]:
    updates: list[PushUpdate] = []
    for line in lines:
        fields = line.split()
        if not fields:
            continue
        if len(fields) != 4:
            raise ValueError(f"invalid pre-push ref update: {line.rstrip()!r}")
        updates.append(PushUpdate(*fields))
    return updates


def _git(repo: Path, *args: str) -> str:
    env = _isolated_git_env()
    completed = subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )
    return completed.stdout.strip()


def _isolated_git_env() -> dict[str, str]:
    parent_env = dict(os.environ)
    local_env_vars = subprocess.run(
        ["git", "rev-parse", "--local-env-vars"],
        check=True,
        capture_output=True,
        text=True,
        env=parent_env,
    ).stdout.splitlines()
    for key in local_env_vars:
        parent_env.pop(key, None)
    return parent_env


def _local_advertised_commits(repo: Path, advertised: Iterable[str]) -> list[str]:
    """Return unique advertised tips that locally peel to commits.

    ``cat-file --batch-check`` reports an unknown or non-commit ``^{commit}``
    expression as ``missing`` without turning that expected probe result into a
    Git command failure.  A failure of the batch command itself still raises.
    """
    unique = list(dict.fromkeys(advertised))
    if not unique:
        return []
    expressions = [f"{tip}^{{commit}}" for tip in unique]
    completed = subprocess.run(
        ["git", "-C", str(repo), "cat-file", "--batch-check=%(objectname) %(objecttype)"],
        input="\n".join(expressions) + "\n",
        check=True,
        capture_output=True,
        text=True,
        env=_isolated_git_env(),
    )
    resolved: list[str] = []
    for tip, line in zip(unique, completed.stdout.splitlines(), strict=True):
        fields = line.split()
        if len(fields) == 2 and fields[1] == "commit":
            resolved.append(tip)
        elif fields[-1:] != ["missing"]:
            raise ValueError(f"unexpected git cat-file response: {line!r}")
    return resolved


def commits_for_updates(
    repo: Path,
    updates: Iterable[PushUpdate],
    remote_location: str,
) -> list[str]:
    commits: list[str] = []
    seen: set[str] = set()
    advertised: list[str] | None = None
    for update in updates:
        if update.local_sha == ZERO_SHA:  # deleted ref
            continue
        if update.remote_sha == ZERO_SHA:
            args = ["rev-list", update.local_sha]
            if remote_location:
                if advertised is None:
                    output = _git(repo, "ls-remote", "--refs", remote_location)
                    remote_tips = [line.split()[0] for line in output.splitlines() if line]
                    advertised = _local_advertised_commits(repo, remote_tips)
                if advertised:
                    args.extend(["--not", *advertised])
        else:
            args = ["rev-list", f"{update.remote_sha}..{update.local_sha}"]
        discovered = _git(repo, *args).splitlines()
        # A new tag can point at a commit already reachable from the remote.
        # Scan its target at least once even when the range is empty.
        if not discovered:
            discovered = [update.local_sha]
        for commit in discovered:
            if commit not in seen:
                seen.add(commit)
                commits.append(commit)
    return commits


def _safe_members(
    archive: tarfile.TarFile,
) -> tuple[list[tarfile.TarInfo], list[tarfile.TarInfo]]:
    members: list[tarfile.TarInfo] = []
    symlinks: list[tarfile.TarInfo] = []
    for member in archive.getmembers():
        path = PurePosixPath(member.name)
        if path.is_absolute() or ".." in path.parts:
            raise ValueError(f"unsafe path in git archive: {member.name!r}")
        if member.islnk():
            raise ValueError(f"unexpected hard link in git archive: {member.name!r}")
        if member.issym():
            symlinks.append(member)
            continue
        members.append(member)
    return members, symlinks


def scan_commit(repo: Path, commit: str, out_dir: Path) -> ScanResult:
    with tempfile.TemporaryDirectory(prefix="trustforge-security-push-") as raw_tmp:
        tmp = Path(raw_tmp)
        archive_path = tmp / "commit.tar"
        with archive_path.open("wb") as archive_file:
            subprocess.run(
                ["git", "-C", str(repo), "archive", "--format=tar", commit],
                check=True,
                stdout=archive_file,
                env=_isolated_git_env(),
            )
        tree = tmp / "tree"
        tree.mkdir()
        with tarfile.open(archive_path, "r") as archive:
            members, symlinks = _safe_members(archive)
            archive.extractall(tree, members=members)
        # A Git symlink's tracked blob is its target text.  Materialize that
        # text as a regular file for scanning; never create or follow the link.
        for symlink in symlinks:
            link_blob = tree / PurePosixPath(symlink.name)
            link_blob.parent.mkdir(parents=True, exist_ok=True)
            link_blob.write_text(symlink.linkname, encoding="utf-8")
        result = scan(tree)
        write_report(result, out_dir / commit)
        return result


def run(
    repo: Path,
    updates: list[PushUpdate],
    remote_location: str,
    out_dir: Path,
) -> int:
    if not updates:
        head = _git(repo, "rev-parse", "HEAD")
        # Direct/manual hook execution has no ref tuples.  Represent an empty
        # range so commits_for_updates falls back to scanning HEAD exactly once.
        updates = [PushUpdate("HEAD", head, "HEAD", head)]
        remote_location = ""

    commits = commits_for_updates(repo, updates, remote_location)
    for commit in commits:
        result = scan_commit(repo, commit, out_dir)
        print(
            f"[security-push] {commit[:12]}: "
            f"P0={result.p0_count}, P1={result.p1_count}, P2={result.p2_count}",
        )
        if result.p0_count:
            for finding in result.findings:
                if finding.severity == "P0":
                    print(
                        f"[security-push] BLOCK {finding.file}:{finding.line} "
                        f"({finding.pattern_name})",
                        file=sys.stderr,
                    )
            return 1
    return 0


def main(argv: list[str] | None = None, stdin: TextIO = sys.stdin) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument("--remote-location", default="")
    parser.add_argument("--out", type=Path, default=Path("out/pre-push/security-gate-push"))
    args = parser.parse_args(argv)
    try:
        updates = parse_updates(stdin)
        return run(args.repo.resolve(), updates, args.remote_location, args.out)
    except (OSError, ValueError, subprocess.CalledProcessError) as exc:
        print(f"[security-push] FAILED: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
