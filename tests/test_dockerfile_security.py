from pathlib import Path


def test_dockerfile_declares_non_root_runtime_user():
    dockerfile = Path("Dockerfile").read_text(encoding="utf-8")

    assert "useradd --system" in dockerfile
    assert "trustforge" in dockerfile

    user_index = dockerfile.index("USER trustforge")
    cmd_index = dockerfile.index('CMD ["python", "-m", "trustforge.web"]')
    assert user_index < cmd_index
