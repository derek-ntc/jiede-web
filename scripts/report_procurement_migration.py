#!/usr/bin/env python3
"""Read-only JSON reconciliation for an already initialized procurement database."""

import argparse
import json
from pathlib import Path
import sqlite3
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from procurement import procurement_migration_report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", required=True, type=Path)
    args = parser.parse_args()
    try:
        # as_uri quotes spaces, # and ?; never create a missing file.
        conn = sqlite3.connect(args.database.resolve().as_uri() + "?mode=ro", uri=True)
        try:
            conn.execute("BEGIN")  # One consistent read snapshot across all sums.
            report = procurement_migration_report(conn)
        finally:
            conn.close()
    except (sqlite3.Error, ValueError) as error:
        parser.exit(1, f"Procurement migration report failed: {error}\n")
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
