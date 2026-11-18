from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "core"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sybermem_cli import context as context_module
from sybermem_cli import main as main_module


def run_cli(argv: list[str], monkeypatch: pytest.MonkeyPatch) -> int:
    monkeypatch.setattr(sys, "argv", ["sybermem", *argv])
    return main_module.main()


def _rows(count: int, title: str = "Recall hit") -> list[dict]:
    return [
        {
            "record_id": f"change-{index:032d}",
            "title": f"{title} {index}",
            "type": "change",
            "score": 100,
            "match": "record-id",
            "match_reason": "record-id",
        }
        for index in range(count)
    ]


def _stub_recall(monkeypatch: pytest.MonkeyPatch, rows: list[dict]) -> None:
    monkeypatch.setattr(context_module, "high_signal_recall_hints", lambda query, limit=3: (rows, ""))


def test_recall_defaults_to_balanced_budget(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _stub_recall(monkeypatch, _rows(5))
    monkeypatch.delenv("SYBERMEM_MEMORY_BUDGET", raising=False)

    exit_code = run_cli(["context", "recall", "--query", "auth", "--format", "json"], monkeypatch)

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert payload["budget"]["mode"] == "balanced"
    assert payload["budget"]["max_items"] == 3
    assert len(payload["results"]) == 3
    assert payload["budget"]["withheld_items"] == 2


def test_minimal_budget_delivers_one_shortened_hint(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _stub_recall(monkeypatch, _rows(4, title="x" * 200))

    exit_code = run_cli(["context", "recall", "--query", "auth", "--budget", "minimal", "--format", "json"], monkeypatch)

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert payload["budget"]["mode"] == "minimal"
    assert len(payload["results"]) == 1
    assert len(payload["results"][0]["title"]) <= 80
    assert payload["results"][0]["title_truncated"] is True


def test_rich_budget_delivers_more_hints_than_balanced(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _stub_recall(monkeypatch, _rows(6))

    exit_code = run_cli(["context", "recall", "--query", "auth", "--budget", "rich", "--format", "json"], monkeypatch)

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert payload["budget"]["mode"] == "rich"
    assert len(payload["results"]) == 5


def test_explicit_limit_overrides_the_mode_cap(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _stub_recall(monkeypatch, _rows(6))

    exit_code = run_cli(["context", "recall", "--query", "auth", "--budget", "minimal", "--limit", "4", "--format", "json"], monkeypatch)

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert len(payload["results"]) == 4
    assert payload["budget"]["max_items"] == 4


def test_env_budget_is_honoured(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _stub_recall(monkeypatch, _rows(6))
    monkeypatch.setenv("SYBERMEM_MEMORY_BUDGET", "rich")

    exit_code = run_cli(["context", "recall", "--query", "auth", "--format", "json"], monkeypatch)

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert payload["budget"]["mode"] == "rich"


def test_project_yaml_budget_is_honoured(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    project_root = tmp_path / "project"
    (project_root / ".sybermem").mkdir(parents=True)
    (project_root / ".sybermem" / "project.yaml").write_text("project_id: p\nmemory_budget: minimal\n", encoding="utf-8")
    monkeypatch.setattr(context_module, "resolve_project_root", lambda: project_root)
    monkeypatch.delenv("SYBERMEM_MEMORY_BUDGET", raising=False)
    _stub_recall(monkeypatch, _rows(4))

    exit_code = run_cli(["context", "recall", "--query", "auth", "--format", "json"], monkeypatch)

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert payload["budget"]["mode"] == "minimal"
    assert len(payload["results"]) == 1


def test_markdown_reflects_the_budgeted_title(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _stub_recall(monkeypatch, _rows(2, title="y" * 200))

    exit_code = run_cli(["context", "recall", "--query", "auth", "--budget", "minimal", "--format", "markdown"], monkeypatch)

    output = capsys.readouterr().out
    assert exit_code == 0
    assert "…" in output
    assert "y" * 200 not in output
    assert output.count("- ⭐") == 1


def test_abstention_still_reports_the_budget(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(context_module, "high_signal_recall_hints", lambda query, limit=3: ([], "no candidate records matched the prompt"))

    exit_code = run_cli(["context", "recall", "--query", "weak", "--budget", "minimal", "--format", "json"], monkeypatch)

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert payload["results"] == []
    assert payload["budget"]["mode"] == "minimal"
    assert payload["budget"]["delivered_items"] == 0


def test_budget_does_not_loosen_the_high_signal_gate(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A rich budget must not turn a weak keyword-only match into an injected hint."""
    weak = [{"record_id": "change-weak", "title": "weak", "type": "change", "score": 3, "match": "keyword", "match_reason": "keyword"}]
    monkeypatch.setattr(context_module, "high_signal_recall_hints", lambda query, limit=3: ([], "matched rows were keyword-only and below the high-signal floor"))

    exit_code = run_cli(["context", "recall", "--query", "weak", "--budget", "rich", "--format", "json"], monkeypatch)

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert payload["results"] == []
    assert "below the high-signal floor" in payload["abstention"]
    assert weak[0]["record_id"] not in json.dumps(payload["results"])