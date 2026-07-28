"""Registry of available stacks."""

from __future__ import annotations

from launchpad.scaffold.plan import Recipe, ScaffoldError
from launchpad.scaffold.recipes_jvm import spring_boot
from launchpad.scaffold.recipes_node import mern, next_fullstack
from launchpad.scaffold.recipes_python import fastapi

RECIPES: tuple[Recipe, ...] = (
    Recipe(
        key="next",
        title="Next.js full-stack",
        summary="Next.js 16 + TypeScript + Tailwind + Prisma + Postgres + Auth.js v5",
        requires=("npx", "pnpm"),
        build=next_fullstack,
    ),
    Recipe(
        key="mern",
        title="MERN",
        summary="MongoDB + Express + React (Vite, TypeScript) + Node, client/server layout",
        requires=("npm",),
        build=mern,
    ),
    Recipe(
        key="spring",
        title="Spring Boot",
        summary="Spring Boot 3 (Java 21) + JPA + Postgres + Actuator, via start.spring.io",
        requires=("mvn",),
        build=spring_boot,
    ),
    Recipe(
        key="fastapi",
        title="FastAPI",
        summary="FastAPI + SQLAlchemy + pydantic-settings + pytest, managed by uv",
        requires=("uv",),
        build=fastapi,
    ),
)


def all_recipes() -> tuple[Recipe, ...]:
    return RECIPES


def find_recipe(key: str) -> Recipe:
    match = next((recipe for recipe in RECIPES if recipe.key == key.lower()), None)
    if match is None:
        available = ", ".join(recipe.key for recipe in RECIPES)
        raise ScaffoldError(f"unknown stack {key!r} — available: {available}")
    return match
