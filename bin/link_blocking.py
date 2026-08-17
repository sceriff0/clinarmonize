#!/usr/bin/env python3
"""§3.1 -- block candidate pairs so comparison is tractable.

Named alts seam (docs/steps/s3-1.md):
    Blocker_block(records, rules) -> [CandidatePair]

Comparing every pair is quadratic and unnecessary. Blocking is where the
RECALL CEILING is set: a pair excluded by every blocking rule can never be
matched, no matter how good the comparison model is, and no later stage can
tell it was excluded. That asymmetry is the whole reason this step reports
what it dropped rather than only what it kept.

Contract (docs/steps/s3-1.md):
    IN   a cohort's datasets, one row per patient-record
    OUT  candidate pairs (left_id, right_id, blocking_rule_id)
         link/blocking_report.json  { rule_id, n_pairs, n_records_unblocked }
    SIDE none

Not here (nogo): this script does not score, threshold or decide anything --
§3.2 owns the weight and §3.3 owns the cut. And no blocking rule may
reference the outcome variable or any column the pack marks outcome: true;
that is enforced in _refuse_outcome_rules() below, before a single key is
computed, and it kills the run rather than dropping the rule.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys

import duckdb
import yaml

# `substr(<column>, <start>, <len>)`, the second and last form the rule
# grammar accepts. Deliberately not a general expression language: a
# blocking rule is the recall ceiling, and a ceiling nobody can read off the
# rule file is not inspectable, which is the property §3.1's Alternatives
# table says LSH gives up.
_SUBSTR_RE = re.compile(r"^substr\(\s*(?P<col>[A-Za-z_][A-Za-z0-9_]*)\s*,\s*(?P<start>\d+)\s*,\s*(?P<len>\d+)\s*\)$")
_BARE_COL_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

_NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")


def _normalize(s: str) -> str:
    """The same normalisation §4.1 applies before comparing a column header
    to a pack variable name. Used ONLY by the outcome refusal below, so that
    `Birth_Date` and `birthdate` cannot slip past a check written against
    one spelling."""
    return _NON_ALNUM_RE.sub("", str(s).lower())


class KeyComponent:
    """One component of a rule's block key: a column, optionally sliced."""

    def __init__(self, raw: str, column: str, start: int | None = None, length: int | None = None):
        self.raw = raw
        self.column = column
        self.start = start
        self.length = length

    def sql(self, alias: str = "r") -> str:
        quoted = f'{alias}."{self.column}"'
        if self.start is None:
            return f"CAST({quoted} AS VARCHAR)"
        return f"substr(CAST({quoted} AS VARCHAR), {self.start}, {self.length})"

    def __repr__(self) -> str:  # pragma: no cover - diagnostics only
        return f"KeyComponent({self.raw!r})"


def _parse_component(raw: str) -> KeyComponent:
    text = str(raw).strip()
    if _BARE_COL_RE.match(text):
        return KeyComponent(text, text)
    match = _SUBSTR_RE.match(text)
    if match:
        return KeyComponent(text, match.group("col"), int(match.group("start")), int(match.group("len")))
    raise ValueError(
        f"blocking rule component '{raw}' is not a supported key expression. "
        "The grammar is a bare column name or substr(<column>, <start>, <len>); "
        "anything else is refused here rather than silently matching nothing."
    )


def load_rules(path: str) -> list[dict]:
    with open(path, encoding="utf-8") as handle:
        raw = yaml.safe_load(handle)
    if not isinstance(raw, list) or not raw:
        raise ValueError(
            f"{path} must be a non-empty LIST of blocking rules. Recall is the UNION of the "
            "rules, so a rule file that is not a list has no union to take."
        )
    rules = []
    seen: set[str] = set()
    for entry in raw:
        rule_id = str(entry["id"])
        if rule_id in seen:
            raise ValueError(f"duplicate blocking rule id '{rule_id}' in {path}")
        seen.add(rule_id)
        components = [_parse_component(c) for c in _rule_key_components(entry, path, rule_id)]
        rules.append({"id": rule_id, "components": components})
    return rules


