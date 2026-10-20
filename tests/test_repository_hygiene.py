"""Guards that keep data-bearing and secret-bearing files out of the repository.

A SQLite database was committed once already. These checks fail fast if that
happens again, before the file reaches a remote.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
FORBIDDEN_SUFFIXES = (".db", ".db-wal", ".db-shm", ".sqlite3", ".pem", ".key", ".p12", ".pfx")
ALLOWED_NAMES = {".env.example"}


def tracked_files() -> list[str]:
    try:
        result = subprocess.run(
            ["git", "ls-files", "-z"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:  # pragma: no cover - environment specific
        pytest.skip(f"git metadata is unavailable: {exc}")
    return [path for path in result.stdout.split("\0") if path]


def test_repository_does_not_track_database_or_key_material() -> None:
    offenders = [
        path
        for path in tracked_files()
        if path.endswith(FORBIDDEN_SUFFIXES)
        or (Path(path).name.startswith(".env") and Path(path).name not in ALLOWED_NAMES)
    ]

    assert offenders == [], (
        "Remove these from git history and rotate any secrets they contained: "
        + ", ".join(offenders)
    )
