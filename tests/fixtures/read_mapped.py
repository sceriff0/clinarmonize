#!/usr/bin/env python3
"""Dump a mapped/<table>.parquet (or mapped/_unmapped.parquet) to JSON.

Test scaffolding for tests/map_concepts.nf.test. §6.1's done-when is a claim
about a COLUMN of a parquet file ("that count is zero"), and §6.1's contract
is a claim about which columns exist at all -- so the assertion needs a
reader that reports the schema as well as the rows.

Same reasoning as tests/fixtures/read_scores.py: the honest reader is the
duckdb that wrote the file, not a reimplementation in Groovy that could
agree with a bug. It also means a column that silently failed to survive the
write comes back ABSENT from `columns` and the schema assertions go red,
rather than a `null` that a `is null` count would have to interpret.

Usage:
    python3 tests/fixtures/read_mapped.py <table.parquet>

Emits {"columns": [...], "rows": [ {col: value, ...}, ... ]} on stdout, with
rows in the file's own stored order (never re-sorted here -- §6.1's output
order is part of what a byte-determinism claim would rest on, so a reader
that quietly re-orders would hide a change in it).
"""
from __future__ import annotations

import json
import sys

import duckdb


def _jsonable(value):
    # A DECIMAL/TIMESTAMP/UUID round-trips through duckdb as a Python object
    # json cannot encode. Stringify rather than drop: a test asserting on a
    # datetime needs to see SOMETHING, and a silent drop reads as "the
    # column is null" which is a different claim entirely.
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, list):
        return [_jsonable(v) for v in value]
    return str(value)


def main(argv: list[str]) -> int:
    con = duckdb.connect()
    cursor = con.execute("SELECT * FROM read_parquet(?)", [argv[1]])
    columns = [d[0] for d in cursor.description]
    rows = [{col: _jsonable(val) for col, val in zip(columns, row)} for row in cursor.fetchall()]
    json.dump({"columns": columns, "rows": rows}, sys.stdout)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
