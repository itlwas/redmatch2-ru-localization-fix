#!/usr/bin/env python3
"""Redmatch 2 — RU Localization Fix.

Скрипт обновляет колонку `russian` в целевом `localization.csv`, используя
значения из `LocalizationRemake.csv`, лежащего рядом со скриптом.

Перед перезаписью целевого файла создаётся резервная копия с таймстампом.
"""

from __future__ import annotations

import csv
import shutil
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

CSV_ENCODING = "utf-8-sig"
HELP_FLAGS = {"-h", "--help", "/?"}

TEXT_KEY_COLUMN = "Text Key (internal use only)"
RUSSIAN_COLUMN = "russian"


@dataclass(frozen=True)
class Config:
    target_csv: Path
    remake_csv: Path
    allow_empty_replacement: bool = False


def normalize_header(name: str) -> str:
    return (name or "").strip().lower()


def find_column_index(header: list[str], expected: str) -> int:
    expected_norm = normalize_header(expected)
    for idx, col in enumerate(header):
        if normalize_header(col) == expected_norm:
            return idx
    raise ValueError(f"Required column not found: {expected!r}")


def safe_get(row: list[str], idx: int) -> str:
    return row[idx] if idx < len(row) else ""


def ensure_row_length(row: list[str], length: int) -> None:
    if len(row) < length:
        row.extend([""] * (length - len(row)))


def load_ru_map(remake_csv: Path) -> dict[str, str]:
    ru_map: dict[str, str] = {}

    with remake_csv.open("r", encoding=CSV_ENCODING, newline="") as fp:
        reader = csv.reader(fp)
        header = next(reader)

        key_idx = find_column_index(header, TEXT_KEY_COLUMN)
        ru_idx = find_column_index(header, RUSSIAN_COLUMN)

        for row in reader:
            key = safe_get(row, key_idx).strip()
            if not key:
                continue
            ru_map[key] = safe_get(row, ru_idx)

    return ru_map


def backup_file(path: Path) -> Path:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = path.with_suffix(f".bak_{timestamp}{path.suffix}")
    shutil.copy2(path, backup_path)
    return backup_path


def patch_russian_column(cfg: Config) -> tuple[Path, int, int]:
    if not cfg.target_csv.exists():
        raise FileNotFoundError(f"Target file not found: {cfg.target_csv}")
    if not cfg.remake_csv.exists():
        raise FileNotFoundError(f"Remake file not found: {cfg.remake_csv}")

    ru_map = load_ru_map(cfg.remake_csv)
    backup_path = backup_file(cfg.target_csv)
    tmp_path = cfg.target_csv.with_suffix(".tmp")

    changed_rows = 0
    missing_keys = 0

    with cfg.target_csv.open("r", encoding=CSV_ENCODING, newline="") as fin, tmp_path.open(
        "w",
        encoding=CSV_ENCODING,
        newline="",
    ) as fout:
        reader = csv.reader(fin)
        writer = csv.writer(fout)

        header = next(reader)
        writer.writerow(header)

        key_idx = find_column_index(header, TEXT_KEY_COLUMN)
        ru_idx = find_column_index(header, RUSSIAN_COLUMN)

        for row in reader:
            key = safe_get(row, key_idx).strip()
            new_ru = ru_map.get(key)

            if key and new_ru is None:
                missing_keys += 1

            if new_ru is not None and (cfg.allow_empty_replacement or new_ru != ""):
                ensure_row_length(row, ru_idx + 1)
                if row[ru_idx] != new_ru:
                    row[ru_idx] = new_ru
                    changed_rows += 1

            writer.writerow(row)

    tmp_path.replace(cfg.target_csv)
    return backup_path, changed_rows, missing_keys


def usage(script_name: str) -> str:
    return "Usage:\n" f'  py {script_name} "C:\\path\\to\\localization.csv"\n'


def main(argv: list[str]) -> int:
    script_name = Path(argv[0]).name

    if len(argv) != 2:
        print(usage(script_name))
        return 2

    if argv[1] in HELP_FLAGS:
        print(usage(script_name))
        return 0

    target_csv = Path(argv[1])
    remake_csv = Path(__file__).resolve().parent / "LocalizationRemake.csv"
    cfg = Config(
        target_csv=target_csv,
        remake_csv=remake_csv,
        allow_empty_replacement=False,
    )

    try:
        backup_path, changed, missing = patch_russian_column(cfg)
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    print("Done.")
    print(f"Backup created: {backup_path}")
    print(f"Russian rows updated: {changed}")
    print(f"Keys missing in remake: {missing}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
