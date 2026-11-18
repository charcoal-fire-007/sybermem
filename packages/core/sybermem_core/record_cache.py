"""Cross-process parsed-record cache for the prompt-time recall hot path.

Every prompt forks a fresh CLI process, so the in-process row cache in ``search``
never survives to the next prompt and each one re-reads and re-parses every record.
This persists the same pristine parses to a machine-local, Git-ignored file so an
unchanged project skips that work on the next prompt.

Markdown stays the source of truth: the cache is derived, disposable, and fail-open.
Any doubt (missing, corrupt, oversized, symlinked, schema drift, fingerprint change)
rebuilds from the records themselves. It stores only parsed record content that is
already on local disk — never prompt text, queries, or injection decisions.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

from .records import iter_record_files


CACHE_SCHEMA_VERSION: int = 1
CACHE_DIR_NAME: str = ".cache"
CACHE_FILE_NAME: str = "records.json"
MAX_CACHE_BYTES: int = 8 * 1024 * 1024
RECORD_CACHE_ENV: str = "SYBERMEM_RECORD_CACHE"


def cache_enabled() -> bool:
    """Opt-out switch; the cache is on by default because the hot path needs it."""
    return os.environ.get(RECORD_CACHE_ENV, "") != "0"


def fingerprint(root: Path) -> str:
    """Hash every record's identity, mtime and size, plus project.yaml.

    The previous max-mtime fingerprint missed two real cases: deleting a record
    (the newest mtime can stay unchanged) and a rewrite that keeps mtime but changes
    length. Including the full file set with per-file size closes both. project.yaml
    is included because parsed rows embed its project_id/slug.
    """
    entries: list[str] = []
    for path in iter_record_files(root):
        try:
            stat = path.stat()
        except OSError:
            continue
        entries.append(f"{path.as_posix()}|{stat.st_mtime_ns}|{stat.st_size}")
    project_yaml = root / ".sybermem" / "project.yaml"
    try:
        stat = project_yaml.stat()
        entries.append(f"project.yaml|{stat.st_mtime_ns}|{stat.st_size}")
    except OSError:
        entries.append("project.yaml|absent")
    return hashlib.sha256("\n".join(sorted(entries)).encode("utf-8")).hexdigest()


def _cache_path(root: Path) -> Path | None:
    """Return the cache destination, or None when it is not safe to write there."""
    sybermem = root / ".sybermem"
    if sybermem.is_symlink() or not sybermem.is_dir():
        return None
    cache_dir = sybermem / CACHE_DIR_NAME
    if cache_dir.is_symlink():
        return None
    target = cache_dir / CACHE_FILE_NAME
    if target.is_symlink():
        return None
    try:
        resolved = target.resolve()
        resolved.relative_to(sybermem.resolve())
    except (OSError, ValueError):
        return None
    return target


def _valid_rows(rows: object) -> bool:
    if not isinstance(rows, list):
        return False
    for row in rows:
        if not isinstance(row, dict):
            return False
        if any(not isinstance(key, str) or not isinstance(value, str) for key, value in row.items()):
            return False
    return True


def load(root: Path, expected_fingerprint: str) -> list[dict[str, str]] | None:
    """Return cached pristine rows, or None when they cannot be trusted."""
    if not cache_enabled():
        return None
    target = _cache_path(root)
    if target is None or not target.is_file():
        return None
    try:
        if target.stat().st_size > MAX_CACHE_BYTES:
            return None
        payload = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeError):
        return None
    if not isinstance(payload, dict):
        return None
    if payload.get("schema_version") != CACHE_SCHEMA_VERSION:
        return None
    if payload.get("fingerprint") != expected_fingerprint:
        return None
    rows = payload.get("rows")
    if not _valid_rows(rows):
        return None
    return rows


def store(root: Path, current_fingerprint: str, rows: list[dict[str, str]]) -> None:
    """Atomically persist pristine rows. Never raises; a failed write is a cache miss."""
    if not cache_enabled():
        return
    target = _cache_path(root)
    if target is None:
        return
    try:
        encoded = json.dumps(
            {"schema_version": CACHE_SCHEMA_VERSION, "fingerprint": current_fingerprint, "rows": rows},
            ensure_ascii=False,
        )
        if len(encoded.encode("utf-8")) > MAX_CACHE_BYTES:
            return
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(f"{CACHE_FILE_NAME}.{os.getpid()}.tmp")
        temporary.write_text(encoded, encoding="utf-8")
        os.replace(temporary, target)
    except (OSError, ValueError, TypeError, UnicodeError):
        return