"""Project scaffolding: wrap each ecosystem's own generator, then add the glue."""

from launchpad.scaffold.engine import build_plan, describe, execute, missing_tools, validate_name
from launchpad.scaffold.plan import Command, Download, FileSpec, Plan, Recipe, ScaffoldError
from launchpad.scaffold.recipes import all_recipes, find_recipe

__all__ = [
    "Command",
    "Download",
    "FileSpec",
    "Plan",
    "Recipe",
    "ScaffoldError",
    "all_recipes",
    "build_plan",
    "describe",
    "execute",
    "find_recipe",
    "missing_tools",
    "validate_name",
]
