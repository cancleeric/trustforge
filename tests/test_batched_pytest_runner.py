"""Behaviour contract for the pre-push backend gate runner.

The gate's guarantee is per-file process isolation; the runner is allowed to put
several of those processes in flight at once but must not weaken the isolation,
lose a failure, or let a `serial`-listed file share the host with a neighbour.
"""
from __future__ import annotations

import importlib.util
import json
import sys
import textwrap
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]


def _load_runner():
    path = _ROOT / "scripts" / "run_batched_pytest.py"
    spec = importlib.util.spec_from_file_location("run_batched_pytest", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


runner = _load_runner()


def _fake_repo(tmp_path: Path, files: dict[str, str]) -> Path:
    tests = tmp_path / "tests"
    tests.mkdir()
    for name, body in files.items():
        (tests / name).write_text(textwrap.dedent(body), encoding="utf-8")
    return tmp_path


def _timestamped_test(marker_dir: Path, name: str, seconds: float) -> str:
    """A test file that records the wall-clock window it occupied."""
    return f"""
        import json, time
        from pathlib import Path

        def test_records_window():
            start = time.time()
            time.sleep({seconds})
            Path({str(marker_dir)!r}).mkdir(parents=True, exist_ok=True)
            Path({str(marker_dir)!r}, {name!r}).write_text(
                json.dumps([start, time.time()])
            )
    """


def _run(root: Path, *, workers: int) -> int:
    return runner.main(["--chunk-size", "1", "--workers", str(workers), "--root", str(root)])


def _windows(marker_dir: Path) -> list[tuple[float, float]]:
    return [tuple(json.loads(p.read_text())) for p in sorted(marker_dir.glob("*"))]


def _overlaps(windows: list[tuple[float, float]]) -> bool:
    ordered = sorted(windows)
    return any(b[0] < a[1] for a, b in zip(ordered, ordered[1:]))


# --- isolation is preserved -------------------------------------------------


def test_each_file_still_runs_in_its_own_process(tmp_path: Path) -> None:
    """The isolation guarantee: one file per pytest process, never merged."""
    root = _fake_repo(
        tmp_path,
        {
            "test_a.py": """
                import os
                from pathlib import Path

                def test_pid():
                    Path(os.environ["PID_DIR"], f"a-{os.getpid()}").write_text("x")
            """,
            "test_b.py": """
                import os
                from pathlib import Path

                def test_pid():
                    Path(os.environ["PID_DIR"], f"b-{os.getpid()}").write_text("x")
            """,
        },
    )
    pid_dir = tmp_path / "pids"
    pid_dir.mkdir()
    import os

    os.environ["PID_DIR"] = str(pid_dir)
    try:
        assert _run(root, workers=2) == 0
    finally:
        del os.environ["PID_DIR"]

    pids = {p.name.split("-", 1)[1] for p in pid_dir.iterdir()}
    assert len(list(pid_dir.iterdir())) == 2
    assert len(pids) == 2, "two test files must not share one pytest process"


def test_pytest_command_keeps_the_isolation_flags() -> None:
    command = runner._pytest_command([Path("tests/test_x.py")])
    # A shared addopts or the pytest-cov plugin would reintroduce exactly the
    # process-global state the per-file gate exists to avoid.
    assert "--parallel-mode" in command
    assert command[command.index("-p") + 1] == "no:cov"
    assert command[command.index("--override-ini") + 1] == "addopts="


# --- concurrency actually happens ------------------------------------------


def test_workers_greater_than_one_overlap_batches(tmp_path: Path) -> None:
    markers = tmp_path / "markers"
    root = _fake_repo(
        tmp_path,
        {
            "test_one.py": _timestamped_test(markers, "one", 0.4),
            "test_two.py": _timestamped_test(markers, "two", 0.4),
        },
    )
    assert _run(root, workers=2) == 0
    assert _overlaps(_windows(markers))


def test_single_worker_keeps_batches_sequential(tmp_path: Path) -> None:
    markers = tmp_path / "markers"
    root = _fake_repo(
        tmp_path,
        {
            "test_one.py": _timestamped_test(markers, "one", 0.3),
            "test_two.py": _timestamped_test(markers, "two", 0.3),
        },
    )
    assert _run(root, workers=1) == 0
    assert not _overlaps(_windows(markers))


# --- failures are never swallowed ------------------------------------------


def test_a_failing_batch_fails_the_gate(tmp_path: Path) -> None:
    root = _fake_repo(
        tmp_path,
        {
            "test_ok.py": "def test_ok():\n    assert True\n",
            "test_bad.py": "def test_bad():\n    assert False\n",
        },
    )
    assert _run(root, workers=4) != 0


def test_failure_is_reported_even_when_it_finishes_last(tmp_path: Path) -> None:
    root = _fake_repo(
        tmp_path,
        {
            "test_aaa_fast.py": "def test_ok():\n    assert True\n",
            "test_zzz_slow.py": (
                "import time\n\ndef test_bad():\n    time.sleep(0.3)\n    assert False\n"
            ),
        },
    )
    assert _run(root, workers=4) != 0


def test_empty_batch_does_not_fail_the_gate(tmp_path: Path) -> None:
    """pytest exit code 5 means 'nothing collected', which is not a failure."""
    root = _fake_repo(
        tmp_path,
        {
            "test_empty.py": "# no tests here\n",
            "test_ok.py": "def test_ok():\n    assert True\n",
        },
    )
    assert _run(root, workers=2) == 0


def test_missing_tests_directory_is_an_error(tmp_path: Path) -> None:
    (tmp_path / "tests").mkdir()
    assert runner.main(["--chunk-size", "1", "--workers", "2", "--root", str(tmp_path)]) == 2


# --- the serial opt-out -----------------------------------------------------


def test_serial_listed_file_never_shares_the_host(tmp_path: Path) -> None:
    markers = tmp_path / "markers"
    root = _fake_repo(
        tmp_path,
        {
            "test_one.py": _timestamped_test(markers, "one", 0.4),
            "test_two.py": _timestamped_test(markers, "two", 0.4),
            "test_lonely.py": _timestamped_test(markers, "lonely", 0.4),
        },
    )
    (root / "tests" / "serial_batches.txt").write_text(
        "# files that cannot share the host\ntests/test_lonely.py\n", encoding="utf-8"
    )
    assert _run(root, workers=3) == 0

    windows = {p.name: tuple(json.loads(p.read_text())) for p in markers.iterdir()}
    lonely = windows.pop("lonely")
    for name, window in windows.items():
        assert not (window[0] < lonely[1] and lonely[0] < window[1]), (
            f"{name} overlapped the serial batch"
        )


def test_serial_list_ignores_comments_and_blank_lines(tmp_path: Path) -> None:
    listing = tmp_path / "tests"
    listing.mkdir()
    (listing / "serial_batches.txt").write_text(
        "\n# a comment\ntests/test_a.py  # trailing\n\n   \ntests/test_b.py\n",
        encoding="utf-8",
    )
    assert runner._serial_names(tmp_path) == {"tests/test_a.py", "tests/test_b.py"}


def test_missing_serial_list_means_everything_may_run_concurrently(tmp_path: Path) -> None:
    assert runner._serial_names(tmp_path) == set()


# --- argument validation ----------------------------------------------------


@pytest.mark.parametrize("argv", [["--chunk-size", "0"], ["--workers", "-1"]])
def test_invalid_arguments_are_rejected(argv: list[str]) -> None:
    with pytest.raises(SystemExit):
        runner.main(argv)


def test_chunking_covers_every_file_exactly_once() -> None:
    files = [Path(f"tests/test_{i}.py") for i in range(7)]
    batches = runner._chunk(files, 3)
    assert [len(b) for b in batches] == [3, 3, 1]
    assert [f for batch in batches for f in batch] == files
