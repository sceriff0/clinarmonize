#!/usr/bin/env python3
"""§2.1 / §2.2 / §2.3 — profile every column of one table into a typed
evidence record, with ranked candidate units and a manifest of unprofilable
columns.

Named alts seams (docs/plans/phase-0.md Global Constraint 7; the cards name
these exactly):
    Profiler_profile(table)           -> [ColumnProfile]   (docs/steps/s2-1.md)
    UnitParser_parse(header, quants)  -> [UnitCandidate]    (docs/steps/s2-2.md)
    FailurePolicy_onFail(col, reason) -> 'skip' | 'fail'    (docs/steps/s2-3.md)

This is the FIRST thing in the pipeline to ever read a table's bytes:
everything upstream of PROFILE_COLUMNS (modules/local/profile_columns/main.nf)
deliberately does not.

Not here (nogo, all three cards): never normalises/lowercases/strips a
column name (§4.1 needs it verbatim); never converts a value or resolves a
unit ambiguity; never repairs, re-encodes or coerces a failing column; never
reads the outcome column differently from any other -- this script has no
notion of the concept pack at all.
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import re
import sys
from datetime import datetime

import duckdb
import yaml

REPLACEMENT_CHAR = "�"
INT_RE = re.compile(r"^[+-]?\d+$")
FLOAT_RE = re.compile(r"^[+-]?(\d+\.\d*|\.\d+|\d+)([eE][+-]?\d+)?$")
BOOL_VALUES = {"true", "false", "True", "False", "TRUE", "FALSE"}

# §2.2's time-unit confusion band: a small-magnitude duration is genuinely
# ambiguous between days and months (60 days ~ 2 months of survival; 60
# months ~ 5 years -- both real spans). Symmetric on purpose: the ambiguity
# runs both directions, and the card's Done-when only fixes the MONTHS ->
# day-candidate direction, not the reverse, so this is the general rule that
# example is drawn from.
TIME_UNIT_AMBIGUITY = {"d": "mo", "mo": "d"}
TIME_AMBIGUITY_MAX = 60.0

_DUCKDB_CON = duckdb.connect()


# ---------------------------------------------------------------------------
# §2.3 — FailurePolicy alts seam. Today's only implementation: always skip
# (manifest the column, never drop it, never abort the run over one bad
# column). The Alternatives table's "fail on the first bad column" would
# change only this function's return value to 'fail'; "quarantine to a side
# table" would need a second output path this function doesn't have. The
# run-level pass/fail decision (SIDE: "exit non-zero when the failure RATE
# exceeds --max_failed_frac") is made by the caller, workflows/harmonize.nf,
# once every dataset in the run has been counted -- not here, and not on a
# single column.
# ---------------------------------------------------------------------------
def FailurePolicy_onFail(column: str, reason: str) -> str:
    del column, reason  # unused today; kept so the seam's real shape is visible
    return "skip"


def _looks_numeric(value: str) -> bool:
    return bool(INT_RE.match(value)) or bool(FLOAT_RE.match(value))


def _matches_date(value: str, date_formats: list[str]) -> bool:
    for fmt in date_formats:
        try:
            datetime.strptime(value, fmt)
            return True
        except ValueError:
            continue
    return False


def _classify(values: list[str], na_strings: list[str], date_formats: list[str]) -> dict:
    """Classify one column's raw values (row order preserved). Returns
    either a successful 'inferred_type' or a 'failure_reason' -- never both.
    Never coerces, repairs or drops a value."""
    nonmissing = [v for v in values if v not in na_strings]
    n_missing = len(values) - len(nonmissing)

    if not nonmissing:
        # §2.3 Trap: all-missing is a SUCCESSFUL profile of an empty column,
        # never a failure.
        return {"inferred_type": "empty", "nonmissing": [], "n_missing": n_missing}

    if all(INT_RE.match(v) for v in nonmissing):
        return {"inferred_type": "int", "nonmissing": nonmissing, "n_missing": n_missing}
    if all(FLOAT_RE.match(v) for v in nonmissing):
        return {"inferred_type": "float", "nonmissing": nonmissing, "n_missing": n_missing}
    if all(v in BOOL_VALUES for v in nonmissing):
        return {"inferred_type": "bool", "nonmissing": nonmissing, "n_missing": n_missing}
    if all(_matches_date(v, date_formats) for v in nonmissing):
        return {"inferred_type": "date", "nonmissing": nonmissing, "n_missing": n_missing}

    numericish = [v for v in nonmissing if _looks_numeric(v) or _matches_date(v, date_formats)]
    other = [v for v in nonmissing if v not in numericish]
    # Rebuild `other` value-wise (membership test above is by value, which is
    # fine: every occurrence of the same raw string classifies identically).
    if numericish and other:
        # The column disagrees with itself, not just with our assumptions
        # (§2.3 Why): some values look like they were trying to be
        # numeric/date, some plainly aren't. The offending sample is
        # whichever group is the minority -- the outlier against the
        # column's own apparent intent.
        offending = other if len(other) <= len(numericish) else numericish
        return {"failure_reason": "mixed_type", "offending": offending, "n_missing": n_missing}

    # No numeric/date-like hint anywhere: a legitimate free-text/categorical
    # column (e.g. an ordinal code set), not a failure.
    return {"inferred_type": "str", "nonmissing": nonmissing, "n_missing": n_missing}


def compute_quantiles(values: list[float]) -> list[float]:
    """p0 p1 p25 p50 p75 p99 p100, via DuckDB's quantile_cont -- the
    Alternatives table's "pin one" trap: Polars' quantile semantics differ,
    so exactly one engine's interpolation is used, everywhere, always."""
    row = _DUCKDB_CON.execute(
        "SELECT quantile_cont(x, [0.0, 0.01, 0.25, 0.5, 0.75, 0.99, 1.0]) FROM (SELECT UNNEST(?) AS x)",
        [values],
    ).fetchone()
    return [float(v) for v in row[0]]


