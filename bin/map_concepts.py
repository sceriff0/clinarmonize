#!/usr/bin/env python3
"""§6.1 -- apply concept mappings into OMOP rows.

Named alts seam (docs/steps/s6-1.md):
    ConceptMapper -- map(value, Rule, Vocabulary) -> [OmopRow]

IN   linked cohort tables + rules/ruleset.json + pinned vocabulary
OUT  mapped/<table>.parquet    one row per (person, concept, datetime)
     mapped/_unmapped.parquet  every source value with no standard concept
SIDE none (the run FAILS above --max_unmapped_frac; that is an exit code,
     not an emitted artefact)

The pinned vocabulary, and Ruling R14
-------------------------------------
The card's IN slot names a "pinned vocabulary", and there is none in this
repo: no ATHENA release is vendored and §0.8 pins no downloader. §4.1 hit
the identical wall and resolved it as Ruling R14 -- generate against the
PACK's declared variables and their concept_ids, read the pack's
`vocabulary` key as the pinned RELEASE ID, and never resolve it. §6.1
inherits that ruling rather than re-litigating it: if the proposer and the
mapper disagreed about what a concept id denotes, a rule confirmed against
one would be applied against the other, which is a worse failure than
having no vocabulary at all. The release id is recorded (the module's
versions.yml) and never consulted, exactly as in §4.1.

That ruling is what fixes `<domain>_source_concept_id` at 0. OMOP's own
convention is that 0 denotes "no matching concept" -- assets/schema_pack.json
already says so in its own concept_id description -- so 0 is the DESIGNATED
ABSENCE of a source concept, not an invented one. The card's nogo forbids
inventing a concept id for an unmapped VALUE, and does so in the same breath
as naming where such a value goes instead (_unmapped.parquet); it is not a
licence to leave the column null, because the card's own done-when counts
exactly those nulls and requires zero. Without a resolvable vocabulary there
is no non-standard, pre-translation concept to look up, and 0 says that
truthfully. The moment a vocabulary is vendored, this is the one lookup that
changes.

What is mapped, and what is not
-------------------------------
Every rule in ruleset.json whose `kind` is "column_map" -- which, as of §5.2,
is every rule it emits. A rule names (cohort_id, dataset_id, column) on the
source side and (variable, concept_id) on the target side; the pack says
which DOMAIN that variable lives in, and the domain says which CDM table the
row lands in. A source column with no rule has no standard concept by
definition and its values go to _unmapped.parquet -- recorded, never
invented and never dropped, because a value that vanishes between ingest and
mapped/ is indistinguishable from one that was never there.

The outcome-flagged variable is in that second group on every run, and not
by a check in this file: §4.1 refuses to propose it (Global Constraint 1),
so §5 cannot confirm it, so no rule for it can reach here. Nothing in this
script reads the outcome flag, tests a variable's name against it, or
branches on it -- there is no decision here that COULD be a function of the
outcome, which is a stronger position than checking for one.

value_as_number, which this card's Contract does not list
--------------------------------------------------------
The Contract snippet is annotated "Both ids are kept" -- it is making the
reversibility point, not enumerating the row. Two things put value_as_number
in the output anyway. The card's own OUT slot is "one row per (person,
concept, datetime)" for a MEASUREMENT table, and a measurement row carrying
no measured value is not a measurement. And §6.2's IN slot is this file's
output with the instruction "same rows, value_as_number converted" -- a
conversion with nothing to convert. It is the numeric parse of the verbatim
source value, NULL where the value does not parse; no unit conversion
happens here (§6.2) and no value vocabulary is applied here (§6.3).

person_id is a VARCHAR, deliberately, against this card's Contract
-----------------------------------------------------------------
The Contract types person_id `int`. §3.3 is built and makes it a content
hash of the cluster's sorted membership (bin/link_resolve.py `_person_id`),
for the same reason §5.2's rule_id is one: a counter renumbers everybody
when an unrelated cohort is added, so an audit against an older output
resolves to the wrong person. Re-typing it to int here would mean either
re-deriving identity in §6 -- which is §3's job and would put two answers in
the repo -- or numbering the clusters, which is the trap §3.3 already
refused. The VARCHAR is carried through verbatim. This is a deviation from
the Contract's type and is called out here rather than quietly made.

cdm_version
-----------
Validated against its enum and recorded. Nothing branches on it: across the
five-table subset this stage emits, the generic (concept, source_value,
source_concept, datetime, value) shape is identical in 5.3 and 5.4, and the
places the two releases genuinely differ are columns this stage does not
write. Faking a shape difference to make the param look live would be worse
than saying it is inert -- so it is said here.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

import duckdb

#
# Pack domain (assets/schema_pack.json's own enum) -> CDM table name. This
# is OMOP CDM structure, not a clinical constant: no disease term, gene
# symbol, cohort name, range, unit or ordering appears in it (§10.3 /
# Global Constraint 4). 'derived' is deliberately absent -- a derived
# variable is computed by §7 from other domains and has no source column to
# map, so a rule can never name one.
#
_DOMAIN_TO_CDM_TABLE = {
    "person": "person",
    "observation": "observation",
    "measurement": "measurement",
    "condition": "condition_occurrence",
    "drug": "drug_exposure",
    "procedure": "procedure_occurrence",
    "device": "device_exposure",
}

# OMOP's designated "no matching concept". See the module docstring: under
# Ruling R14 there is no vocabulary to resolve a source code against, and
# this is the value that says so rather than a null the done-when forbids.
_NO_MATCHING_CONCEPT = 0


def _cdm_table(domain: str) -> str | None:
    return _DOMAIN_TO_CDM_TABLE.get(domain)


def ConceptMapper_map(
    rules: list[dict],
    pack_variables: list[dict],
    cdm_domains: list[str],
) -> tuple[dict[str, list[dict]], list[dict]]:
    """The seam, resolved against metadata only: which rules land in which
    CDM table, and which are excluded because their table is outside
    --cdm_domains. Returns (rules by table, excluded rules).

    Split out from the row-level work below so the routing decision is
    testable without a table in hand, and so the row loop never has to ask
    "which table is this again" per row.
    """
    variable_by_name = {v["name"]: v for v in pack_variables}
    wanted_tables = set(cdm_domains)

    by_table: dict[str, list[dict]] = {table: [] for table in cdm_domains}
    excluded: list[dict] = []

    for rule in rules:
        if rule.get("kind") != "column_map":
            # §6.1 applies column maps. unit_convert is §6.2's, value_map is
            # §6.3's, derive is §7's -- each is applied by the stage that
            # owns it, never opportunistically here.
            excluded.append({"rule": rule, "reason": f"kind '{rule.get('kind')}' is not applied by §6.1"})
            continue

        variable_name = rule["to"]["variable"]
        variable = variable_by_name.get(variable_name)
        if variable is None:
            # A confirmed rule naming a variable the pack does not declare
            # cannot be applied and must not be guessed at. §5.1 already
            # refuses an unmatched ledger row, so reaching here means the
            # pack changed under a ruleset -- exactly the case a silent skip
            # would turn into missing rows nobody can account for.
            raise SystemExit(
                f"rule {rule['rule_id']} maps to variable '{variable_name}', which the concept pack does not "
                "declare. The ruleset was compiled against a different pack; recompile it or restore the pack."
            )

        table = _cdm_table(variable["domain"])
        if table is None or table not in wanted_tables:
            excluded.append(
                {
                    "rule": rule,
                    "reason": f"variable '{variable_name}' is domain '{variable['domain']}'"
                    + (f", CDM table '{table}', which --cdm_domains excludes" if table else ", which has no CDM table"),
                }
            )
            continue

        by_table[table].append({"rule": rule, "variable": variable})

    return by_table, excluded


def _view_key(spec: dict) -> str:
    """A filesystem- and SQL-safe key for one (cohort, dataset) pair.

    Not `hash()`: Python salts str hashing per process, so a view name built
    from it changes between runs and any error message quoting one is
    unreproducible. Built from the ids themselves instead, with everything
    outside [A-Za-z0-9_] replaced, so the name is stable and readable.
    """
    raw = f'{spec["cohort_id"]}_{spec["dataset_id"]}'
    return "".join(c if (c.isalnum() or c == "_") else "_" for c in raw)


def _view_name(spec: dict) -> str:
    return f"src_{_view_key(spec)}"


def _register_table(con: duckdb.DuckDBPyConnection, spec: dict) -> list[str]:
    """Stage one source table as a view carrying the record_id §3 assigned.

    record_id is '<dataset_id>#<row_number>', the SAME construction
    bin/link_blocking.py uses (and bin/link_score.py re-derives in Python),
    because that is what links.parquet's source_row_id holds. It rests on
    duckdb's `preserve_insertion_order` (default true) making a CSV scan
    yield file order; with it off, three files in this repo would silently
    disagree about which row is which person. That is a §3 surface, not one
    this file may change -- but it is what the cardinality guard in main()
    exists to catch.
    """
    # con.register() of a read_csv relation, then a view over it, rather
    # than a parameterised read_csv() inside the CREATE VIEW: duckdb cannot
    # prepare a DDL statement, so the path would have to be interpolated
    # into the SQL text. Same construction bin/link_blocking.py uses, for
    # the same reason.
    raw = f'raw_{_view_key(spec)}'
    view = _view_name(spec)
    con.register(raw, con.read_csv(spec["path"], header=True, all_varchar=True, sample_size=-1))
    con.execute(
        f"CREATE OR REPLACE VIEW {view} AS SELECT *, "
        f"'{spec['dataset_id']}#' || CAST(row_number() OVER () AS VARCHAR) AS __record_id "
        f"FROM {raw}"
    )
    columns = [row[0] for row in con.execute(f"DESCRIBE {view}").fetchall() if row[0] != "__record_id"]
    return columns


def _write_parquet(con: duckdb.DuckDBPyConnection, select_sql: str, out_path: str) -> None:
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    con.execute(f"COPY ({select_sql}) TO '{out_path}' (FORMAT PARQUET)")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="§6.1 apply concept mappings into OMOP rows")
    ap.add_argument("--tables", required=True, help='JSON: [{cohort_id, dataset_id, path}]')
    ap.add_argument("--ruleset", required=True, help="rules/ruleset.json (§5.2's output)")
    ap.add_argument("--links", required=True, help="link/links.parquet (§3.3's output)")
    ap.add_argument("--pack-variables", required=True, help="JSON: the pack's `variables` list")
    ap.add_argument("--vocabulary-release", required=True, help="Recorded, never resolved (Ruling R14)")
    ap.add_argument(
        "--map-params",
        required=True,
        help='JSON: {"cdm_version": str, "cdm_domains": [str], "max_unmapped_frac": float, '
        '"keep_source_concept": bool}',
    )
    ap.add_argument("--out-dir", required=True)
    args = ap.parse_args(argv)

    map_params = json.loads(args.map_params)
    cdm_version = str(map_params["cdm_version"])
    cdm_domains = list(map_params["cdm_domains"])
    max_unmapped_frac = float(map_params["max_unmapped_frac"])
    keep_source_concept = bool(map_params["keep_source_concept"])

    if cdm_version not in {"5.3", "5.4"}:
        raise SystemExit(f"--cdm_version '{cdm_version}' is outside the card's enum (5.3|5.4).")

    tables = json.loads(args.tables)
    pack_variables = json.loads(args.pack_variables)
    with open(args.ruleset, encoding="utf-8") as fh:
        rules = json.load(fh)

    rules_by_table, excluded = ConceptMapper_map(rules, pack_variables, cdm_domains)
    for entry in excluded:
        print(f"NOTE: rule {entry['rule']['rule_id']} not applied: {entry['reason']}", file=sys.stderr)

    con = duckdb.connect()

    # §3.3's links, exploded to one row per source record. A record no
    # blocking rule ever paired is still a person there (a singleton
    # cluster), so this covers every admitted record, not only linked ones.
    con.execute(
        "CREATE OR REPLACE TABLE record_person AS "
        "SELECT unnest(source_row_id) AS record_id, person_id, cohort_id "
        f"FROM read_parquet('{args.links}')"
    )

    mapped_selects: dict[str, list[str]] = {table: [] for table in cdm_domains}
    unmapped_selects: list[str] = []
    n_source_values = 0

    for spec in tables:
        columns = _register_table(con, spec)
        view = _view_name(spec)

        n_rows = con.execute(f"SELECT count(*) FROM {view}").fetchone()[0]

        # The denominator of --max_unmapped_frac: every non-null source
        # value in every admitted column. Nulls are excluded from BOTH sides
        # -- an absent cell is not "a source value with no standard
        # concept", it is no source value at all, and counting it would make
        # the gate fire on sparsity rather than on coverage.
        for column in columns:
            n_source_values += con.execute(
                f'SELECT count("{column}") FROM {view}'
            ).fetchone()[0]

        applicable = {
            table: [
                entry
                for entry in entries
                if entry["rule"]["from"]["cohort_id"] == spec["cohort_id"]
                and entry["rule"]["from"]["dataset_id"] == spec["dataset_id"]
            ]
            for table, entries in rules_by_table.items()
        }
        mapped_columns = {
            entry["rule"]["from"]["column"] for entries in applicable.values() for entry in entries
        }

        for table, entries in applicable.items():
            for entry in entries:
                rule = entry["rule"]
                column = rule["from"]["column"]
                if column not in columns:
                    raise SystemExit(
                        f"rule {rule['rule_id']} maps column '{column}' of "
                        f"{spec['cohort_id']}/{spec['dataset_id']}, which that table does not have."
                    )

                # `<domain>_` is the PACK domain, not the CDM table name --
                # which is what OMOP itself does (condition_occurrence's
                # columns are condition_concept_id, condition_source_value,
                # condition_source_concept_id). The uniform `<domain>_datetime`
                # of the Contract is not OMOP's own spelling on every table;
                # the card's cdm_domains row already says the pipeline
                # "never claims full conformance", and §0.2 says implement to
                # contract.
                domain = entry["variable"]["domain"]
                concept_id = rule["to"].get("concept_id")
                concept_sql = "NULL" if concept_id is None else str(int(concept_id))
                source_concept_sql = (
                    f'{_NO_MATCHING_CONCEPT} AS "{domain}_source_concept_id", ' if keep_source_concept else ""
                )

                mapped_selects[table].append(
                    f"""
                    SELECT
                        rp.person_id                                   AS person_id,
                        '{spec["cohort_id"]}'                          AS cohort_id,
                        {concept_sql}                                  AS "{domain}_concept_id",
                        s."{column}"                                   AS "{domain}_source_value",
                        {source_concept_sql}
                        CAST(NULL AS TIMESTAMP)                        AS "{domain}_datetime",
                        TRY_CAST(s."{column}" AS DOUBLE)               AS value_as_number,
                        '{rule["rule_id"]}'                            AS rule_id,
                        '{rule["rule_version"]}'                       AS rule_version
                    FROM {view} s
                    JOIN record_person rp ON rp.record_id = s.__record_id
                    WHERE s."{column}" IS NOT NULL
                    """
                )

        # Every column with no rule: no standard concept, by definition.
        # Recorded as DISTINCT values with their occurrence count rather than
        # one row per occurrence -- a 100M-row table with one unmapped column
        # would otherwise write a 100M-row _unmapped.parquet, and the
        # question it answers ("which source values have nowhere to go") is
        # about the values, not their multiplicity. The multiplicity is
        # carried in n_rows so the gate's arithmetic stays checkable.
        for column in columns:
            if column in mapped_columns:
                continue
            unmapped_selects.append(
                f"""
                SELECT
                    '{spec["cohort_id"]}'   AS cohort_id,
                    '{spec["dataset_id"]}'  AS dataset_id,
                    '{column}'              AS "column",
                    s."{column}"            AS source_value,
                    count(*)                AS n_rows
                FROM {view} s
                WHERE s."{column}" IS NOT NULL
                GROUP BY s."{column}"
                """
            )

        # The cardinality guard the _register_table docstring names: §3
        # assigned a person to every record it saw, so every record here
        # must find one. A mismatch means the two record_id constructions
        # have drifted apart, and the rows would otherwise be silently
        # dropped by the JOIN with the wrong person attached to the rest.
        n_joined = con.execute(
            f"SELECT count(*) FROM {view} s JOIN record_person rp ON rp.record_id = s.__record_id"
        ).fetchone()[0]
        if n_joined != n_rows:
            raise SystemExit(
                f"{spec['cohort_id']}/{spec['dataset_id']}: {n_rows} source rows but {n_joined} matched a "
                "person in link/links.parquet. The record_id construction here and in §3 have drifted apart; "
                "every mapped row's person would be wrong, not merely missing."
            )

    out_dir = args.out_dir.rstrip("/")

    # Every table named by --cdm_domains is written, empty or not, for the
    # same reason _unmapped.parquet is (the card's own done-when): a
    # downstream stage that has to test for a file's existence before
    # reading it will one day read a missing file as "no rows" on a run
    # where the stage actually failed.
    n_unmapped_values = 0
    for table in cdm_domains:
        selects = mapped_selects.get(table) or []
        if selects:
            sql = "\nUNION ALL\n".join(selects)
            sql = f"SELECT * FROM ({sql}) ORDER BY person_id, rule_id"
        else:
            domain = next((d for d, t in _DOMAIN_TO_CDM_TABLE.items() if t == table), table)
            source_concept_col = (
                f'CAST(NULL AS BIGINT) AS "{domain}_source_concept_id", ' if keep_source_concept else ""
            )
            sql = (
                "SELECT "
                "CAST(NULL AS VARCHAR) AS person_id, "
                "CAST(NULL AS VARCHAR) AS cohort_id, "
                f'CAST(NULL AS BIGINT) AS "{domain}_concept_id", '
                f'CAST(NULL AS VARCHAR) AS "{domain}_source_value", '
                f"{source_concept_col}"
                f'CAST(NULL AS TIMESTAMP) AS "{domain}_datetime", '
                "CAST(NULL AS DOUBLE) AS value_as_number, "
                "CAST(NULL AS VARCHAR) AS rule_id, "
                "CAST(NULL AS VARCHAR) AS rule_version "
                "WHERE false"
            )
        _write_parquet(con, sql, f"{out_dir}/{table}.parquet")

    if unmapped_selects:
        sql = "\nUNION ALL\n".join(unmapped_selects)
        sql = f'SELECT * FROM ({sql}) ORDER BY cohort_id, dataset_id, "column", source_value'
        _write_parquet(con, sql, f"{out_dir}/_unmapped.parquet")
        n_unmapped_values = con.execute(
            f"SELECT coalesce(sum(n_rows), 0) FROM read_parquet('{out_dir}/_unmapped.parquet')"
        ).fetchone()[0]
    else:
        _write_parquet(
            con,
            "SELECT CAST(NULL AS VARCHAR) AS cohort_id, CAST(NULL AS VARCHAR) AS dataset_id, "
            'CAST(NULL AS VARCHAR) AS "column", CAST(NULL AS VARCHAR) AS source_value, '
            "CAST(NULL AS BIGINT) AS n_rows WHERE false",
            f"{out_dir}/_unmapped.parquet",
        )

    unmapped_frac = (n_unmapped_values / n_source_values) if n_source_values else 0.0
    print(
        f"§6.1 map: vocabulary '{args.vocabulary_release}' (recorded, not resolved); CDM {cdm_version}; "
        f"{n_source_values} source values, {n_unmapped_values} unmapped ({unmapped_frac:.4f})",
        file=sys.stderr,
    )
    if unmapped_frac > max_unmapped_frac:
        raise SystemExit(
            f"--max_unmapped_frac is {max_unmapped_frac} and {unmapped_frac:.4f} of source values "
            f"({n_unmapped_values} of {n_source_values}) reached mapped/_unmapped.parquet instead of a "
            "domain table. Either the confirmed ruleset does not cover this data, or it covers the wrong "
            "columns; both are decided in §5, not here."
        )

    return 0


if __name__ == "__main__":
    sys.exit(main())
