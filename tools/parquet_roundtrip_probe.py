#!/usr/bin/env python3
"""Prove the HOST duckdb can read what the CONTAINER duckdb writes.

Why this exists
---------------
nf-test's parquet assertions shell out to the *host* python3 (see the closure
re-declared in tests/propose_candidates.nf.test and tests/propose_channels.nf.test).
The pipeline writes those parquet files from *inside* the container. So every
containerised run reads one duckdb's output with a different duckdb.

That skew is not a mistake and cannot be closed by pinning:

  * the container is pinned at duckdb 1.5.5 (containers/duckdb-pyyaml/environment.yml);
  * duckdb >= 1.5.0 declares requires_python >= 3.10 and ships no cp39 wheel;
  * the cluster's host interpreter is python 3.9, so `pip install duckdb`
    there resolves to 1.4.5 and *cannot* resolve to 1.5.5.

Pinning both sides to 1.4.5 would mean the container no longer matches its own
canonical environment.yml. Pinning both to 1.5.5 needs a new host interpreter.
And pinning them *equal* would be actively worse than the skew: a reader and a
writer of the same version share their bugs, so a parquet round-trip defect
would round-trip cleanly and the suite would never see it.

So: do not pin. Assert instead. This probe turns "we assume the reader is
compatible" into a measured precondition of the run.

Scope, deliberately narrow
--------------------------
The fixtures below are exactly the two schemas bin/propose_candidates.py and
bin/propose_channels.py emit -- VARCHAR / BIGINT / DOUBLE, plus the awkward
values the assertions actually depend on (NULL in every nullable column, the
empty table, non-ASCII text, INT64 limits, a float just under the ledger's
5e-5 rounding floor). It does not probe types this pipeline does not write.
A probe that fails on a type nobody emits is a probe that gets switched off.

**Widen this the moment a stage writes a nested type.** That moment arrived
with §3: bin/link_score.py writes per_field_agreement as
LIST(STRUCT(field, level, weight)) and bin/link_resolve.py writes
source_row_id as VARCHAR[]. LIST/STRUCT is a genuinely new encoding surface
between the two versions in a way VARCHAR/BIGINT/DOUBLE is not -- nesting
brings its own repetition/definition levels, and an empty list, a NULL list
and a list of one are three different encodings of things that look alike in
a printout. All three are fixtures below.

§2's profiler is still per-column, so no nested type comes from there yet.
Widen again when it does, and when a stage first writes DECIMAL or
TIMESTAMP.

Usage
-----
    python3 tools/parquet_roundtrip_probe.py write <dir>   # in the container
    python3 tools/parquet_roundtrip_probe.py read  <dir>   # on the host
"""

import json
import sys

import duckdb

# (table name, CREATE TABLE body, rows). Both sides read this one definition,
# so "what was written" and "what is expected" cannot drift apart.
FIXTURES = [
    (
        "candidates",
        """cohort_id VARCHAR, dataset_id VARCHAR, "column" VARCHAR, variable VARCHAR,
           concept_id BIGINT, generator_id VARCHAR,
           excluded_candidates BIGINT, excluded_variables VARCHAR""",
        [
            ("c1", "d1", "AGE", "age", 4265453, "exact_name", 0, None),
            ("c1", "d1", "SEX", None, None, "exact_name", 3, "a;b"),
            ("c1", "d1", "µ_col", "ünïcode",
             -9223372036854775808, "g", 9223372036854775807, ""),
        ],
    ),
    (
        "evidence",
        """candidate_key VARCHAR, channel VARCHAR, score DOUBLE, detail VARCHAR,
           status VARCHAR, effective_weight DOUBLE""",
        [
            ("k1", "name_similarity", 1.0, "{}", "scored", 0.25),
            # A null score is NOT zero -- see the handoff on effective_weight.
            ("k1", "distribution_distance", None, None, "excluded", 0.0),
            ("k1", "type_agreement", 4.9999e-05, "d", "scored", 1.0 / 3.0),
        ],
    ),
    # An empty parquet still has to carry its schema: the default -profile test
    # run proposes nothing, so this is the *common* case there, not a corner.
    ("evidence_empty", """candidate_key VARCHAR, score DOUBLE""", []),
]

# Nested fixtures (§3). Kept in a separate list because they are inserted
# from SQL LITERALS rather than through parameter binding: how a Python list
# or dict binds to a LIST or STRUCT column is exactly the kind of detail that
# can differ between two duckdb minor versions, and a probe whose WRITE path
# depends on the thing it is probing cannot fail honestly. A literal is the
# same bytes of SQL on both sides.
#
# (table name, CREATE TABLE body, list of VALUES literals)
NESTED_FIXTURES = [
    (
        "scores",
        """cohort_id VARCHAR, left_id VARCHAR, right_id VARCHAR, match_weight DOUBLE,
           per_field_agreement STRUCT(field VARCHAR, level VARCHAR, weight DOUBLE)[]""",
        [
            # The ordinary case: several structs, one per comparison field.
            "('c1', 'a#1', 'b#2', 14.6546, "
            "[{'field': 'given', 'level': 'agree', 'weight': 4.7661}, "
            "{'field': 'middle', 'level': 'missing', 'weight': 0.2965}])",
            # A NULL inside a struct inside a list. The handoff's
            # effective_weight lesson in nested form: a null weight is not a
            # zero weight, and it has to survive as null.
            "('c1', 'a#2', 'b#3', -3.0195, "
            "[{'field': 'middle', 'level': 'disagree', 'weight': NULL}])",
            # An EMPTY list and a NULL list are different encodings, and both
            # are reachable: an empty comparison spec yields the first, a pair
            # that failed to score yields the second.
            "('c1', 'a#3', 'b#4', 0.0, [])",
            "('c1', 'a#4', 'b#5', NULL, NULL)",
        ],
    ),
    (
        "links",
        """person_id VARCHAR, cohort_id VARCHAR, source_row_id VARCHAR[]""",
        [
            # A resolved person: several source records.
            "('P-aaa', 'c1', ['registry#1', 'encounter#1'])",
            # A singleton -- the common case, since every unlinked record is
            # still a person. A one-element list and a scalar look identical
            # in a printout and are not the same parquet.
            "('P-bbb', 'c1', ['registry#2'])",
            "('P-ccc', 'c1', [])",
            "('P-ddd', 'c1', NULL)",
            # Non-ASCII inside a nested value, matching the flat fixtures'
            # own µ_col / ünïcode case.
            "('P-eee', 'c1', ['µ#1', 'ünïcode#2'])",
        ],
    ),
]