# ---------------------------------------------------------------------------
# §2.2 — UnitParser alts seam. Today's only implementation: a pattern list
# (assets/unit_patterns.yaml) matched against the header, plus (when
# infer_unit_from_range) a small set of range-derived candidates. Ambiguity
# is RECORDED, never resolved: a column can and often does end up with
# several candidates at different confidences.
# ---------------------------------------------------------------------------
def UnitParser_parse(
    header: str,
    quantiles: list[float],
    unit_patterns: list[dict],
    infer_unit_from_range: bool,
) -> list[dict]:
    candidates = []
    header_ucum = None
    for pat in unit_patterns:
        if re.search(pat["regex"], header, re.IGNORECASE):
            candidates.append(
                {"ucum": pat["ucum"], "source": f"header:{pat['label']}", "confidence": pat["confidence"]}
            )
            if header_ucum is None:
                header_ucum = pat["ucum"]

    if infer_unit_from_range and quantiles:
        p0, p100 = quantiles[0], quantiles[-1]

        if p0 >= 0 and p100 <= 1:
            candidates.append({"ucum": "1", "source": "range:0..1", "confidence": 0.3})
        elif p0 >= 0 and p100 <= 100:
            candidates.append({"ucum": "1", "source": "range:0..100", "confidence": 0.3})

        if header_ucum in TIME_UNIT_AMBIGUITY and p0 >= 0 and p100 <= TIME_AMBIGUITY_MAX:
            alt_ucum = TIME_UNIT_AMBIGUITY[header_ucum]
            candidates.append({"ucum": alt_ucum, "source": "range:0..60", "confidence": 0.3})

    return candidates


