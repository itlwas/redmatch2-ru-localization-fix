#!/usr/bin/env python3
"""Redmatch 2 — Russian Localization Patcher.

Updates the ``russian`` column in the game's ``localization.csv`` using
translations from a companion ``LocalizationRemake.csv``.

Design goals
------------
* **Byte-faithful output.** The game's CSV parser is strict. The tool
  never injects a BOM, never rewrites line endings, and never adds,
  removes, or changes the trailing byte sequence of the file.
* **Atomic write.** The patched content is staged in a sibling ``.tmp``
  file and swapped into place with :func:`os.replace`. The staging file
  is cleaned up on any failure.
* **Safe by default.** A timestamped backup of the target is created
  before the swap, unless ``--no-backup`` is passed. ``--dry-run``
  reports planned changes without touching disk.
* **Actionable diagnostics.** The final report surfaces quality issues
  in the remake file: duplicate keys, key-less (orphan) rows, target
  keys missing from the remake.

Exit codes
----------
* ``0`` — success (including dry-run).
* ``1`` — runtime error (missing/malformed file, permission denied, …).
* ``2`` — argparse usage error.
"""

from __future__ import annotations

import argparse
import csv
import io
import logging
import os
import shutil
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

__all__ = ["Config", "PatchReport", "load_russian_map", "patch", "main"]


# --------------------------------------------------------------------- #
# Constants
# --------------------------------------------------------------------- #

# Read with "utf-8-sig" so any leading BOM is transparently stripped.
# Write with plain "utf-8" so no BOM is ever emitted.
READ_ENCODING = "utf-8-sig"
WRITE_ENCODING = "utf-8"

TEXT_KEY_COLUMN = "Text Key (internal use only)"
RUSSIAN_COLUMN = "russian"
REMAKE_FILENAME = "LocalizationRemake.csv"

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_USAGE = 2

log = logging.getLogger(__name__)


# --------------------------------------------------------------------- #
# Data classes
# --------------------------------------------------------------------- #


@dataclass(frozen=True)
class Config:
    """Immutable configuration for a single patch run."""

    target_csv: Path
    remake_csv: Path
    allow_empty_replacement: bool = False
    dry_run: bool = False
    make_backup: bool = True
    backup_dir: Path | None = None


@dataclass
class PatchReport:
    """Outcome of a patch run, safe to inspect and print."""

    target_rows: int = 0
    matched_rows: int = 0
    changed_rows: int = 0
    unchanged_rows: int = 0
    skipped_empty: int = 0
    missing_in_remake: list[str] = field(default_factory=list)
    remake_duplicates: dict[str, int] = field(default_factory=dict)
    remake_orphan_rows: int = 0
    backup_path: Path | None = None
    dry_run: bool = False


# --------------------------------------------------------------------- #
# Low-level helpers
# --------------------------------------------------------------------- #


def _normalize(name: str) -> str:
    return (name or "").strip().lower()


def _find_column(header: list[str], expected: str) -> int:
    target = _normalize(expected)
    for idx, col in enumerate(header):
        if _normalize(col) == target:
            return idx
    raise ValueError(f"Required column not found in header: {expected!r}")


def _cell(row: list[str], idx: int) -> str:
    return row[idx] if 0 <= idx < len(row) else ""


def _pad(row: list[str], length: int) -> None:
    if len(row) < length:
        row.extend([""] * (length - len(row)))


def _trailing_terminator(path: Path) -> str:
    """Return the literal EOF byte sequence of ``path``.

    Possible return values: ``""``, ``"\\n"``, ``"\\r\\n"``, ``"\\r"``.
    Used to replicate the target file's exact EOF convention instead of
    forcing ``csv.writer``'s default terminator.
    """
    try:
        size = path.stat().st_size
    except OSError:
        return ""
    if size == 0:
        return ""
    try:
        with path.open("rb") as fp:
            fp.seek(max(0, size - 2), os.SEEK_SET)
            tail = fp.read(2)
    except OSError:
        return ""
    if tail.endswith(b"\r\n"):
        return "\r\n"
    if tail.endswith(b"\n"):
        return "\n"
    if tail.endswith(b"\r"):
        return "\r"
    return ""


# --------------------------------------------------------------------- #
# Core
# --------------------------------------------------------------------- #


