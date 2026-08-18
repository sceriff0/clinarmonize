#!/usr/bin/env python3
"""§10.1 / ADR-004 — a canonical content digest of a published artefact directory.

What this exists for. Widening `--invariant_scope` to 'map' means the harness's
claim now covers `mapped/`, so a replicate has to reduce to ONE comparable
value the way `sha256(ledger.proposed.yaml)` already does for the proposer. The
obvious move -- hash the parquet bytes -- is wrong, and ADR-004 says why:

    Parquet embeds writer metadata, and a byte digest would make the harness
    sensitive to the encoder rather than to the data -- the same class of
    mistake §4.3's rounding and top-k truncation exist to prevent for the
    ledger ("a float's last bit cannot change the hash").

A byte digest would go red on a duckdb upgrade, a compression-codec change, or
a row-group boundary moving, and every one of those reads as `leak` in the
report. A harness that cries leak for reasons that are not leaks gets muted,
and a muted harness is worse than no harness.

So the digest is taken over the DATA, read back through duckdb:

  * per file, in filename order, so the set of tables is part of the digest --
    a table that stops being written is a change, not an absence;
  * columns in declared order, WITH their types -- retyping a column from
    VARCHAR to BIGINT is a real change to what the artefact means, and a
    values-only digest would call it identical;
  * floats rounded at --float-precision (the same `ledger_float_precision`
    §4.3 rounds its ledger at), so a last-bit difference between two runs of
    the same arithmetic cannot move the digest;
  * rows sorted by their own canonical encoding, so ROW ORDER is not part of
    the digest. Two runs that wrote the same rows in a different order wrote
    the same table. Duplicates survive the sort, so this is a multiset
    comparison and not a set one -- a row emitted twice is not the same
    artefact as a row emitted once.

Injectivity of the row encoding, because this is the part that silently goes
wrong. Fields are joined with US (chr(31)), and every field is tagged before
joining: 'V' + value for a present value, 'N' for SQL NULL. Without the tag,
the row ("a", NULL) and the row (NULL, "a") both encode to something
indistinguishable from "a" beside an empty string, and a leak that moved a
value from one column to another would hash identically. With it, no two
distinct rows share an encoding unless two distinct values share one, which
US-joining already rules out for anything a CSV can carry.

The per-table digests are reported alongside the artefact digest, not folded
away. A `leak` verdict is read by someone who has to find out WHICH table
moved, and 'the digests differ' is not an answer to that question.
"""
from __future__ import annotations

import argparse
import glob
import hashlib
import json
import os
import sys

import duckdb

# The field separator inside one row's canonical encoding, and the tags that
# make that encoding injective (see the module docstring). US (unit separator)
# rather than a printable character because no CSV-derived value carries it.
_US = chr(31)

# Types whose default CAST to VARCHAR is not stable enough to hash. Everything
# else -- VARCHAR, BIGINT, BOOLEAN, DATE, and the LIST/STRUCT columns §3 writes
# -- goes through a plain CAST, which duckdb renders deterministically.
_FLOAT_TYPES = {"FLOAT", "DOUBLE", "REAL"}


def _canonical_expr(name: str, type_name: str, float_precision: int) -> str:
    """One column, as the canonical VARCHAR that goes into the row encoding."""
    quoted = '"' + name.replace('"', '""') + '"'
    base = type_name.upper()

    if base in _FLOAT_TYPES or base.startswith("DECIMAL"):
        # printf rather than round(): round() returns a DOUBLE whose CAST to
        # VARCHAR is still a shortest-round-trip rendering, so 0.1 + 0.2 and
        # 0.3 would round to the same number and then print differently.
        # Fixing the rendering to N decimal places is what actually makes the
        # last bit unable to move the digest.
        rendered = f"printf('%.{float_precision}f', {quoted})"
    elif base.startswith("TIMESTAMP"):
        # An explicit format, so a duckdb default that drops a zero
        # sub-second component on one run and not another cannot register as
        # a change. Microseconds is the precision the round-trip probe
        # (tools/parquet_roundtrip_probe.py) covers.
        rendered = f"strftime({quoted}, '%Y-%m-%dT%H:%M:%S.%f')"
    else:
        rendered = f"CAST({quoted} AS VARCHAR)"

    return f"CASE WHEN {quoted} IS NULL THEN 'N' ELSE 'V' || {rendered} END"