def load_unit_patterns(path: str) -> list[dict]:
    with open(path, encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    return data["patterns"]


def _read_raw_columns(table_path: str) -> tuple[list[str], dict[str, list[str]], int]:
    """Read the table as raw strings, tolerant of encoding problems (a
    decode failure becomes U+FFFD per cell, detected downstream as the
    'encoding' failure reason -- not a crash) and of ragged rows (a short
    row pads with '', a long row's extra cells are dropped; real clinical
    exports are not always well-formed, and one bad line must not take down
    every column in the table)."""
    with open(table_path, "rb") as fh:
        raw_bytes = fh.read()
    text = raw_bytes.decode("utf-8", errors="replace")

    reader = csv.reader(io.StringIO(text))
    try:
        header = next(reader)
    except StopIteration:
        header = []

    columns: dict[str, list[str]] = {h: [] for h in header}
    n_rows = 0
    for row in reader:
        n_rows += 1
        for i, h in enumerate(header):
            columns[h].append(row[i] if i < len(row) else "")
    return header, columns, n_rows


# ---------------------------------------------------------------------------
# §2.1 — Profiler alts seam. Today's only implementation: DuckDB for the
# quantile engine (see compute_quantiles), hand-rolled classification for
# type inference and failure detection (DuckDB's own type sniffing can't
# give us the §2.3 failure granularity or the §2.1 na_strings control).
# ---------------------------------------------------------------------------
def Profiler_profile(
    table_path: str,
    cohort_id: str,
    dataset_id: str,
    params: dict,
    unit_patterns: list[dict],
) -> tuple[list[dict], list[dict]]:
    na_strings = params["na_strings"]
    date_formats = params["date_formats"]
    example_k = params["example_k"]
    max_unique_listed = params["max_unique_listed"]
    fail_sample_k = params["fail_sample_k"]
    infer_unit_from_range = params["infer_unit_from_range"]

    header, columns, n_rows = _read_raw_columns(table_path)

    profiles: list[dict] = []
    failures: list[dict] = []

    for col in header:
        values = columns[col]
        try:
            if any(REPLACEMENT_CHAR in v for v in values):
                offending = [v for v in values if REPLACEMENT_CHAR in v][:fail_sample_k]
                FailurePolicy_onFail(col, "encoding")
                failures.append(
                    {
                        "cohort_id": cohort_id,
                        "dataset_id": dataset_id,
                        "column": col,
                        "reason": "encoding",
                        "sample": offending,
                    }
                )
                continue

            result = _classify(values, na_strings, date_formats)
            if "failure_reason" in result:
                FailurePolicy_onFail(col, result["failure_reason"])
                failures.append(
                    {
                        "cohort_id": cohort_id,
                        "dataset_id": dataset_id,
                        "column": col,
                        "reason": result["failure_reason"],
                        "sample": result["offending"][:fail_sample_k],
                    }
                )
                continue

            inferred_type = result["inferred_type"]
            nonmissing = result["nonmissing"]
            n_missing = result["n_missing"]
            n_unique = len(set(nonmissing))

            if n_unique <= max_unique_listed:
                unique_values = sorted(set(nonmissing))
                unique_truncated = False
            else:
                # §2.1 Trap: absence of the set must never be readable as
                # "few values" -- null (not a partial list) plus an explicit
                # flag, always together.
                unique_values = None
                unique_truncated = True

            quantiles: list[float] = []
            if inferred_type in ("int", "float") and nonmissing:
                quantiles = compute_quantiles([float(v) for v in nonmissing])

            candidate_units = UnitParser_parse(col, quantiles, unit_patterns, infer_unit_from_range)

            profiles.append(
                {
                    "column": col,  # verbatim source name, never normalised here
                    "inferred_type": inferred_type,
                    "n_rows": n_rows,
                    "n_missing": n_missing,
                    "n_unique": n_unique,
                    "unique_values": unique_values,
                    "unique_truncated": unique_truncated,
                    "quantiles": quantiles,
                    "candidate_units": candidate_units,
                    "example_values": nonmissing[:example_k],
                }
            )
        except Exception:  # noqa: BLE001 - FailurePolicy's 'unreadable' catch-all
            FailurePolicy_onFail(col, "unreadable")
            failures.append(
                {
                    "cohort_id": cohort_id,
                    "dataset_id": dataset_id,
                    "column": col,
                    "reason": "unreadable",
                    "sample": values[:fail_sample_k],
                }
            )

    return profiles, failures


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--table", required=True)
    ap.add_argument("--cohort-id", required=True)
    ap.add_argument("--dataset-id", required=True)
    ap.add_argument("--params", required=True, help="JSON object: max_unique_listed, date_formats, "
                     "example_k, na_strings, ucum_release, infer_unit_from_range, fail_sample_k")
    ap.add_argument("--unit-patterns", required=True)
    ap.add_argument("--out-profile", required=True)
    ap.add_argument("--out-failed", required=True)
    args = ap.parse_args(argv)

    params = json.loads(args.params)
    unit_patterns = load_unit_patterns(args.unit_patterns)

    profiles, failures = Profiler_profile(args.table, args.cohort_id, args.dataset_id, params, unit_patterns)

    with open(args.out_profile, "w", encoding="utf-8") as fh:
        json.dump(profiles, fh, indent=2, sort_keys=True)
        fh.write("\n")
    with open(args.out_failed, "w", encoding="utf-8") as fh:
        json.dump(failures, fh, indent=2, sort_keys=True)
        fh.write("\n")

    return 0


if __name__ == "__main__":
    sys.exit(main())
