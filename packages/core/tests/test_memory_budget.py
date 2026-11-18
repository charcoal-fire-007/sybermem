from pathlib import Path

from sybermem_core.memory_budget import (
    DEFAULT_MODE,
    MODES,
    apply_budget,
    budget_for,
    resolve_budget,
    resolve_mode,
    truncate_title,
)


def _items(count: int) -> list[dict]:
    return [{"record_id": f"change-{index:032d}", "title": f"record title {index}"} for index in range(count)]


def test_balanced_is_the_default_and_matches_historical_behaviour() -> None:
    assert DEFAULT_MODE == "balanced"
    budget = budget_for("balanced")
    assert budget.max_items == 3
    assert budget.max_title_chars is None
    assert budget.max_total_chars is None


def test_unknown_mode_degrades_to_balanced() -> None:
    assert budget_for("nonsense").mode == "balanced"
    assert budget_for("").mode == "balanced"


def test_modes_are_ordered_by_generosity() -> None:
    assert MODES == ("minimal", "balanced", "rich")
    assert budget_for("minimal").max_items < budget_for("balanced").max_items < budget_for("rich").max_items


def test_explicit_flag_beats_env_and_project(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / ".sybermem").mkdir()
    (tmp_path / ".sybermem" / "project.yaml").write_text("memory_budget: minimal\n", encoding="utf-8")
    monkeypatch.setenv("SYBERMEM_MEMORY_BUDGET", "rich")
    assert resolve_mode(tmp_path, explicit="balanced") == "balanced"


def test_env_beats_project(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / ".sybermem").mkdir()
    (tmp_path / ".sybermem" / "project.yaml").write_text("memory_budget: minimal\n", encoding="utf-8")
    monkeypatch.setenv("SYBERMEM_MEMORY_BUDGET", "rich")
    assert resolve_mode(tmp_path) == "rich"


def test_project_yaml_is_used_when_no_env(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / ".sybermem").mkdir()
    (tmp_path / ".sybermem" / "project.yaml").write_text("project_id: p\nmemory_budget: minimal\n", encoding="utf-8")
    monkeypatch.delenv("SYBERMEM_MEMORY_BUDGET", raising=False)
    assert resolve_mode(tmp_path) == "minimal"


def test_missing_project_yaml_falls_back_to_balanced(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("SYBERMEM_MEMORY_BUDGET", raising=False)
    assert resolve_mode(tmp_path) == "balanced"
    assert resolve_budget(tmp_path).mode == "balanced"


def test_balanced_delivers_three_untruncated_items() -> None:
    delivered, metadata = apply_budget(_items(5), budget_for("balanced"))
    assert len(delivered) == 3
    assert metadata["truncated"] is False
    assert metadata["withheld_items"] == 2
    assert delivered[0]["title"] == "record title 0"


def test_minimal_delivers_one_shortened_item() -> None:
    long_title = "x" * 200
    delivered, metadata = apply_budget([{"record_id": "change-1", "title": long_title}], budget_for("minimal"))
    assert len(delivered) == 1
    assert len(delivered[0]["title"]) <= 80
    assert delivered[0]["title"].endswith("…")
    assert delivered[0]["title_truncated"] is True
    assert metadata["truncated"] is True


def test_rich_delivers_more_items_than_balanced() -> None:
    delivered, _ = apply_budget(_items(6), budget_for("rich"))
    assert len(delivered) == 5


def test_total_char_ceiling_stops_delivery() -> None:
    items = [{"record_id": f"change-{index}", "title": "y" * 300} for index in range(3)]
    delivered, metadata = apply_budget(items, budget_for("minimal"))
    assert len(delivered) == 1
    assert metadata["title_chars"] <= 400


def test_apply_budget_does_not_mutate_caller_items() -> None:
    items = _items(2)
    apply_budget(items, budget_for("minimal"))
    assert items[0]["title"] == "record title 0"
    assert "title_truncated" not in items[0]


def test_truncate_title_leaves_short_titles_alone() -> None:
    assert truncate_title("short", 80) == ("short", False)
    assert truncate_title("short", None) == ("short", False)


def test_empty_selection_reports_zero_without_error() -> None:
    delivered, metadata = apply_budget([], budget_for("minimal"))
    assert delivered == []
    assert metadata["delivered_items"] == 0
    assert metadata["withheld_items"] == 0