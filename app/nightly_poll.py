"""Run the monitoring poll independently of the Streamlit dashboard."""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

from .monitoring import DEFAULT_DB_PATH, import_csv_files, poll_database


DEFAULT_DATA_DIR = Path(__file__).resolve().parents[1] / "data"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Import source CSVs and run the zoning validation poll."
    )
    parser.add_argument(
        "--db-path",
        type=Path,
        default=DEFAULT_DB_PATH,
        help="SQLite database path (default: data/monitoring.sqlite3)",
    )
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=DEFAULT_DATA_DIR,
        help="Directory containing source CSV files (default: data)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    source_files = sorted(args.data_dir.glob("*.csv"))
    try:
        imported = import_csv_files(source_files, args.db_path)
        result = poll_database(args.db_path)
    except (OSError, sqlite3.Error, ValueError) as exc:
        print(f"Nightly poll failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1

    print(
        f"Nightly poll complete: checked {result['checked']:,}; "
        f"updated {result['updated']:,}; issues {result['review']:,}; "
        f"imported {imported['rows']:,} row(s)."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
