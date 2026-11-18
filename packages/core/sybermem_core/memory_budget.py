"""Configurable memory budgets for prompt-time injection (minimal / balanced / rich).

Selection stays untouched: the high-signal recall gate still decides *which* records
qualify. A budget only caps how much of that already-selected material is delivered,
so a project can trade context economy against continuity without loosening trust.

`balanced` reproduces the historical behaviour exactly (three recall hints, no
truncation), so upgrading changes nothing until a project opts into another mode.
Resolution order: explicit CLI flag > SYBERMEM_MEMORY_BUDGET > project.yaml
`memory_budget` > balanced. An unknown value degrades to balanced rather than failing.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from .records import parse_project_yaml


MEMORY_BUDGET_ENV: str = "SYBERMEM_MEMORY_BUDGET"
DEFAULT_MODE: str = "balanced"


@dataclass(frozen=True)
class MemoryBudget:
    mode: str
    max_items: int
    max_title_chars: int | None
    max_total_chars: int | None


_BUDGETS: dict[str, MemoryBudget] = {
    "minimal": MemoryBudget("minimal", 1, 80, 400),
    "balanced": MemoryBudget("balanced", 3, None, None),
    "rich": MemoryBudget("rich", 5, None, None),
}

MODES: tuple[str, ...] = tuple(_BUDGETS)


def budget_for(mode: str) -> MemoryBudget:
    return _BUDGETS.get((mode or "").strip().lower(), _BUDGETS[DEFAULT_MODE])


def resolve_mode(root: Path | None = None, explicit: str | None = None) -> str:
    if explicit:
        return explicit.strip().lower()
    from_env = os.environ.get(MEMORY_BUDGET_ENV, "").strip().lower()
    if from_env:
        return from_env
    if root is not None:
        from_project = parse_project_yaml(root).get("memory_budget", "").strip().lower()
        if from_project:
            return from_project
    return DEFAULT_MODE


def resolve_budget(root: Path | None = None, explicit: str | None = None) -> MemoryBudget:
    return budget_for(resolve_mode(root, explicit))


def truncate_title(title: str, limit: int | None) -> tuple[str, bool]:
    if limit is None or len(title) <= limit:
        return title, False
    return title[: max(limit - 1, 0)].rstrip() + "…", True


def apply_budget(items: list[dict], budget: MemoryBudget) -> tuple[list[dict], dict[str, object]]:
    """Cap item count and rendered size, reporting what the budget actually did.

    Items are dictionaries carrying at least a ``title``; they are copied so callers
    keep their originals. The character ceiling is measured on the delivered title
    text, which is the part of a recall hint that scales with record verbosity.
    """
    delivered: list[dict] = []
    used_chars = 0
    truncated = False
    for item in items[: budget.max_items]:
        copied = dict(item)
        title, was_truncated = truncate_title(str(copied.get("title", "")), budget.max_title_chars)
        if budget.max_total_chars is not None and used_chars + len(title) > budget.max_total_chars:
            break
        copied["title"] = title
        if was_truncated:
            copied["title_truncated"] = True
            truncated = True
        used_chars += len(title)
        delivered.append(copied)
    metadata: dict[str, object] = {
        "mode": budget.mode,
        "max_items": budget.max_items,
        "delivered_items": len(delivered),
        "title_chars": used_chars,
        "truncated": truncated,
        "withheld_items": max(len(items) - len(delivered), 0),
    }
    return delivered, metadata