def _rule_key_components(entry: dict, path: str, rule_id: str) -> list:
    """Read a rule's `on:` list.

    YAML 1.1 -- which is what PyYAML implements -- resolves the bare token
    `on` to boolean true, the same family of surprise as `NO` resolving to
    false. §3.1's contract writes the key as `on:` literally, so a rule file
    matching the published contract arrives here with the key True, not the
    string "on". Both spellings are accepted rather than renaming the key in
    the asset, because the asset has to keep matching the contract the spec
    published; quoting it as "on" in every rule file would work too, and
    would put the burden on whoever writes the next one.
    """
    for key in (True, "on"):
        if key in entry:
            value = entry[key]
            if not isinstance(value, list) or not value:
                raise ValueError(f"blocking rule '{rule_id}' in {path} has an empty or non-list `on:`")
            return value
    raise ValueError(
        f"blocking rule '{rule_id}' in {path} has no `on:` key. A rule with no key components "
        "blocks nothing and cannot be distinguished from a rule that matched nothing."
    )


def _refuse_outcome_rules(rules: list[dict], outcome_variables: list[str]) -> None:
    """§3.1 nogo / Global Constraint 1.

    Blocking sets the recall ceiling, so a rule keyed on the outcome makes
    every downstream linkage a function of the outcome -- and does it in a
    place no later stage can observe, because an unblocked pair leaves no
    trace beyond a count. The refusal is by NAME against the pack's
    outcome-flagged variables (never a hard-coded column name), matched
    after the same normalisation §4.1 uses, and it kills the run: dropping
    the offending rule instead would silently lower the recall ceiling,
    which is the failure this check exists to prevent.
    """
    outcome_norm = {_normalize(v): v for v in outcome_variables}
    if not outcome_norm:
        return
    for rule in rules:
        for component in rule["components"]:
            hit = outcome_norm.get(_normalize(component.column))
            if hit is not None:
                raise ValueError(
                    f"blocking rule '{rule['id']}' references column '{component.column}', which is "
                    f"named by the outcome-flagged pack variable '{hit}'. §0's invariant forbids any "
                    "harmonization decision from being a function of the outcome, and a blocking rule "
                    "is the strongest such decision there is: it sets a recall ceiling no later stage "
                    "can lift or even detect. Remove the rule or unflag the variable."
                )


def _register_records(con: duckdb.DuckDBPyConnection, tables: list[dict]) -> list[str]:
    """Load every dataset into one `records` view, keyed by
    (cohort_id, record_id), and return the union of their column names.

    record_id is '<dataset_id>#<row_number>'. §3.1's IN slot is "a cohort's
    datasets, one row per patient-record" and the samplesheet promises no
    shared surrogate key across datasets, so the position within its own
    table is the only identifier every record is guaranteed to have. It is
    stable for a given input file, which is what §3.3's links.parquet needs
    to point back at.
    """
    all_columns: list[str] = []
    parts = []
    for spec in tables:
        rel = con.read_csv(spec["path"], header=True, all_varchar=True, sample_size=-1)
        view = f"src_{len(parts)}"
        con.register(view, rel)
        cols = [c for c in rel.columns]
        for c in cols:
            if c not in all_columns:
                all_columns.append(c)
        parts.append((view, spec, cols))

    # Every part is widened to the union schema with explicit NULLs, so one
    # rule can be evaluated across datasets that do not share a column --
    # the normal case inside a cohort (§1.1), not a misconfiguration.
    selects = []
    for view, spec, cols in parts:
        projected = []
        for column in all_columns:
            if column in cols:
                projected.append(f'CAST("{column}" AS VARCHAR) AS "{column}"')
            else:
                projected.append(f'CAST(NULL AS VARCHAR) AS "{column}"')
        selects.append(
            "SELECT "
            f"'{spec['cohort_id']}' AS cohort_id, "
            f"'{spec['dataset_id']}' AS dataset_id, "
            f"'{spec['dataset_id']}#' || CAST(row_number() OVER () AS VARCHAR) AS record_id, "
            + ", ".join(projected)
            + f" FROM {view}"
        )
    con.execute("CREATE OR REPLACE VIEW records AS " + "\nUNION ALL\n".join(selects))
    return all_columns


