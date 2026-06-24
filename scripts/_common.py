"""Shared helpers for Loopita scripts.

Path resolution, atomic JSON read/write, and JSONL append/replace-by-key.
Standard library only. See references/conventions.md for the file layout.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any, Callable, Iterable

DEFAULT_HOME = ".loopita"


# --- path resolution -------------------------------------------------------


def home_dir(override: str | None = None) -> Path:
    """Resolve $LOOPITA_HOME: explicit override > env var > default."""
    if override:
        return Path(override)
    return Path(os.environ.get("LOOPITA_HOME", DEFAULT_HOME))


def run_dir(home: Path, run_id: str) -> Path:
    return home / "runs" / run_id


def agent_dir(home: Path, run_id: str, agent_id: str) -> Path:
    return run_dir(home, run_id) / "agents" / agent_id


def persistent_learnings_path(home: Path) -> Path:
    return home / "learnings" / "persistent.md"


# --- atomic JSON / text io -------------------------------------------------


def read_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def _atomic_write(path: Path, data: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # Write to a temp file in the same dir, then atomically replace.
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(data)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def write_json(path: Path, obj: dict[str, Any]) -> None:
    _atomic_write(path, json.dumps(obj, indent=2, ensure_ascii=False) + "\n")


def write_text(path: Path, text: str) -> None:
    _atomic_write(path, text)


# --- JSONL helpers ---------------------------------------------------------


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    out: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


def append_jsonl(path: Path, obj: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(obj, ensure_ascii=False) + "\n")


def upsert_jsonl(path: Path, obj: dict[str, Any], key: str) -> None:
    """Append obj, or replace the existing line whose `key` field matches.

    Rewrites the whole file atomically to keep one line per key.
    """
    rows = read_jsonl(path)
    replaced = False
    for i, row in enumerate(rows):
        if row.get(key) == obj.get(key):
            rows[i] = obj
            replaced = True
            break
    if not replaced:
        rows.append(obj)
    data = "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows)
    _atomic_write(path, data)


def patch_jsonl(
    path: Path, key: str, key_value: Any, patch: dict[str, Any]
) -> dict[str, Any]:
    """Patch fields on the line matching key==key_value. Returns the row.

    Raises KeyError if no matching line exists.
    """
    rows = read_jsonl(path)
    for i, row in enumerate(rows):
        if row.get(key) == key_value:
            row.update(patch)
            rows[i] = row
            data = "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows)
            _atomic_write(path, data)
            return row
    raise KeyError(f"no {key}={key_value!r} in {path}")


# --- CLI plumbing ----------------------------------------------------------


def emit(obj: Any) -> None:
    """Print a JSON result to stdout."""
    print(json.dumps(obj, ensure_ascii=False, indent=2))


def fail(message: str, code: int = 1) -> "NoReturn":  # type: ignore[name-defined]
    """Print a JSON error to stderr and exit non-zero."""
    print(json.dumps({"error": message}, ensure_ascii=False), file=sys.stderr)
    sys.exit(code)


def run_selftest(fn: Callable[[Path], None]) -> None:
    """Run `fn` against a throwaway temp home, report pass/fail, clean up."""
    import shutil

    tmp = Path(tempfile.mkdtemp(prefix="loopita-selftest-"))
    try:
        fn(tmp)
    except BaseException as exc:  # noqa: BLE001 - selftest reports any failure
        shutil.rmtree(tmp, ignore_errors=True)
        fail(f"selftest failed: {exc}")
    shutil.rmtree(tmp, ignore_errors=True)
    emit({"selftest": "passed"})


def split_csv(value: str | None) -> list[str]:
    """Parse a comma-separated CLI arg into a clean list."""
    if not value:
        return []
    return [item.strip() for item in value.split(",") if item.strip()]
