from __future__ import annotations

import io
import zipfile
from pathlib import Path

import pytest

from launchpad import scaffold
from launchpad.scaffold import engine
from launchpad.scaffold.plan import Command, Download, FileSpec, ScaffoldError
from launchpad.scaffold.recipes_jvm import initializr_url


# -- name validation -------------------------------------------------------


@pytest.mark.parametrize("name", ["app", "invoice-ai", "my_app", "app2", "a.b-c_d"])
def test_accepts_reasonable_names(name: str) -> None:
    assert engine.validate_name(name) == name


@pytest.mark.parametrize(
    "name",
    [
        "",
        " ",
        "-leading-dash",
        ".hidden",
        "UPPER",
        "has space",
        "semi;colon",
        "amp&sand",
        "pipe|char",
        "quote\"mark",
        "back`tick",
        "dollar$sign",
        "slash/inside",
        "back\\slash",
        "..",
        "../escape",
        "a" * 65,
    ],
)
def test_rejects_dangerous_or_malformed_names(name: str) -> None:
    with pytest.raises(ScaffoldError):
        engine.validate_name(name)


@pytest.mark.parametrize("name", ["con", "nul", "com1", "lpt9", "aux.txt"])
def test_rejects_windows_reserved_device_names(name: str) -> None:
    with pytest.raises(ScaffoldError, match="reserved device name"):
        engine.validate_name(name)


# -- plan building ---------------------------------------------------------


def test_build_plan_targets_a_subdirectory_of_root(tmp_path: Path) -> None:
    plan = engine.build_plan(scaffold.find_recipe("fastapi"), "svc", tmp_path)

    assert plan.target == (tmp_path / "svc").resolve()
    assert plan.parent == tmp_path.resolve()


def test_build_plan_rejects_a_non_empty_target(tmp_path: Path) -> None:
    (tmp_path / "svc").mkdir()
    (tmp_path / "svc" / "existing.txt").write_text("mine", encoding="utf-8")

    with pytest.raises(ScaffoldError, match="already exists and is not empty"):
        engine.build_plan(scaffold.find_recipe("fastapi"), "svc", tmp_path)


def test_build_plan_allows_an_empty_existing_directory(tmp_path: Path) -> None:
    (tmp_path / "svc").mkdir()

    plan = engine.build_plan(scaffold.find_recipe("fastapi"), "svc", tmp_path)

    assert plan.name == "svc"


def test_build_plan_rejects_a_target_that_is_a_file(tmp_path: Path) -> None:
    (tmp_path / "svc").write_text("i am a file", encoding="utf-8")

    with pytest.raises(ScaffoldError, match="is a file"):
        engine.build_plan(scaffold.find_recipe("fastapi"), "svc", tmp_path)


def test_github_without_git_is_rejected(tmp_path: Path) -> None:
    """gh repo create --push has nothing to push without a commit."""
    with pytest.raises(ScaffoldError, match="--github requires git"):
        engine.build_plan(scaffold.find_recipe("fastapi"), "svc", tmp_path, git=False, github="public")


def test_rejects_name_ending_with_a_dot() -> None:
    with pytest.raises(ScaffoldError, match="cannot end with"):
        engine.validate_name("app.")


def test_unknown_stack_lists_the_available_ones() -> None:
    with pytest.raises(ScaffoldError, match="available: next, mern, spring, fastapi"):
        scaffold.find_recipe("cobol")


@pytest.mark.parametrize("key", ["next", "mern", "spring", "fastapi"])
def test_every_recipe_produces_steps_and_a_launcher_toml(key: str, tmp_path: Path) -> None:
    plan = engine.build_plan(scaffold.find_recipe(key), "demo", tmp_path)

    written = [step.path for step in plan.steps if isinstance(step, FileSpec)]
    assert plan.steps
    assert "launcher.toml" in written
    assert "README.md" in written
    assert ".env.example" in written


@pytest.mark.parametrize("key", ["next", "mern", "spring", "fastapi"])
def test_generated_launcher_toml_parses_and_matches_our_schema(key: str, tmp_path: Path) -> None:
    """The scaffolder must not emit a config its own parser rejects."""
    from launchpad.config import load_config

    plan = engine.build_plan(scaffold.find_recipe(key), "demo", tmp_path)
    toml = next(step for step in plan.steps if isinstance(step, FileSpec) and step.path == "launcher.toml")
    (tmp_path / "launcher.toml").write_text(toml.content, encoding="utf-8")

    tasks, _ = load_config(tmp_path)

    assert tasks, "generated config must define at least one task"
    assert all(task.cmd for task in tasks)


def test_mern_orders_the_api_before_the_client(tmp_path: Path) -> None:
    """Port readiness makes the API a gate for the client."""
    from launchpad.config import load_config

    plan = engine.build_plan(scaffold.find_recipe("mern"), "demo", tmp_path)
    toml = next(step for step in plan.steps if isinstance(step, FileSpec) and step.path == "launcher.toml")
    (tmp_path / "launcher.toml").write_text(toml.content, encoding="utf-8")

    tasks, _ = load_config(tmp_path)

    assert [task.name for task in tasks] == ["api", "web"]
    assert tasks[0].port == 5000
    assert tasks[1].open_browser is True


