"""`scripts/init_env.py`: the one command that creates `.env`."""

import importlib.util
import shutil
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("init_env", ROOT / "scripts" / "init_env.py")
init_env = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(init_env)


def _values(text: str) -> dict[str, str]:
    lines = (line for line in text.splitlines() if "=" in line and not line.startswith("#"))
    return dict(line.split("=", 1) for line in lines)


@pytest.fixture
def project(tmp_path: Path) -> Path:
    shutil.copy(ROOT / ".env.example", tmp_path / ".env.example")
    return tmp_path


def test_creates_env_with_four_distinct_secrets_and_no_placeholder(project: Path) -> None:
    assert init_env.main([], root=project) == 0
    text = (project / ".env").read_text(encoding="utf-8")
    env = _values(text)
    assert "replace-me" not in text
    names = ["POSTGRES_PASSWORD", "MCP_DB_PASSWORD", "MCP_TOKEN", "MCP_URL_TOKEN"]
    secrets = [env[name] for name in names]
    assert len(set(secrets)) == 4
    assert all(len(s) >= 40 for s in secrets)
    # The URLs carry the same password as their variable.
    assert f":{env['MCP_DB_PASSWORD']}@" in env["DATABASE_URL"]
    assert f":{env['POSTGRES_PASSWORD']}@" in env["ADMIN_DATABASE_URL"]


def test_never_overwrites_without_force(project: Path) -> None:
    (project / ".env").write_text("MCP_TOKEN=mine\n", encoding="utf-8")
    assert init_env.main([], root=project) == 1
    assert (project / ".env").read_text(encoding="utf-8") == "MCP_TOKEN=mine\n"

    assert init_env.main(["--force"], root=project) == 0
    assert "mine" not in (project / ".env").read_text(encoding="utf-8")


def test_a_renamed_placeholder_fails_loudly() -> None:
    with pytest.raises(ValueError, match="replace-me-admin"):
        init_env.render("POSTGRES_PASSWORD=changed\n")