def load_russian_map(
    remake_csv: Path,
) -> tuple[dict[str, str], dict[str, int], int]:
    """Load translations from the remake CSV.

    Returns
    -------
    ru_map
        ``key -> russian value``. Last occurrence wins on duplicates, to
        keep behaviour stable across runs.
    duplicates
        ``key -> count`` for keys that appear more than once.
    orphan_count
        Number of rows that carry non-empty data but no key. They cannot
        be applied and are reported so the translator can fix them.
    """
    counts: dict[str, int] = defaultdict(int)
    ru_map: dict[str, str] = {}
    orphan_count = 0

    with remake_csv.open("r", encoding=READ_ENCODING, newline="") as fp:
        reader = csv.reader(fp)
        try:
            header = next(reader)
        except StopIteration as exc:
            raise ValueError(f"Remake CSV is empty: {remake_csv}") from exc

        key_idx = _find_column(header, TEXT_KEY_COLUMN)
        ru_idx = _find_column(header, RUSSIAN_COLUMN)

        for row in reader:
            key = _cell(row, key_idx).strip()
            if not key:
                if any(cell.strip() for cell in row):
                    orphan_count += 1
                continue
            counts[key] += 1
            ru_map[key] = _cell(row, ru_idx)

    duplicates = {k: n for k, n in counts.items() if n > 1}
    return ru_map, duplicates, orphan_count


def _build_patched_text(
    cfg: Config,
    ru_map: dict[str, str],
    report: PatchReport,
) -> str:
    """Stream target through csv.reader/csv.writer, applying patches.

    The file is ~300 KB; buffering in memory keeps the write atomic and
    lets us normalise the EOF byte sequence before flushing.
    """
    buffer = io.StringIO(newline="")

    with cfg.target_csv.open("r", encoding=READ_ENCODING, newline="") as fin:
        reader = csv.reader(fin)
        writer = csv.writer(buffer)

        try:
            header = next(reader)
        except StopIteration as exc:
            raise ValueError(
                f"Target file is empty: {cfg.target_csv}"
            ) from exc

        key_idx = _find_column(header, TEXT_KEY_COLUMN)
        ru_idx = _find_column(header, RUSSIAN_COLUMN)
        writer.writerow(header)

        for row in reader:
            report.target_rows += 1
            key = _cell(row, key_idx).strip()
            new_ru = ru_map.get(key) if key else None

            if key and new_ru is None:
                report.missing_in_remake.append(key)

            if new_ru is not None:
                report.matched_rows += 1
                if new_ru == "" and not cfg.allow_empty_replacement:
                    report.skipped_empty += 1
                else:
                    _pad(row, ru_idx + 1)
                    if row[ru_idx] != new_ru:
                        row[ru_idx] = new_ru
                        report.changed_rows += 1
                    else:
                        report.unchanged_rows += 1

            writer.writerow(row)

    text = buffer.getvalue()

    # csv.writer always appends lineterminator after the last row.
    # Replace it with the target file's actual trailing byte sequence
    # so we never alter EOF convention (including "no terminator").
    writer_term = writer.dialect.lineterminator
    target_term = _trailing_terminator(cfg.target_csv)
    if text.endswith(writer_term) and writer_term != target_term:
        text = text[: -len(writer_term)] + target_term

    return text


def _backup(path: Path, backup_dir: Path | None) -> Path:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_name = f"{path.stem}.bak_{timestamp}{path.suffix}"
    directory = backup_dir if backup_dir is not None else path.parent
    directory.mkdir(parents=True, exist_ok=True)
    backup_path = directory / backup_name
    shutil.copy2(path, backup_path)
    return backup_path


def _atomic_write(target: Path, text: str) -> None:
    # Use ``with_name`` (not ``with_suffix``) to avoid path suffix rules
    # about multi-dot suffixes.
    tmp_path = target.with_name(target.name + ".tmp")
    try:
        with tmp_path.open("w", encoding=WRITE_ENCODING, newline="") as fout:
            fout.write(text)
        os.replace(tmp_path, target)
    except BaseException:
        # Never leave a stray staging file behind.
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise


def patch(cfg: Config) -> PatchReport:
    """Apply the patch according to ``cfg`` and return the report."""
    if not cfg.target_csv.exists():
        raise FileNotFoundError(f"Target file not found: {cfg.target_csv}")
    if not cfg.remake_csv.exists():
        raise FileNotFoundError(f"Remake file not found: {cfg.remake_csv}")

    log.info("Loading translations from %s", cfg.remake_csv)
    ru_map, duplicates, orphan_count = load_russian_map(cfg.remake_csv)
    log.info("Loaded %d unique keys from remake", len(ru_map))

    report = PatchReport(
        remake_duplicates=duplicates,
        remake_orphan_rows=orphan_count,
        dry_run=cfg.dry_run,
    )

    log.info("Scanning target %s", cfg.target_csv)
    text = _build_patched_text(cfg, ru_map, report)

    if cfg.dry_run:
        log.info("Dry run: not writing to disk")
        return report

    if cfg.make_backup:
        report.backup_path = _backup(cfg.target_csv, cfg.backup_dir)
        log.info("Backup written: %s", report.backup_path)

    _atomic_write(cfg.target_csv, text)
    log.info("Target updated: %s", cfg.target_csv)
    return report


