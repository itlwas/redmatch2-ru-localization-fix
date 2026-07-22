#!/usr/bin/env python3
"""Patch the 'russian' column in localization.csv using LocalizationRemake.csv."""

from __future__ import annotations

import csv
import io
import sys
from datetime import datetime
from pathlib import Path

KEY_COL = "Text Key (internal use only)"
RU_COL = "russian"
REMAKE_PATH = Path(__file__).resolve().parent / "LocalizationRemake.csv"


def read_csv(path: Path) -> tuple[bytes, list[list[str]]]:
    raw = path.read_bytes()
    rows = list(csv.reader(io.StringIO(raw.decode("utf-8-sig"), newline="")))
    if not rows:
        raise ValueError(f"empty file: {path}")
    return raw, rows


def require_columns(header: list[str], path: Path) -> tuple[int, int]:
    names = [h.strip().lower() for h in header]
    indices = []
    for name in (KEY_COL, RU_COL):
        if name.lower() not in names:
            raise ValueError(f"column {name!r} not found in {path}")
        indices.append(names.index(name.lower()))
    return indices[0], indices[1]


def cell(row: list[str], idx: int) -> str:
    return row[idx] if idx < len(row) else ""


def load_translations(path: Path) -> dict[str, str]:
    _, (header, *rows) = read_csv(path)
    key_idx, ru_idx = require_columns(header, path)
    return {
        cell(row, key_idx).strip(): cell(row, ru_idx)
        for row in rows
        if cell(row, key_idx).strip()
    }


def apply_patch(header: list[str], rows: list[list[str]], ru_map: dict[str, str], path: Path) -> int:
    key_idx, ru_idx = require_columns(header, path)
    changed = 0
    for row in rows:
        new_value = ru_map.get(cell(row, key_idx).strip())
        if new_value is None:
            continue
        if len(row) <= ru_idx:
            row.extend([""] * (ru_idx + 1 - len(row)))
        if row[ru_idx] != new_value:
            row[ru_idx] = new_value
            changed += 1
    return changed


def main() -> int:
    if len(sys.argv) != 2:
        print("Usage: python script.py <path to localization.csv>")
        return 1

    target = Path(sys.argv[1]).expanduser().resolve()
    if not target.exists():
        print(f"File not found: {target}")
        return 1
    if not REMAKE_PATH.exists():
        print(f"File not found: {REMAKE_PATH}")
        return 1

    try:
        ru_map = load_translations(REMAKE_PATH)
        raw, (header, *rows) = read_csv(target)
        changed = apply_patch(header, rows, ru_map, target)
    except (OSError, ValueError, UnicodeDecodeError) as exc:
        print(f"Error: {exc}")
        return 1

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup = target.with_name(f"{target.stem}.bak_{stamp}{target.suffix}")

    try:
        backup.write_bytes(raw)
        with target.open("w", encoding="utf-8", newline="") as f:
            csv.writer(f).writerows([header, *rows])
    except OSError as exc:
        print(f"Error: {exc}")
        print("Make sure the game is closed and the file is not in use.")
        return 1

    print(f"Rows updated: {changed}")
    print(f"Backup created: {backup.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
