from pathlib import Path
import json
import os

import pytest

import sybermem_core.search as search_module
from sybermem_core import record_cache
from sybermem_core.search import _ROW_CACHE, search_project


def write_project(root: Path) -> None:
    sybermem = root / ".sybermem"
    (sybermem / "changes").mkdir(parents=True)
    (sybermem / "INDEX.md").write_text("# SyberMem Index\n", encoding="utf-8")
    (sybermem / "project.yaml").write_text("project_id: project-1\nslug: demo\n", encoding="utf-8")


def write_record(root: Path, filename: str, title: str, body: str) -> None:
    (root / ".sybermem" / "changes" / filename).write_text(
        "\n".join([
            "---",
            "type: change",
            "date: 2026-08-05",
            f"title: {title}",
            "status: implemented",
            "---",
            "",
            body,
        ]) + "\n",
        encoding="utf-8",
    )


def _count_parses(monkeypatch) -> dict:
    calls = {"n": 0}
    real_parse = search_module.parse_record_file

    def counting_parse(*args, **kwargs):
        calls["n"] += 1
        return real_parse(*args, **kwargs)

    monkeypatch.setattr(search_module, "parse_record_file", counting_parse)
    return calls


def test_cache_survives_a_new_process_cache_clear(tmp_path: Path, monkeypatch) -> None:
    """The hot path forks per prompt: clearing the in-process cache must not re-parse."""
    _ROW_CACHE.clear()
    root = tmp_path / "project"
    root.mkdir()
    write_project(root)
    write_record(root, "2026-08-05-001-alpha.md", "alpha change", "cachetoken alpha body")
    monkeypatch.chdir(root)

    calls = _count_parses(monkeypatch)
    search_project("cachetoken")
    assert calls["n"] >= 1

    _ROW_CACHE.clear()  # simulates the next CLI process
    search_project("cachetoken")
    assert calls["n"] == 1


def test_deleting_a_record_invalidates_the_cache(tmp_path: Path, monkeypatch) -> None:
    _ROW_CACHE.clear()
    root = tmp_path / "project"
    root.mkdir()
    write_project(root)
    write_record(root, "2026-08-05-001-alpha.md", "alpha change", "cachetoken alpha body")
    write_record(root, "2026-08-05-002-beta.md", "beta change", "cachetoken beta body")
    monkeypatch.chdir(root)

    assert len(search_project("cachetoken")) == 2
    _ROW_CACHE.clear()

    # Delete the newer record and restore the directory mtime so a max-mtime
    # fingerprint would still consider the project unchanged.
    deleted = root / ".sybermem" / "changes" / "2026-08-05-002-beta.md"
    directory = deleted.parent
    before = directory.stat().st_mtime_ns
    deleted.unlink()
    os.utime(directory, ns=(before, before))

    _ROW_CACHE.clear()
    assert len(search_project("cachetoken")) == 1


def test_same_mtime_rewrite_with_different_size_invalidates(tmp_path: Path, monkeypatch) -> None:
    _ROW_CACHE.clear()
    root = tmp_path / "project"
    root.mkdir()
    write_project(root)
    record = root / ".sybermem" / "changes" / "2026-08-05-001-alpha.md"
    write_record(root, record.name, "alpha change", "cachetoken short")
    monkeypatch.chdir(root)
    assert search_project("cachetoken")[0]["content"].endswith("cachetoken short\n")

    stat = record.stat()
    record.write_text(record.read_text(encoding="utf-8").replace("cachetoken short", "cachetoken much longer body"), encoding="utf-8")
    os.utime(record, ns=(stat.st_mtime_ns, stat.st_mtime_ns))

    _ROW_CACHE.clear()
    assert "much longer body" in search_project("cachetoken")[0]["content"]


def test_project_yaml_change_refreshes_cached_identity(tmp_path: Path, monkeypatch) -> None:
    _ROW_CACHE.clear()
    root = tmp_path / "project"
    root.mkdir()
    write_project(root)
    write_record(root, "2026-08-05-001-alpha.md", "alpha change", "cachetoken alpha body")
    monkeypatch.chdir(root)
    assert search_project("cachetoken")[0]["slug"] == "demo"

    (root / ".sybermem" / "project.yaml").write_text("project_id: project-2\nslug: renamed\n", encoding="utf-8")
    _ROW_CACHE.clear()
    assert search_project("cachetoken")[0]["slug"] == "renamed"


