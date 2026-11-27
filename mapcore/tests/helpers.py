"""Shared test helpers (stdlib unittest compatible)."""

from __future__ import annotations

from pathlib import Path

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def fixture(name: str) -> Path:
    path = FIXTURES / name
    if not path.is_file():
        raise AssertionError(f"missing test fixture: {path}")
    return path
