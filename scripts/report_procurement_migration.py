#!/usr/bin/env python3
"""Read-only JSON reconciliation for an already initialized procurement database."""

import argparse
from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import sqlite3
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from procurement import procurement_migration_report


@contextmanager
def existing_application_lock(path, *, offline=False):
    if offline:
        print("WARNING: --offline bypasses the application lock; use only a static database copy or a stopped application. Concurrent nolock writers can produce an inconsistent report.", file=sys.stderr)
        yield
        return
    # Never create or write the lock file: its inode must be the writer's lock.
    with path.open("rb") as lock:
        print(f"Acquiring existing application lock: {path}", file=sys.stderr, flush=True)
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", required=True, type=Path)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--lock-path", type=Path, help="Existing application lock file (default: JIEDE_WRITE_LOCK_PATH or /tmp/jiede-web-write.lock); never created by this command")
    mode.add_argument("--offline", action="store_true", help="Bypass the application lock ONLY for a static copy or stopped application; concurrent nolock writers can make the report inconsistent")
    args = parser.parse_args()
    lock_path = args.lock_path or Path(os.getenv("JIEDE_WRITE_LOCK_PATH", "/tmp/jiede-web-write.lock"))
    try:
        with existing_application_lock(lock_path, offline=args.offline):
            # as_uri quotes spaces, # and ?; never create a missing file.
            conn = sqlite3.connect(args.database.resolve().as_uri() + "?mode=ro", uri=True)
            try:
                conn.execute("BEGIN")
                report = procurement_migration_report(conn)
            finally:
                conn.close()
    except OSError as error:
        parser.exit(1, f"Procurement migration report failed: existing application lock unavailable ({lock_path}): {error}. Use --offline only for a static copy or stopped application.\n")
    except (sqlite3.Error, ValueError) as error:
        parser.exit(1, f"Procurement migration report failed: {error}\n")
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
