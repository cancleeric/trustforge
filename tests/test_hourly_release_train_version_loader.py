from pathlib import Path


def test_release_train_loads_version_tools_by_file_path():
    train = (
        Path(__file__).resolve().parent.parent / "scripts" / "hourly_release_train.py"
    ).read_text(encoding="utf-8")
    assert 'runpy.run_path(str(ROOT / "scripts" / "release_version.py"))' in train
    assert "from scripts.release_version import" not in train
