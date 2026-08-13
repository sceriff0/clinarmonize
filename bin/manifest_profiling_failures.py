#!/usr/bin/env python3
"""§2.3 — the run-level half of the FailurePolicy seam.

PROFILE_COLUMNS (bin/profile_columns.py) decides skip-vs-fail PER COLUMN
(always 'skip' today: manifest, never drop). This script decides pass-vs-fail
for the RUN: it collates every dataset's per-column failure manifest into the
single profiles/_failed.json the contract names, and enforces the SIDE
clause -- "exit non-zero when the failure RATE exceeds --max_failed_frac" --
which is a property of every column seen across the whole run, not of any one
dataset, so it can only run after every dataset has been profiled.
"""
from __future__ import annotations

import argparse
import glob
import json
import sys


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--profile-glob", required=True)
    ap.add_argument("--failed-glob", required=True)
    ap.add_argument("--max-failed-frac", required=True, type=float)
    ap.add_argument("--out-failed", required=True)
    args = ap.parse_args(argv)

    all_failures: list[dict] = []
    total_failed = 0
    for path in sorted(glob.glob(args.failed_glob)):
        with open(path, encoding="utf-8") as fh:
            entries = json.load(fh)
        total_failed += len(entries)
        all_failures.extend(entries)

    total_profiled = 0
    for path in sorted(glob.glob(args.profile_glob)):
        with open(path, encoding="utf-8") as fh:
            entries = json.load(fh)
        total_profiled += len(entries)

    with open(args.out_failed, "w", encoding="utf-8") as fh:
        json.dump(all_failures, fh, indent=2, sort_keys=True)
        fh.write("\n")

    total_columns = total_failed + total_profiled
    frac = 0.0 if total_columns == 0 else total_failed / total_columns
    if frac > args.max_failed_frac:
        print(
            f"Refusing to continue: {total_failed}/{total_columns} columns "
            f"({frac * 100:.1f}%) failed to profile across this run, exceeding "
            f"--max_failed_frac {args.max_failed_frac}. See {args.out_failed} "
            "for the failure manifest.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