# ---------------------------------------------------------------------------
# §3.1 -- the Blocker alts seam. Today's only implementation: the rule list
# above, evaluated as an equi-join on a composite key. The Alternatives
# table's "sorted neighbourhood" and "locality-sensitive hashing" would each
# replace only this function's body -- and each would have to answer the
# same question this one answers in blocking_report.json, which is what the
# recall ceiling is.
#
# Everything below happens in SQL, and no pair is ever materialised in
# Python. That is not a micro-optimisation. Blocking exists because the
# comparison space is quadratic, so the ONE thing this function must survive
# is a rule that blocks badly -- a key with few distinct values over a long
# table produces tens of millions of pairs from a few hundred blocks, and a
# fetchall() of that is an OutOfMemoryError in the step whose entire job is
# to keep the problem tractable. The pairs go from the join straight into a
# table and from there into parquet.
# ---------------------------------------------------------------------------
def Blocker_block(
    con: duckdb.DuckDBPyConnection,
    rules: list[dict],
    available_columns: set[str],
    max_block_size: int,
    out_pairs: str,
) -> list[dict]:
    """Writes candidate pairs to `out_pairs` and returns the per-rule report.

    Pairs never cross a cohort. §1.1 is explicit that the JOIN happens
    INSIDE a cohort while the UNION happens across them, so a cross-cohort
    pair is not a candidate that scored badly -- it is a category error, and
    letting §3.2 score it would put the burden of that distinction on a
    threshold.
    """
    con.execute(
        """
        CREATE OR REPLACE TABLE candidate_pairs (
            cohort_id        VARCHAR,
            left_id          VARCHAR,
            right_id         VARCHAR,
            blocking_rule_id VARCHAR
        )
        """
    )

    report: list[dict] = []
    for rule in rules:
        missing = [c.column for c in rule["components"] if c.column not in available_columns]
        if missing:
            # A rule whose columns this run does not carry does not apply.
            # Reported at zero rather than omitted: an absent rule and a rule
            # that found nothing are different facts, and the report is where
            # the recall ceiling is read off.
            report.append(
                {
                    "rule_id": rule["id"],
                    "n_pairs": 0,
                    "n_blocks": 0,
                    "n_blocks_skipped_oversize": 0,
                    "n_records_in_skipped_blocks": 0,
                    "not_applicable_columns": sorted(missing),
                }
            )
            continue

        key_sql = ", ".join(c.sql("r") for c in rule["components"])
        not_null = " AND ".join(
            f'r."{c.column}" IS NOT NULL AND CAST(r."{c.column}" AS VARCHAR) <> \'\'' for c in rule["components"]
        )
        con.execute(
            f"""
            CREATE OR REPLACE TEMP VIEW keyed AS
            SELECT r.cohort_id, r.record_id, concat_ws(chr(31), {key_sql}) AS block_key
            FROM records r
            WHERE {not_null}
            """
        )
        # max_block_size: a block larger than this is REPORTED AND SKIPPED,
        # not silently expanded (§3.1 Params). Expanding it is what turns a
        # blocking pass back into the quadratic comparison it exists to
        # avoid, and doing so quietly is how a run that "worked" takes a
        # week. n_records_in_skipped_blocks says how much recall that cost,
        # because "we skipped 3 blocks" and "we skipped 30,000 records" are
        # very different sentences about the same number of blocks.
        con.execute(
            """
            CREATE OR REPLACE TEMP TABLE blocks AS
            SELECT cohort_id, block_key, count(*) AS n
            FROM keyed GROUP BY 1, 2 HAVING count(*) > 1
            """
        )
        n_blocks, n_oversize, n_records_skipped = con.execute(
            f"""
            SELECT count(*),
                   count(*) FILTER (WHERE n > {max_block_size}),
                   coalesce(sum(n) FILTER (WHERE n > {max_block_size}), 0)
            FROM blocks
            """
        ).fetchone()

        before = con.execute("SELECT count(*) FROM candidate_pairs").fetchone()[0]
        con.execute(
            f"""
            INSERT INTO candidate_pairs
            SELECT a.cohort_id, a.record_id, b.record_id, '{rule["id"]}'
            FROM keyed a
            JOIN keyed b
              ON a.cohort_id = b.cohort_id
             AND a.block_key = b.block_key
             AND a.record_id < b.record_id
            JOIN blocks k
              ON k.cohort_id = a.cohort_id
             AND k.block_key = a.block_key
             AND k.n <= {max_block_size}
            """
        )
        after = con.execute("SELECT count(*) FROM candidate_pairs").fetchone()[0]

        report.append(
            {
                "rule_id": rule["id"],
                "n_pairs": int(after - before),
                "n_blocks": int(n_blocks),
                "n_blocks_skipped_oversize": int(n_oversize),
                "n_records_in_skipped_blocks": int(n_records_skipped),
                "not_applicable_columns": [],
            }
        )

    con.execute(
        f"COPY (SELECT * FROM candidate_pairs ORDER BY cohort_id, left_id, right_id, blocking_rule_id) "
        f"TO '{out_pairs}' (FORMAT PARQUET)"
    )
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="§3.1 blocking")
    parser.add_argument("--tables", required=True, help="JSON list of {cohort_id,dataset_id,path}")
    parser.add_argument("--blocking-rules", required=True)
    parser.add_argument("--outcome-variables", required=True, help="JSON list of outcome-flagged pack variable names")
    parser.add_argument("--max-block-size", type=int, required=True)
    parser.add_argument("--max-pairs-warn-frac", type=float, required=True)
    parser.add_argument("--out-pairs", required=True)
    parser.add_argument("--out-report", required=True)
    args = parser.parse_args(argv)

    tables = json.loads(args.tables)
    outcome_variables = json.loads(args.outcome_variables)

    rules = load_rules(args.blocking_rules)
    _refuse_outcome_rules(rules, outcome_variables)

    con = duckdb.connect()
    available = set(_register_records(con, tables))

    # §3.1's own refusal, separate from the outcome one: a rule referencing a
    # column NO dataset carries is reported not-applicable above. A rule file
    # in which EVERY rule is not applicable means this run has no recall
    # ceiling at all, which is worth saying out loud.
    per_rule = Blocker_block(con, rules, available, args.max_block_size, args.out_pairs)

    n_records = con.execute("SELECT count(*) FROM records").fetchone()[0]
    n_pairs = con.execute("SELECT count(*) FROM candidate_pairs").fetchone()[0]
    # Counted in SQL over the pair table, not over a Python set: on a badly
    # blocked run this table is tens of millions of rows, and the count is
    # the only thing anyone needs from it.
    n_blocked = con.execute(
        "SELECT count(DISTINCT record_id) FROM ("
        "  SELECT left_id AS record_id FROM candidate_pairs"
        "  UNION ALL SELECT right_id FROM candidate_pairs)"
    ).fetchone()[0]
    n_unblocked = int(n_records) - int(n_blocked)

    # The full within-cohort cross product -- the denominator
    # max_pairs_warn_frac is a fraction OF. Across cohorts is not the right
    # denominator: a cross-cohort pair is never a candidate (§1.1), so
    # counting it would understate how much of the reachable space the rules
    # actually cover.
    cross_product = con.execute(
        "SELECT coalesce(sum(n * (n - 1) / 2), 0) FROM (SELECT count(*) AS n FROM records GROUP BY cohort_id)"
    ).fetchone()[0]
    pair_frac = (n_pairs / cross_product) if cross_product else 0.0

    warnings: list[str] = []
    if pair_frac > args.max_pairs_warn_frac:
        warnings.append(
            f"candidate pairs are {pair_frac:.4f} of the within-cohort cross product, above "
            f"--max_pairs_warn_frac {args.max_pairs_warn_frac}. The rules are barely narrowing the "
            "comparison space; check for a rule keyed on something near-constant."
        )
        print(f"WARN: {warnings[-1]}", file=sys.stderr)
    if all(entry["not_applicable_columns"] for entry in per_rule):
        warnings.append(
            "every blocking rule references at least one column no dataset in this run carries, so no "
            "pair can ever be proposed. The recall ceiling for this run is zero."
        )
        print(f"WARN: {warnings[-1]}", file=sys.stderr)

    # n_records_unblocked is a RUN-level quantity repeated on every rule
    # entry, exactly as the card's contract writes it -- the done-when reads
    # it with `jq -e '.[] | .n_records_unblocked'`, i.e. off each element of
    # the array. It is the count of records no rule ever placed in a block
    # with anything else, so it is the recall ceiling's shadow, and it is
    # emitted even at zero.
    report = [
        dict(entry, n_records_unblocked=n_unblocked, n_records=int(n_records),
             n_candidate_pairs=int(n_pairs), warnings=warnings)
        for entry in per_rule
    ]

    os.makedirs(os.path.dirname(os.path.abspath(args.out_report)) or ".", exist_ok=True)
    with open(args.out_report, "w", encoding="utf-8") as handle:
        json.dump(report, handle, indent=2, sort_keys=True)
        handle.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