# --------------------------------------------------------------------- #
# Reporting
# --------------------------------------------------------------------- #


def print_report(report: PatchReport, *, verbose: bool = False) -> None:
    """Render ``report`` on stdout in a stable, human-readable layout."""
    banner = "DRY RUN" if report.dry_run else "DONE"
    print(f"[{banner}]")
    if report.backup_path is not None:
        print(f"  Backup:                 {report.backup_path}")
    print(f"  Target rows scanned:    {report.target_rows}")
    print(f"  Rows matched by key:    {report.matched_rows}")
    print(f"  Rows updated:           {report.changed_rows}")
    print(f"  Rows already current:   {report.unchanged_rows}")
    if report.skipped_empty:
        print(f"  Skipped (empty value):  {report.skipped_empty}")

    missing = len(report.missing_in_remake)
    if missing:
        print(f"  Target keys missing from remake: {missing}")
        if verbose:
            for key in report.missing_in_remake:
                print(f"      - {key}")

    if report.remake_duplicates:
        dup_n = len(report.remake_duplicates)
        print(
            f"  Duplicate keys in remake: {dup_n} "
            "(last occurrence wins)"
        )
        for key, count in sorted(report.remake_duplicates.items()):
            print(f"      - {key!r} x{count}")

    if report.remake_orphan_rows:
        print(
            f"  Orphan rows in remake (no key, skipped): "
            f"{report.remake_orphan_rows}"
        )


# --------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------- #


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="script.py",
        description=(
            "Patch the 'russian' column of Redmatch 2's localization.csv "
            "using translations from LocalizationRemake.csv."
        ),
    )
    parser.add_argument(
        "target",
        type=Path,
        help="Path to the game's localization.csv to be patched.",
    )
    parser.add_argument(
        "--remake",
        type=Path,
        default=None,
        metavar="PATH",
        help=(
            "Path to the remake CSV "
            f"(default: {REMAKE_FILENAME} next to this script)."
        ),
    )
    parser.add_argument(
        "--backup-dir",
        type=Path,
        default=None,
        metavar="DIR",
        help=(
            "Write the timestamped backup to this directory instead of "
            "next to the target (directory is created if needed)."
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report what would change without writing to disk.",
    )
    parser.add_argument(
        "--no-backup",
        action="store_true",
        help="Skip creating a backup of the target.",
    )
    parser.add_argument(
        "--allow-empty",
        action="store_true",
        help=(
            "Apply empty russian values to the target "
            "(clears the existing translation)."
        ),
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Enable verbose logging and list every missing key.",
    )
    return parser


def _configure_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.WARNING
    logging.basicConfig(
        level=level,
        format="%(levelname)s: %(message)s",
    )


def _permission_hint(path: Path) -> str:
    if os.name == "nt":
        return (
            f"Cannot write to {path}. This directory usually requires "
            "administrator privileges. Re-run from an elevated "
            "terminal (Run as administrator)."
        )
    return (
        f"Cannot write to {path}. Check filesystem permissions or "
        "run with sufficient privileges."
    )


def main(argv: list[str] | None = None) -> int:
    parser = _build_arg_parser()
    args = parser.parse_args(argv)

    _configure_logging(args.verbose)

    default_remake = Path(__file__).resolve().parent / REMAKE_FILENAME
    remake_csv = (args.remake or default_remake).expanduser().resolve()
    target_csv = args.target.expanduser().resolve()
    backup_dir = (
        args.backup_dir.expanduser().resolve()
        if args.backup_dir is not None
        else None
    )

    cfg = Config(
        target_csv=target_csv,
        remake_csv=remake_csv,
        allow_empty_replacement=args.allow_empty,
        dry_run=args.dry_run,
        make_backup=not args.no_backup,
        backup_dir=backup_dir,
    )

    try:
        report = patch(cfg)
    except FileNotFoundError as exc:
        log.error("%s", exc)
        return EXIT_ERROR
    except ValueError as exc:
        log.error("Invalid CSV: %s", exc)
        return EXIT_ERROR
    except PermissionError as exc:
        log.error("Permission denied: %s", exc)
        log.error("%s", _permission_hint(cfg.target_csv))
        return EXIT_ERROR
    except OSError as exc:
        log.error("I/O error: %s", exc)
        return EXIT_ERROR

    print_report(report, verbose=args.verbose)
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