def digest_table(
    con: duckdb.DuckDBPyConnection, path: str, float_precision: int
) -> dict:
    """Digest ONE parquet file. Streams rows rather than fetching them all:
    the fixtures here are tiny, but this same code is what a real
    `--invariant_scope map` run would point at a production mapped/, and a
    digest that only works below some row count is a digest that stops being
    run."""
    described = con.execute(
        f"DESCRIBE SELECT * FROM read_parquet('{path}')"
    ).fetchall()
    columns = [(row[0], row[1]) for row in described]

    header = "|".join(f"{name}:{type_name}" for name, type_name in columns)

    h = hashlib.sha256()
    h.update(f"columns\x1e{header}\n".encode())

    n_rows = 0
    if columns:
        exprs = ", ".join(
            _canonical_expr(name, type_name, float_precision) for name, type_name in columns
        )
        row_expr = f"concat_ws(chr(31), {exprs})"
        # ORDER BY the encoding itself, so row order is not part of the
        # digest and the ordering rule needs no per-table knowledge of which
        # column is the key. Duplicate rows are preserved by the sort, which
        # keeps this a multiset comparison.
        cur = con.execute(
            f"SELECT {row_expr} AS __row FROM read_parquet('{path}') ORDER BY __row"
        )
        while True:
            batch = cur.fetchmany(10_000)
            if not batch:
                break
            for (encoded,) in batch:
                n_rows += 1
                # A row that is entirely NULL encodes to 'N\x1fN\x1f...',
                # never to the empty string, so concat_ws cannot collapse it.
                h.update((encoded if encoded is not None else "").encode("utf-8", "surrogateescape"))
                h.update(b"\n")

    return {
        "table": os.path.basename(path),
        "n_rows": n_rows,
        "columns": [{"name": name, "type": type_name} for name, type_name in columns],
        "digest": h.hexdigest(),
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--glob",
        required=True,
        help="Glob naming every parquet file in the artefact. Sorted here, so "
        "the caller's staging order cannot reach the digest.",
    )
    ap.add_argument(
        "--artefact",
        required=True,
        help="What is being digested ('mapped/'), recorded in the output so a "
        "report naming a digest also names what it is a digest OF.",
    )
    ap.add_argument(
        "--float-precision",
        required=True,
        type=int,
        help="Decimal places every float is rendered at -- params.ledger_float_precision, "
        "the same rounding §4.3 applies to the ledger, so the two artefacts in "
        "one scope are insensitive to the same last bit.",
    )
    ap.add_argument("--out-digest", required=True)
    args = ap.parse_args(argv)

    paths = sorted(glob.glob(args.glob))
    if not paths:
        # Not an empty digest. An artefact directory with no tables in it is a
        # stage that did not run, and hashing "nothing" to a fixed value would
        # let 100 replicates that all failed agree perfectly.
        print(
            f"artefact_digest: no files matched '{args.glob}' -- refusing to "
            "digest an artefact that was never written.",
            file=sys.stderr,
        )
        return 1

    con = duckdb.connect()
    tables = [digest_table(con, path, args.float_precision) for path in paths]

    # The artefact digest is over the per-table digests AND their names, so a
    # table appearing, disappearing, or being renamed moves it even when every
    # surviving table is byte-identical.
    h = hashlib.sha256()
    h.update(f"artefact\x1e{args.artefact}\n".encode())
    for entry in tables:
        h.update(f"{entry['table']}\x1e{entry['digest']}\n".encode())
    artefact_digest = h.hexdigest()

    report = {
        "artefact": args.artefact,
        "float_precision": args.float_precision,
        "digest": artefact_digest,
        "n_tables": len(tables),
        "tables": tables,
    }
    with open(args.out_digest, "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=2)
        fh.write("\n")

    # Loud on stdout for the same reason bin/invariant_report.py is: when the
    # verdict is 'leak', the per-replicate logs are where the reader goes
    # first, and a digest that only exists inside a JSON file is one more file
    # to find.
    print(f"artefact_digest: {args.artefact} -> {artefact_digest} ({len(tables)} table(s))")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