def _canonical(rows, description):
    """A representation that survives JSON and still distinguishes 1 from 1.0
    and '' from None -- comparing repr()s of the Python objects duckdb hands
    back is the thing the nf-test assertions actually consume."""
    return {
        "cols": [d[0] for d in description],
        "rows": [[f"{type(v).__name__}:{v!r}" for v in row] for row in rows],
    }


def _expected(body, rows):
    con = duckdb.connect()
    con.execute(f"CREATE TABLE t ({body})")
    if rows:
        con.executemany(f"INSERT INTO t VALUES ({','.join('?' * len(rows[0]))})", rows)
    cur = con.execute("SELECT * FROM t")
    return _canonical(cur.fetchall(), cur.description)


def _build_nested(con, name, body, literals):
    """Materialise a nested fixture from SQL literals. Used by BOTH the write
    path and the expectation, so the two cannot drift."""
    con.execute(f"CREATE OR REPLACE TABLE {name} ({body})")
    for literal in literals:
        con.execute(f"INSERT INTO {name} VALUES {literal}")


def _expected_nested(name, body, literals):
    con = duckdb.connect()
    _build_nested(con, name, body, literals)
    cur = con.execute(f"SELECT * FROM {name}")
    return _canonical(cur.fetchall(), cur.description)


def do_write(target):
    con = duckdb.connect()
    for name, body, rows in FIXTURES:
        con.execute(f"CREATE TABLE {name} ({body})")
        if rows:
            con.executemany(
                f"INSERT INTO {name} VALUES ({','.join('?' * len(rows[0]))})", rows
            )
        con.execute(f"COPY {name} TO '{target}/{name}.parquet' (FORMAT PARQUET)")
    for name, body, literals in NESTED_FIXTURES:
        _build_nested(con, name, body, literals)
        con.execute(f"COPY {name} TO '{target}/{name}.parquet' (FORMAT PARQUET)")
    total = len(FIXTURES) + len(NESTED_FIXTURES)
    print(
        f"probe: wrote {total} parquet fixtures ({len(NESTED_FIXTURES)} nested) "
        f"with duckdb {duckdb.__version__}"
    )
    return 0


def do_read(target):
    con = duckdb.connect()
    failures = []
    for name, body, rows in FIXTURES:
        path = f"{target}/{name}.parquet"
        try:
            cur = con.execute(f"SELECT * FROM read_parquet('{path}')")
            got = _canonical(cur.fetchall(), cur.description)
        except Exception as exc:  # noqa: BLE001 -- any read failure is the finding
            failures.append(f"{name}: host duckdb could not read it at all: {exc}")
            continue
        want = _expected(body, rows)
        if got != want:
            failures.append(
                f"{name}: round-trip changed the values\n"
                f"    written : {json.dumps(want, sort_keys=True)}\n"
                f"    read back: {json.dumps(got, sort_keys=True)}"
            )
    for name, body, literals in NESTED_FIXTURES:
        path = f"{target}/{name}.parquet"
        try:
            cur = con.execute(f"SELECT * FROM read_parquet('{path}')")
            got = _canonical(cur.fetchall(), cur.description)
        except Exception as exc:  # noqa: BLE001 -- any read failure is the finding
            failures.append(
                f"{name} (nested): host duckdb could not read it at all: {exc}. "
                "This is the LIST/STRUCT surface \u00a73 introduced; the flat fixtures "
                "passing tells you nothing about it."
            )
            continue
        want = _expected_nested(name, body, literals)
        if got != want:
            failures.append(
                f"{name} (nested): round-trip changed the values\n"
                f"    written : {json.dumps(want, sort_keys=True)}\n"
                f"    read back: {json.dumps(got, sort_keys=True)}"
            )
    if failures:
        print(f"probe: FAILED with host duckdb {duckdb.__version__}")
        for f in failures:
            print(f"  {f}")
        return 1
    print(
        f"probe: host duckdb {duckdb.__version__} reads the container's parquet "
        f"faithfully ({len(FIXTURES) + len(NESTED_FIXTURES)} fixtures, "
        f"{len(NESTED_FIXTURES)} of them nested, values identical)"
    )
    return 0


def main(argv):
    if len(argv) != 3 or argv[1] not in ("write", "read"):
        print(__doc__.strip().split("Usage\n-----\n")[-1], file=sys.stderr)
        return 2
    return do_write(argv[2]) if argv[1] == "write" else do_read(argv[2])


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
