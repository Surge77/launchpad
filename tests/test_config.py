from __future__ import annotations

from pathlib import Path

import pytest

from launchpad.config import ConfigError, load_config


def write_config(tmp_path: Path, body: str) -> Path:
    (tmp_path / "launcher.toml").write_text(body, encoding="utf-8")
    return tmp_path


def test_returns_empty_when_no_config_file(tmp_path: Path) -> None:
    tasks, editor = load_config(tmp_path)

    assert tasks == ()
    assert editor is None


def test_parses_task_with_all_fields(tmp_path: Path) -> None:
    write_config(
        tmp_path,
        """
        editor = "nvim"
        [[task]]
        name = "web"
        cmd = "pnpm dev"
        cwd = "apps/web"
        port = 3000
        open = true
        env = { NODE_ENV = "development" }
        """,
    )

    tasks, editor = load_config(tmp_path)

    assert editor == "nvim"
    assert len(tasks) == 1
    task = tasks[0]
    assert task.name == "web"
    assert task.cmd == "pnpm dev"
    assert task.subdir == "apps/web"
    assert task.port == 3000
    assert task.open_browser is True
    assert task.env == {"NODE_ENV": "development"}


def test_parses_multiple_tasks_in_order(tmp_path: Path) -> None:
    write_config(
        tmp_path,
        """
        [[task]]
        name = "db"
        cmd = "docker compose up"
        [[task]]
        name = "web"
        cmd = "pnpm dev"
        """,
    )

    tasks, _ = load_config(tmp_path)

    assert [task.name for task in tasks] == ["db", "web"]


def test_rejects_task_without_cmd(tmp_path: Path) -> None:
    write_config(tmp_path, '[[task]]\nname = "web"\n')

    with pytest.raises(ConfigError, match="non-empty 'cmd'"):
        load_config(tmp_path)


def test_rejects_empty_name(tmp_path: Path) -> None:
    write_config(tmp_path, '[[task]]\nname = "  "\ncmd = "x"\n')

    with pytest.raises(ConfigError, match="non-empty 'name'"):
        load_config(tmp_path)


@pytest.mark.parametrize("port", ["0", "70000", '"3000"', "true"])
def test_rejects_out_of_range_or_non_integer_port(tmp_path: Path, port: str) -> None:
    write_config(tmp_path, f'[[task]]\nname = "web"\ncmd = "x"\nport = {port}\n')

    with pytest.raises(ConfigError, match="'port' must be an integer"):
        load_config(tmp_path)


@pytest.mark.parametrize("subdir", ["../secrets", "C:/Windows", "/etc"])
def test_rejects_cwd_escaping_the_project(tmp_path: Path, subdir: str) -> None:
    write_config(tmp_path, f'[[task]]\nname = "web"\ncmd = "x"\ncwd = "{subdir}"\n')

    with pytest.raises(ConfigError, match="must stay inside the project"):
        load_config(tmp_path)


def test_rejects_duplicate_task_names(tmp_path: Path) -> None:
    write_config(
        tmp_path,
        """
        [[task]]
        name = "web"
        cmd = "a"
        [[task]]
        name = "web"
        cmd = "b"
        """,
    )

    with pytest.raises(ConfigError, match="duplicate task name"):
        load_config(tmp_path)


def test_rejects_malformed_toml(tmp_path: Path) -> None:
    write_config(tmp_path, "[[task]\nname =")

    with pytest.raises(ConfigError):
        load_config(tmp_path)


def test_rejects_non_string_env_value(tmp_path: Path) -> None:
    write_config(tmp_path, '[[task]]\nname = "web"\ncmd = "x"\nenv = { PORT = 3000 }\n')

    with pytest.raises(ConfigError, match="must be a string"):
        load_config(tmp_path)
