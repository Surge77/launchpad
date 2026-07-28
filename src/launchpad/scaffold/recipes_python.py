"""FastAPI, bootstrapped with uv."""

from __future__ import annotations

from launchpad.scaffold import files
from launchpad.scaffold.files import TaskSpec
from launchpad.scaffold.plan import Command, FileSpec, Step

UVICORN_PORT = 8000


def fastapi(name: str) -> list[Step]:
    return [
        # --bare writes only pyproject.toml: without it uv drops a sample main.py
        # next to our app package, and every generated project starts with junk.
        Command(
            label="uv init",
            argv=("uv", "init", "--bare", "--name", name, "--python", "3.11", "--vcs", "none"),
        ),
        Command(
            label="add runtime deps",
            argv=("uv", "add", "fastapi", "uvicorn[standard]", "sqlalchemy", "psycopg[binary]", "pydantic-settings"),
        ),
        Command(label="add dev deps", argv=("uv", "add", "--dev", "pytest", "httpx"), optional=True),
        _settings(),
        _main(),
        _test(),
        _pytest_ini(),
        _gitignore(),
        files.postgres_compose(name),
        files.env_example(
            {
                "DATABASE_URL": f"postgresql+psycopg://{name}:{name}@localhost:5432/{name}",
                "APP_ENV": "local",
            }
        ),
        files.launcher_toml(
            [
                TaskSpec(
                    name="api",
                    cmd="uv run uvicorn app.main:app --reload",
                    port=UVICORN_PORT,
                    open_browser=True,
                )
            ]
        ),
        files.readme(
            name,
            "FastAPI + SQLAlchemy + uv",
            [
                "`docker compose up -d` to start Postgres",
                "`cp .env.example .env`",
                "`uv run uvicorn app.main:app --reload`",
                "Open `http://localhost:8000/docs`",
            ],
            [
                "`uv run pytest` runs the test suite.",
                "Settings are read from the environment via pydantic-settings.",
                "The browser opens on :8000 — FastAPI's Swagger UI lives at /docs.",
            ],
        ),
    ]


def _pytest_ini() -> FileSpec:
    """`app` is not an installed package, so the project root must be importable.

    Written as pytest.ini rather than a [tool.pytest] block because uv owns
    pyproject.toml and overwriting it would drop the dependencies it just added.
    """
    return FileSpec(
        path="pytest.ini",
        content="""[pytest]
pythonpath = .
testpaths = tests
addopts = -q
""",
    )


def _gitignore() -> FileSpec:
    return FileSpec(
        path=".gitignore",
        content=""".venv/
__pycache__/
*.py[cod]
.pytest_cache/
.env
""",
    )


def _settings() -> FileSpec:
    return FileSpec(
        path="app/config.py",
        content='''from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: str = "local"
    database_url: str = "postgresql+psycopg://localhost:5432/app"


@lru_cache
def get_settings() -> Settings:
    return Settings()
''',
    )


def _main() -> FileSpec:
    return FileSpec(
        path="app/main.py",
        content='''from fastapi import FastAPI

from app.config import get_settings

app = FastAPI(title="service")


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "env": get_settings().app_env}
''',
    )


def _test() -> FileSpec:
    return FileSpec(
        path="tests/test_main.py",
        content='''from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_health_reports_ok() -> None:
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"
''',
    )
