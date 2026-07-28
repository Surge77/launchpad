from __future__ import annotations

import json
from pathlib import Path

from launchpad.discovery import detect_stack, detect_tasks, discover


def make_repo(root: Path, name: str, files: dict[str, str] | None = None) -> Path:
    repo = root / name
    (repo / ".git").mkdir(parents=True)
    for filename, content in (files or {}).items():
        target = repo / filename
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    return repo


def package_json(**kwargs: object) -> str:
    return json.dumps(kwargs)


def test_finds_only_git_repos(tmp_path: Path) -> None:
    make_repo(tmp_path, "real-repo")
    (tmp_path / "just-a-folder").mkdir()

    names = [project.name for project in discover(tmp_path)]

    assert names == ["real-repo"]


def test_finds_repos_one_level_down(tmp_path: Path) -> None:
    make_repo(tmp_path / "work", "nested-repo")

    names = [project.name for project in discover(tmp_path)]

    assert names == ["nested-repo"]


def test_skips_node_modules(tmp_path: Path) -> None:
    make_repo(tmp_path / "node_modules", "vendored")

    assert discover(tmp_path) == []


def test_detects_typescript_when_tsconfig_present(tmp_path: Path) -> None:
    repo = make_repo(tmp_path, "web", {"package.json": package_json(), "tsconfig.json": "{}"})

    assert detect_stack(repo) == "TS"


def test_detects_javascript_without_tsconfig(tmp_path: Path) -> None:
    repo = make_repo(tmp_path, "web", {"package.json": package_json()})

    assert detect_stack(repo) == "JS"


def test_detects_rust_go_java_python(tmp_path: Path) -> None:
    cases = {
        "Cargo.toml": "Rust",
        "go.mod": "Go",
        "pom.xml": "Java",
        "pyproject.toml": "Py",
    }
    for filename, expected in cases.items():
        repo = make_repo(tmp_path, f"repo-{expected}", {filename: ""})
        assert detect_stack(repo) == expected


def test_returns_dash_for_unknown_stack(tmp_path: Path) -> None:
    repo = make_repo(tmp_path, "docs", {"README.md": "hi"})

    assert detect_stack(repo) == "-"


def test_node_task_uses_pnpm_when_lockfile_present(tmp_path: Path) -> None:
    repo = make_repo(
        tmp_path,
        "web",
        {"package.json": package_json(scripts={"dev": "next dev"}), "pnpm-lock.yaml": ""},
    )

    tasks = detect_tasks(repo, "JS")

    assert tasks[0].cmd == "pnpm dev"


def test_node_task_falls_back_to_npm_run(tmp_path: Path) -> None:
    repo = make_repo(tmp_path, "web", {"package.json": package_json(scripts={"dev": "webpack serve"})})

    assert detect_tasks(repo, "JS")[0].cmd == "npm run dev"


def test_next_project_gets_port_3000(tmp_path: Path) -> None:
    repo = make_repo(
        tmp_path,
        "web",
        {"package.json": package_json(scripts={"dev": "next dev"}, dependencies={"next": "16"})},
    )

    assert detect_tasks(repo, "TS")[0].port == 3000


def test_vite_project_gets_port_5173(tmp_path: Path) -> None:
    repo = make_repo(
        tmp_path,
        "web",
        {"package.json": package_json(scripts={"dev": "vite"}, devDependencies={"vite": "5"})},
    )

    assert detect_tasks(repo, "TS")[0].port == 5173


def test_node_project_without_dev_script_has_no_tasks(tmp_path: Path) -> None:
    repo = make_repo(tmp_path, "lib", {"package.json": package_json(scripts={"build": "tsc"})})

    assert detect_tasks(repo, "TS") == ()


def test_malformed_package_json_yields_no_tasks(tmp_path: Path) -> None:
    repo = make_repo(tmp_path, "broken", {"package.json": "{not json"})

    assert detect_tasks(repo, "JS") == ()


def test_spring_boot_pom_gets_boot_run_task(tmp_path: Path) -> None:
    repo = make_repo(tmp_path, "api", {"pom.xml": "<artifactId>spring-boot-starter-web</artifactId>"})

    tasks = detect_tasks(repo, "Java")

    assert tasks[0].cmd == "mvn spring-boot:run"
    assert tasks[0].port == 8080


def test_plain_maven_pom_gets_compile_task(tmp_path: Path) -> None:
    repo = make_repo(tmp_path, "lib", {"pom.xml": "<artifactId>plain</artifactId>"})

    assert detect_tasks(repo, "Java")[0].cmd == "mvn -q compile"


def test_fastapi_project_gets_uvicorn_task(tmp_path: Path) -> None:
    repo = make_repo(tmp_path, "api", {"pyproject.toml": 'dependencies = ["fastapi"]'})

    tasks = detect_tasks(repo, "Py")

    assert tasks[0].port == 8000
    assert "uvicorn" in tasks[0].cmd


def test_launcher_toml_overrides_detection(tmp_path: Path) -> None:
    make_repo(
        tmp_path,
        "web",
        {
            "package.json": package_json(scripts={"dev": "next dev"}),
            "launcher.toml": '[[task]]\nname = "custom"\ncmd = "make run"\n',
        },
    )

    project = discover(tmp_path)[0]

    assert [task.name for task in project.tasks] == ["custom"]
    assert project.tasks[0].cmd == "make run"


def test_monorepo_detects_stacks_one_level_down(tmp_path: Path) -> None:
    repo = make_repo(tmp_path, "relay")
    (repo / "gateway").mkdir()
    (repo / "gateway" / "go.mod").write_text("module relay/gateway", encoding="utf-8")
    (repo / "web").mkdir()
    (repo / "web" / "package.json").write_text(
        package_json(scripts={"dev": "next dev"}, dependencies={"next": "16"}), encoding="utf-8"
    )

    project = discover(tmp_path)[0]

    assert project.stack == "Go+TS" or project.stack == "Go+JS"
    assert sorted(task.name for task in project.tasks) == ["gateway", "web"]


def test_monorepo_tasks_carry_their_subdirectory(tmp_path: Path) -> None:
    repo = make_repo(tmp_path, "relay")
    (repo / "gateway").mkdir()
    (repo / "gateway" / "go.mod").write_text("module relay/gateway", encoding="utf-8")

    task = discover(tmp_path)[0].tasks[0]

    assert task.subdir == "gateway"
    assert task.cmd == "go run ./..."


def test_monorepo_skips_vendored_directories(tmp_path: Path) -> None:
    repo = make_repo(tmp_path, "app")
    (repo / "node_modules" / "dep").mkdir(parents=True)
    (repo / "node_modules" / "dep" / "package.json").write_text(package_json(scripts={"dev": "x"}), encoding="utf-8")

    project = discover(tmp_path)[0]

    assert project.stack == "-"
    assert project.tasks == ()


def test_root_manifest_wins_over_subdirectory_scan(tmp_path: Path) -> None:
    repo = make_repo(tmp_path, "app", {"package.json": package_json(scripts={"dev": "next dev"})})
    (repo / "server").mkdir()
    (repo / "server" / "go.mod").write_text("module app/server", encoding="utf-8")

    project = discover(tmp_path)[0]

    assert project.stack == "JS"
    assert [task.name for task in project.tasks] == ["dev"]


def test_launcher_toml_can_override_editor(tmp_path: Path) -> None:
    make_repo(tmp_path, "web", {"launcher.toml": 'editor = "nvim"\n'})

    assert discover(tmp_path)[0].editor == "nvim"