def test_describe_renders_every_step(tmp_path: Path) -> None:
    plan = engine.build_plan(scaffold.find_recipe("spring"), "demo", tmp_path)

    lines = engine.describe(plan)

    assert any("start.spring.io" in line for line in lines)
    assert any("launcher.toml" in line for line in lines)
    assert any("git init" in line for line in lines)


def test_describe_mentions_github_only_when_requested(tmp_path: Path) -> None:
    quiet = engine.build_plan(scaffold.find_recipe("fastapi"), "a", tmp_path)
    loud = engine.build_plan(scaffold.find_recipe("fastapi"), "b", tmp_path, github="private")

    assert not any("gh repo create" in line for line in engine.describe(quiet))
    assert any("gh repo create" in line for line in engine.describe(loud))


def test_initializr_url_is_https_and_carries_the_artifact_id() -> None:
    url = initializr_url("ledger-api")

    assert url.startswith("https://start.spring.io/starter.zip?")
    assert "artifactId=ledger-api" in url
    assert "dependencies=web%2Cdata-jpa%2Cpostgresql" in url


def test_java_package_name_strips_dashes_and_leading_digits() -> None:
    assert "packageName=com.surge.ledgerapi" in initializr_url("ledger-api")
    assert "packageName=com.surge.app2fast" in initializr_url("2fast")


# -- execution -------------------------------------------------------------


def test_execute_writes_files_without_running_commands(tmp_path: Path) -> None:
    recipe = scaffold.find_recipe("fastapi")
    plan = engine.build_plan(recipe, "svc", tmp_path, git=False)
    files_only = tuple(step for step in plan.steps if isinstance(step, FileSpec))
    plan = type(plan)(**{**vars(plan), "steps": files_only})

    engine.execute(plan, on_progress=lambda _: None)

    assert (tmp_path / "svc" / "launcher.toml").is_file()
    assert (tmp_path / "svc" / "app" / "main.py").is_file()
    assert "FastAPI" in (tmp_path / "svc" / "app" / "main.py").read_text(encoding="utf-8")


def test_failing_command_aborts_and_leaves_the_directory(tmp_path: Path) -> None:
    recipe = scaffold.find_recipe("fastapi")
    plan = engine.build_plan(recipe, "svc", tmp_path, git=False)
    failing = (
        FileSpec(path="keep.txt", content="work"),
        Command(label="doomed", argv=("definitely-not-a-real-tool-xyz",)),
    )
    plan = type(plan)(**{**vars(plan), "steps": failing})

    with pytest.raises(ScaffoldError, match="not on PATH"):
        engine.execute(plan, on_progress=lambda _: None)

    assert (tmp_path / "svc" / "keep.txt").is_file(), "partial work must survive for the user to inspect"


def test_optional_step_failure_does_not_abort(tmp_path: Path) -> None:
    import sys

    recipe = scaffold.find_recipe("fastapi")
    plan = engine.build_plan(recipe, "svc", tmp_path, git=False)
    steps = (
        Command(
            label="optional failure",
            argv=(Path(sys.executable).stem, "-c", "import sys; sys.exit(3)"),
            optional=True,
        ),
        FileSpec(path="after.txt", content="reached"),
    )
    plan = type(plan)(**{**vars(plan), "steps": steps})

    engine.execute(plan, on_progress=lambda _: None)

    assert (tmp_path / "svc" / "after.txt").is_file()


def test_download_refuses_plain_http(tmp_path: Path) -> None:
    recipe = scaffold.find_recipe("spring")
    plan = engine.build_plan(recipe, "svc", tmp_path, git=False)
    plan = type(plan)(**{**vars(plan), "steps": (Download(label="insecure", url="http://example.com/x.zip"),)})

    with pytest.raises(ScaffoldError, match="plain HTTP"):
        engine.execute(plan, on_progress=lambda _: None)


def test_zip_extraction_rejects_paths_escaping_the_project(tmp_path: Path) -> None:
    """Zip slip: a crafted archive must not write outside the target directory."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("../escaped.txt", "pwned")
    target = tmp_path / "svc"
    target.mkdir()

    with zipfile.ZipFile(io.BytesIO(buffer.getvalue())) as archive:
        with pytest.raises(ScaffoldError, match="escapes the project directory"):
            engine._extract_safely(archive, target)

    assert not (tmp_path / "escaped.txt").exists()


def test_zip_extraction_accepts_normal_entries(tmp_path: Path) -> None:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("pom.xml", "<project/>")
        archive.writestr("src/main/java/App.java", "class App {}")
    target = tmp_path / "svc"
    target.mkdir()

    with zipfile.ZipFile(io.BytesIO(buffer.getvalue())) as archive:
        engine._extract_safely(archive, target)

    assert (target / "pom.xml").is_file()
    assert (target / "src" / "main" / "java" / "App.java").is_file()


def test_missing_tools_reports_absent_executables() -> None:
    from launchpad.scaffold.plan import Recipe

    recipe = Recipe(key="x", title="X", summary="", requires=("definitely-not-a-tool-xyz", "git"), build=lambda n: [])

    assert engine.missing_tools(recipe) == ["definitely-not-a-tool-xyz"]
