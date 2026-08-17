#!/usr/bin/env python3
"""Dump link/scores.parquet to JSON, keyed by the unordered pair.

Test scaffolding for tests/link_score.nf.test. §3.2's done-when is a claim
about two match WEIGHTS, which live in a parquet file with a nested
per_field_agreement column -- so the assertion needs a reader, and the
honest reader is the same duckdb that wrote the file rather than a
reimplementation in Groovy that could agree with a bug.

Reading it here also exercises the nested column end to end: if the
LIST(STRUCT) did not survive the write, `levels` comes back empty and the
test's level assertions fail rather than silently passing on an absent key.

Usage:
    python3 tests/fixtures/read_scores.py <scores.parquet>
"""
from __future__ import annotations

import json
import sys

import duckdb


def main(argv: list[str]) -> int:
    con = duckdb.connect()
    rows = con.execute(
        "SELECT left_id, right_id, match_weight, per_field_agreement FROM read_parquet(?)", [argv[1]]
    ).fetchall()
    out = {}
    for left, right, weight, per_field in rows:
        key = "|".join(sorted([left, right]))
        out[key] = {
            "weight": weight,
            "levels": {f["field"]: f["level"] for f in (per_field or [])},
            "contributions": {f["field"]: f["weight"] for f in (per_field or [])},
        }
    json.dump(out, sys.stdout)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