def test_corrupt_cache_rebuilds_from_markdown(tmp_path: Path, monkeypatch) -> None:
    _ROW_CACHE.clear()
    root = tmp_path / "project"
    root.mkdir()
    write_project(root)
    write_record(root, "2026-08-05-001-alpha.md", "alpha change", "cachetoken alpha body")
    monkeypatch.chdir(root)
    search_project("cachetoken")

    cache_file = root / ".sybermem" / ".cache" / "records.json"
    assert cache_file.is_file()
    cache_file.write_text("{ not json", encoding="utf-8")

    _ROW_CACHE.clear()
    assert len(search_project("cachetoken")) == 1


def test_schema_drift_is_ignored(tmp_path: Path, monkeypatch) -> None:
    _ROW_CACHE.clear()
    root = tmp_path / "project"
    root.mkdir()
    write_project(root)
    write_record(root, "2026-08-05-001-alpha.md", "alpha change", "cachetoken alpha body")
    monkeypatch.chdir(root)
    search_project("cachetoken")

    cache_file = root / ".sybermem" / ".cache" / "records.json"
    payload = json.loads(cache_file.read_text(encoding="utf-8"))
    payload["schema_version"] = record_cache.CACHE_SCHEMA_VERSION + 1
    cache_file.write_text(json.dumps(payload), encoding="utf-8")

    calls = _count_parses(monkeypatch)
    _ROW_CACHE.clear()
    search_project("cachetoken")
    assert calls["n"] >= 1


def test_opt_out_env_disables_persistence(tmp_path: Path, monkeypatch) -> None:
    _ROW_CACHE.clear()
    root = tmp_path / "project"
    root.mkdir()
    write_project(root)
    write_record(root, "2026-08-05-001-alpha.md", "alpha change", "cachetoken alpha body")
    monkeypatch.chdir(root)
    monkeypatch.setenv(record_cache.RECORD_CACHE_ENV, "0")

    search_project("cachetoken")
    assert not (root / ".sybermem" / ".cache" / "records.json").exists()

    calls = _count_parses(monkeypatch)
    _ROW_CACHE.clear()
    search_project("cachetoken")
    assert calls["n"] >= 1


def test_symlinked_cache_destination_is_refused(tmp_path: Path, monkeypatch) -> None:
    _ROW_CACHE.clear()
    root = tmp_path / "project"
    root.mkdir()
    write_project(root)
    write_record(root, "2026-08-05-001-alpha.md", "alpha change", "cachetoken alpha body")
    outside = tmp_path / "outside"
    outside.mkdir()
    try:
        (root / ".sybermem" / ".cache").symlink_to(outside, target_is_directory=True)
    except OSError as exc:
        if os.name == "nt" and getattr(exc, "winerror", None) == 1314:
            pytest.skip("Windows account lacks symlink creation privilege (WinError 1314)")
        raise
    monkeypatch.chdir(root)

    assert len(search_project("cachetoken")) == 1
    assert not (outside / "records.json").exists()


def test_cached_rows_are_not_shared_mutable_state(tmp_path: Path, monkeypatch) -> None:
    _ROW_CACHE.clear()
    root = tmp_path / "project"
    root.mkdir()
    write_project(root)
    write_record(root, "2026-08-05-001-alpha.md", "alpha change", "cachetoken alpha body")
    monkeypatch.chdir(root)

    first = search_project("cachetoken")
    first[0]["title"] = "mutated by caller"
    _ROW_CACHE.clear()
    assert search_project("cachetoken")[0]["title"] == "alpha change"


def test_cache_never_stores_prompt_text(tmp_path: Path, monkeypatch) -> None:
    _ROW_CACHE.clear()
    root = tmp_path / "project"
    root.mkdir()
    write_project(root)
    write_record(root, "2026-08-05-001-alpha.md", "alpha change", "cachetoken alpha body")
    monkeypatch.chdir(root)
    search_project("a secret prompt nobody should persist")

    cache_file = root / ".sybermem" / ".cache" / "records.json"
    assert "a secret prompt nobody should persist" not in cache_file.read_text(encoding="utf-8")