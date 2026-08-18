#!/usr/bin/env python3
"""§6.2 -- convert units through UCUM, refusing ambiguity.

Named alts seam (docs/steps/s6-2.md):
    UnitConverter -- convert(value, from, to, analyte) -> value

IN   mapped/<domain>.parquet + rules (unit_in, unit_out) + pack ranges
OUT  same rows, value_as_number converted; unit_concept_id set
     qc/unit_conversions.json  [{rule_id, from, to, factor, n_rows}]
SIDE fails the run on an ambiguous or missing conversion factor

Where unit_in and unit_out come from
------------------------------------
The card's IN slot names "rules (unit_in, unit_out)" and only one of the two
is on the rule. `unit_in` is: §5.1's ledger carries "the resolved unit; §2.2
only ranked them", and bin/compile_rules.py records it verbatim into every
rule's `params` -- deliberately, and its docstring says why in as many words
("§6 map ... is where an actual unit conversion, with the pack's canonical
unit in hand, belongs"). `unit_out` is the PACK variable's `unit`, and it can
be nowhere else: the card's nogo is "Never convert to a unit the pack does
not declare", which only means anything if the pack is what declares it.

So the pair is (rule.params.unit_in, pack_variable.unit), and this script
never re-reads §2.2's ranked candidate_units. A ranking is candidates; a
conversion needs a decision, and the decision was made at the human gate.

The four cases, and why one of them is an exit code
--------------------------------------------------
  unit_out is None                 -> nothing to convert TO. Passed through
                                      untouched, status `no-target-unit`,
                                      recorded in the qc file rather than
                                      passed over in silence.
  unit_in == unit_out              -> factor 1.0, status `identity`, no
                                      lookup. Converting a unit to itself
                                      has no molar ambiguity to resolve, and
                                      §5.2's own Contract example is exactly
                                      this ({"unit_in":"%","unit_out":"%",
                                      "factor":1.0}).
  unit_in is None, unit_out is not -> REFUSED. The pack asserts this variable
                                      is expressed in a specific unit and the
                                      source unit was never resolved, so
                                      emitting the number anyway would claim
                                      a unit on no evidence. That is the
                                      card's nogo ("Do not guess a unit when
                                      candidate_units was empty") and its Why
                                      ("Refusing an ambiguous conversion is
                                      cheaper than detecting one") pointing
                                      the same direction. The fix is a
                                      `unit_in` on the ledger row, which is a
                                      §5 decision, not a §6 default.
  both present and different       -> looked up in --unit_conversion_table,
                                      by (analyte, from, to). A miss is an
                                      exit code, never 1.0.

`analyte` is the pack VARIABLE's name. That is the card's Contract signature
(`convert(v, "mg/dL", "mmol/L", analyte)`) resolved against the only identity
this pipeline has for a measured substance: a source column header is one
cohort's spelling, and two cohorts' columns mapping to one variable must
convert identically or the harmonization did nothing.

Why the factor is looked up per RULE and applied in SQL
------------------------------------------------------
UnitConverter_convert() below is the seam's literal signature and is what
decides the number. UnitConverter_factor() is what it delegates to, and the
column rewrite multiplies by that same value inside duckdb. The alternative
-- a Python callback per row -- would put the seam on the hot path of a
table this pipeline expects to reach hundreds of millions of rows, and would
buy nothing: the decision is per (analyte, from, to), which is constant
within a rule. The seam is where the decision is made, not where the
multiplication happens.

The post-condition, and what it is checked over
-----------------------------------------------
"pack.plausible_range contains p1..p99 of the result". Three deliberate
readings:

  * "of the RESULT" -- the check runs on CONVERTED values. An unconverted
    column's implausibility is a §2 profiling concern; firing on it here
    would make --fail_on_implausible_range a data-quality gate that stops
    runs for reasons §6.2 did not cause.
  * p1..p99 is --plausible_range_quantiles, whose effect column says the
    quantiles exist "so outliers alone cannot fail it". They are read from
    the param on every check; nothing here hard-codes 0.01 or 0.99.
  * the range is a property of the VARIABLE, so it is evaluated per variable
    over every rule that writes it, and the verdict is then recorded on each
    of those rules' qc entries. Two cohorts' columns mapping to one variable
    are one distribution, and checking them separately would let a
    half-sized cohort's wrong factor hide inside a right one.

A flagged variable writes qc/unit_conversions.json BEFORE the exit, so the
file exists in the task's work directory on a failed run even though
Nextflow will not publish a failed task's output. The exit message carries
the same numbers, because an operator reading a red run should not have to
find a work directory to learn which analyte and which factor.

unit_concept_id, and Ruling R14 again
-------------------------------------
Set to 0 -- OMOP's designated "no matching concept" -- for the reason
bin/map_concepts.py sets `<domain>_source_concept_id` to 0: Ruling R14, no
ATHENA release is vendored and none is resolved, so there is no concept to
look "umol/L" up as. The OUT slot says unit_concept_id is set and this sets
it truthfully. `unit_source_value` carries the unit STRING beside it, so the
artefact still says what the number is in; without it the conversion would
be recorded only in a qc file, and a mapped table that does not name its own
units is the artefact this card exists to prevent.
"""
from __future__ import annotations

import argparse
import glob as globlib
import json
import os
import sys

import duckdb
import yaml

# OMOP's designated "no matching concept", same constant and same reason as
# bin/map_concepts.py's. Ruling R14: recorded, never resolved.
_NO_MATCHING_CONCEPT = 0

# Tables that carry no CDM row and are copied through verbatim.
# mapped/_unmapped.parquet is a record of source values with nowhere to go
# (§6.1); it has no value_as_number to convert and no variable to convert it
# for, and giving it unit columns would imply it had been through a
# conversion it was excluded from by construction.
_PASSTHROUGH_BASENAMES = {"_unmapped.parquet"}


class ConversionError(SystemExit):
    """Raised for every refusal in this file, so the caller cannot catch one
    class of unit failure while letting another through."""


def load_factor_table(path: str) -> dict[tuple[str, str, str], dict]:
    """Read --unit_conversion_table into {(analyte, from, to): row}.

    Duplicate keys are an ERROR, not a last-one-wins: the SIDE clause names
    an "ambiguous" factor first, and a table carrying two answers for one
    question is exactly that, whether or not the two answers agree. A pair
    that agrees is a table someone edited twice and will one day edit once.
    """
    with open(path, encoding="utf-8") as fh:
        document = yaml.safe_load(fh) or {}

    rows = document.get("factors")
    if not isinstance(rows, list):
        raise ConversionError(
            f"--unit_conversion_table '{path}' has no `factors:` list. It is the only source of "
            "conversions this pipeline has; an unreadable one is not an empty one."
        )

    table: dict[tuple[str, str, str], dict] = {}
    for index, row in enumerate(rows):
        missing = [key for key in ("analyte", "from", "to", "factor") if row.get(key) is None]
        if missing:
            raise ConversionError(
                f"--unit_conversion_table '{path}' row {index} is missing {missing}. Every row names an "
                "analyte: there is no universal factor for a unit pair, so a row without one could only "
                "ever be applied by guessing which analyte it meant."
            )
        key = (str(row["analyte"]), str(row["from"]), str(row["to"]))
        if key in table:
            raise ConversionError(
                f"--unit_conversion_table '{path}' declares {key[1]} -> {key[2]} for analyte '{key[0]}' "
                f"twice (factors {table[key]['factor']} and {row['factor']}). Which one applies is "
                "ambiguous; §6.2 refuses an ambiguous conversion rather than picking one."
            )
        table[key] = {"factor": float(row["factor"]), "source": row.get("source")}
    return table


def UnitConverter_factor(
    analyte: str,
    from_unit: str,
    to_unit: str,
    table: dict[tuple[str, str, str], dict],
) -> float:
    """The seam's decision: which number multiplies this analyte's values.

    Split out from UnitConverter_convert() below so the DECISION is reachable
    without a value in hand -- the column rewrite needs the factor once, not
    once per row, and a seam that could only be exercised by passing it a
    number would be a seam nothing could reuse.

    An exact (analyte, from, to) match or nothing. No inverse is derived from
    a reverse row, no compound unit is parsed, and no fallback is consulted:
    every one of those would manufacture a factor the table's author never
    reviewed, and the whole point of a single declared source of conversions
    is that a human has looked at each one.
    """
    if from_unit == to_unit:
        return 1.0

    row = table.get((analyte, from_unit, to_unit))
    if row is None:
        offered = sorted(
            f"{key[1]} -> {key[2]}" for key in table if key[0] == analyte
        )
        raise ConversionError(
            f"no conversion factor for analyte '{analyte}': {from_unit} -> {to_unit}. "
            + (
                f"The table offers {analyte}: {', '.join(offered)}."
                if offered
                else f"The table names no factor for '{analyte}' at all."
            )
            + " §6.2 refuses a missing factor rather than defaulting to 1.0: a fallback would emit "
            f"{from_unit} values under a column the pack declares as {to_unit}, and every downstream "
            "check would agree with it -- the type is right, the values are numbers, and only a "
            "comparison against another cohort would ever expose it."
        )
    return row["factor"]


def UnitConverter_convert(
    value: float | None,
    from_unit: str,
    to_unit: str,
    analyte: str,
    table: dict[tuple[str, str, str], dict],
) -> float | None:
    """The alts seam, at its Contract's literal signature:

        convert(v, "mg/dL", "mmol/L", analyte) -> v * factor(analyte)

    Kept even though the bulk path multiplies inside duckdb, because the seam
    is what an alternative implementation replaces (the card's Alternatives
    table names a UCUM library), and an interface that exists only as a SQL
    fragment is not one anything can be swapped in behind (Global Constraint
    7).
    """
    if value is None:
        return None
    return value * UnitConverter_factor(analyte, from_unit, to_unit, table)


def plan_conversions(
    rules: list[dict],
    pack_variables: list[dict],
    table: dict[tuple[str, str, str], dict],
) -> dict[str, dict]:
    """One entry per column_map rule: what happens to its rows' units.

    Every refusal in the four-case table at the top of this file happens
    here, before a single value is written -- which is the whole reason the
    plan is built as its own pass. A converter that discovered a missing
    factor halfway through a rewrite would leave half a mapped table
    converted and half not, and the two halves would be indistinguishable
    afterwards.
    """
    variable_by_name = {variable["name"]: variable for variable in pack_variables}
    plan: dict[str, dict] = {}

    for rule in rules:
        if rule.get("kind") != "column_map":
            # §6.1 applies column maps and this converts what §6.1 wrote.
            # value_map is §6.3's and derive is §7's, each applied by the
            # stage that owns it.
            continue

        variable_name = rule["to"]["variable"]
        variable = variable_by_name.get(variable_name)
        if variable is None:
            # bin/map_concepts.py raises on this first and would have
            # stopped the run before anything reached here; the check is
            # repeated rather than assumed because this script is also
            # reachable on its own, and a plan built against a pack that
            # does not declare the variable would silently convert nothing.
            raise ConversionError(
                f"rule {rule['rule_id']} maps to variable '{variable_name}', which the concept pack does "
                "not declare. The ruleset was compiled against a different pack."
            )

        unit_out = variable.get("unit")
        unit_in = (rule.get("params") or {}).get("unit_in")

        if unit_out is None:
            plan[rule["rule_id"]] = {
                "rule_id": rule["rule_id"],
                "variable": variable_name,
                "from": unit_in,
                "to": None,
                "factor": None,
                "status": "no-target-unit",
                "plausible_range": variable.get("plausible_range"),
            }
            continue

        if unit_in is None:
            raise ConversionError(
                f"rule {rule['rule_id']} writes '{variable_name}', which the pack declares in "
                f"'{unit_out}', and the confirmed ledger resolved no unit_in for "
                f"{rule['from']['cohort_id']}/{rule['from']['dataset_id']}/{rule['from']['column']}. "
                "§6.2 will not assume the source is already in the target unit: that is a claim about "
                "the data made on no evidence, and it is invisible in the output. Add `unit_in` to that "
                "ledger row (§5.1) and re-confirm."
            )

        factor = UnitConverter_factor(variable_name, unit_in, unit_out, table)
        plan[rule["rule_id"]] = {
            "rule_id": rule["rule_id"],
            "variable": variable_name,
            "from": unit_in,
            "to": unit_out,
            "factor": factor,
            "status": "identity" if unit_in == unit_out else "converted",
            "plausible_range": variable.get("plausible_range"),
        }

    return plan


def _sql_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _case_expression(plan: dict[str, dict], key: str, default_sql: str, quote: bool) -> str:
    """A CASE over rule_id, or the default when no rule contributes one.

    duckdb rejects an empty CASE, and an empty plan is the ordinary state of
    a table no rule writes -- §6.1 emits every --cdm_domains table whether or
    not it has rows.
    """
    branches = []
    for entry in plan.values():
        value = entry[key]
        if value is None:
            continue
        rendered = _sql_literal(str(value)) if quote else repr(float(value))
        branches.append(f"WHEN {_sql_literal(entry['rule_id'])} THEN {rendered}")
    if not branches:
        return default_sql
    return "CASE rule_id " + " ".join(branches) + f" ELSE {default_sql} END"


def rewrite_table(
    con: duckdb.DuckDBPyConnection,
    source_path: str,
    out_path: str,
    plan: dict[str, dict],
) -> None:
    """Rewrite one mapped table with converted values and its unit columns.

    `SELECT * REPLACE (...)` rather than an explicit column list: the five
    CDM tables §6.1 emits do not share a schema (each carries its own
    `<domain>_concept_id` / `<domain>_source_value` / `<domain>_datetime`),
    so an explicit list would be five lists to keep in step with a file this
    script does not own. REPLACE also keeps value_as_number in ITS OWN
    POSITION, which `* EXCLUDE (...)` plus a re-added column would not --
    and bin/artefact_digest.py hashes columns in declared order, so a
    positional shift would move every §10.1 digest for a reason that is not
    data.

    Row order is not touched. §6.1 wrote `ORDER BY person_id, rule_id` and
    owns that decision; re-sorting here would put a second answer in the repo
    about what a mapped table's order is.
    """
    factor_case = _case_expression(plan, "factor", "1.0", quote=False)
    unit_case = _case_expression(plan, "to", "CAST(NULL AS VARCHAR)", quote=True)
    con.execute(
        f"""
        COPY (
            SELECT * REPLACE (CAST(value_as_number * ({factor_case}) AS DOUBLE) AS value_as_number),
                   CAST({_NO_MATCHING_CONCEPT} AS BIGINT) AS unit_concept_id,
                   CAST({unit_case} AS VARCHAR)           AS unit_source_value
            FROM read_parquet({_sql_literal(source_path)})
        ) TO {_sql_literal(out_path)} (FORMAT PARQUET)
        """
    )


def check_ranges(
    con: duckdb.DuckDBPyConnection,
    out_dir: str,
    plan: dict[str, dict],
    quantiles: list[float],
) -> dict[str, dict]:
    """Evaluate the post-condition per VARIABLE over the converted tables.

    Returns {variable: verdict}. A variable with no plausible_range gets a
    verdict too (`no-plausible-range`) rather than being omitted: the qc file
    is the record of what the post-condition did, and a silently absent
    variable and a silently passing one read identically.
    """
    lo_q, hi_q = float(quantiles[0]), float(quantiles[1])
    rule_ids_by_variable: dict[str, list[str]] = {}
    for entry in plan.values():
        rule_ids_by_variable.setdefault(entry["variable"], []).append(entry["rule_id"])

    parquet_files = sorted(globlib.glob(os.path.join(out_dir, "*.parquet")))
    domain_files = [
        path for path in parquet_files if os.path.basename(path) not in _PASSTHROUGH_BASENAMES
    ]

    verdicts: dict[str, dict] = {}
    for variable, rule_ids in sorted(rule_ids_by_variable.items()):
        entry = plan[rule_ids[0]]
        plausible_range = entry["plausible_range"]

        if entry["status"] == "no-target-unit":
            # Nothing was converted, so there is no "result" for the range to
            # contain -- the post-condition's own words. A variable the pack
            # declares no `unit` for is passed through in whatever unit its
            # source used, and checking a range against those values would
            # make --fail_on_implausible_range a data-quality gate on numbers
            # §6.2 never touched. That question belongs to §2's profiling.
            #
            # Every rule for one variable shares its pack `unit`, so this is
            # all-or-nothing per variable and never splits a distribution.
            verdicts[variable] = {
                "variable": variable,
                "quantiles": [lo_q, hi_q],
                "observed": [None, None],
                "plausible_range": list(plausible_range) if plausible_range else None,
                "n_values": 0,
                "flag": None,
                "status": "not-converted",
            }
            continue

        observed_lo = observed_hi = None
        n_values = 0
        if domain_files:
            in_list = ", ".join(_sql_literal(rule_id) for rule_id in sorted(rule_ids))
            file_list = "[" + ", ".join(_sql_literal(path) for path in domain_files) + "]"
            row = con.execute(
                f"SELECT count(value_as_number), "
                f"quantile_cont(value_as_number, {lo_q}), quantile_cont(value_as_number, {hi_q}) "
                f"FROM read_parquet({file_list}, union_by_name = true) "
                f"WHERE rule_id IN ({in_list})"
            ).fetchone()
            n_values = int(row[0])
            observed_lo, observed_hi = row[1], row[2]

        if plausible_range is None:
            verdicts[variable] = {
                "variable": variable,
                "quantiles": [lo_q, hi_q],
                "observed": [observed_lo, observed_hi],
                "plausible_range": None,
                "n_values": n_values,
                "flag": None,
                "status": "no-plausible-range",
            }
            continue

        if n_values == 0 or observed_lo is None:
            # Nothing converted, so there is no result for the range to
            # contain. Recorded, and NOT a pass: a variable whose rows all
            # failed to parse as numbers has a units question nobody has
            # answered, and calling that green would be the vacuity §10.1's
            # own report has verdicts for.
            verdicts[variable] = {
                "variable": variable,
                "quantiles": [lo_q, hi_q],
                "observed": [None, None],
                "plausible_range": list(plausible_range),
                "n_values": 0,
                "flag": None,
                "status": "no-numeric-values",
            }
            continue

        low, high = float(plausible_range[0]), float(plausible_range[1])
        inside = low <= float(observed_lo) and float(observed_hi) <= high
        verdicts[variable] = {
            "variable": variable,
            "quantiles": [lo_q, hi_q],
            "observed": [float(observed_lo), float(observed_hi)],
            "plausible_range": [low, high],
            "n_values": n_values,
            "flag": None if inside else "implausible_after_conversion",
            "status": "in-range" if inside else "out-of-range",
        }

    return verdicts


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="§6.2 convert units through UCUM, refusing ambiguity")
    ap.add_argument("--mapped-glob", required=True, help="Glob over the staged mapped/*.parquet")
    ap.add_argument("--ruleset", required=True, help="rules/ruleset.json (§5.2's output)")
    ap.add_argument("--pack-variables", required=True, help="JSON: the pack's `variables` list")
    ap.add_argument("--unit-conversion-table", required=True, help="The ONLY source of conversions")
    ap.add_argument(
        "--convert-params",
        required=True,
        help='JSON: {"fail_on_implausible_range": bool, "plausible_range_quantiles": [float, float]}',
    )
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--out-qc", required=True)
    args = ap.parse_args(argv)

    convert_params = json.loads(args.convert_params)
    fail_on_implausible_range = bool(convert_params["fail_on_implausible_range"])
    quantiles = [float(q) for q in convert_params["plausible_range_quantiles"]]
    if not 0.0 <= quantiles[0] <= quantiles[1] <= 1.0:
        raise ConversionError(
            f"--plausible_range_quantiles {quantiles} is not a non-decreasing pair inside [0, 1]."
        )

    pack_variables = json.loads(args.pack_variables)
    with open(args.ruleset, encoding="utf-8") as fh:
        rules = json.load(fh)

    table = load_factor_table(args.unit_conversion_table)
    plan = plan_conversions(rules, pack_variables, table)

    sources = sorted(globlib.glob(args.mapped_glob))
    if not sources:
        raise ConversionError(
            f"--mapped-glob '{args.mapped_glob}' matched no parquet file. §6.1 emits every "
            "--cdm_domains table and _unmapped.parquet unconditionally, so an empty match means the "
            "mapped artefact did not arrive, not that it was empty."
        )

    out_dir = args.out_dir.rstrip("/")
    os.makedirs(out_dir, exist_ok=True)
    con = duckdb.connect()

    n_rows_by_rule: dict[str, int] = {}
    for source in sources:
        basename = os.path.basename(source)
        out_path = os.path.join(out_dir, basename)
        if basename in _PASSTHROUGH_BASENAMES:
            con.execute(
                f"COPY (SELECT * FROM read_parquet({_sql_literal(source)})) "
                f"TO {_sql_literal(out_path)} (FORMAT PARQUET)"
            )
            continue
        rewrite_table(con, source, out_path, plan)
        for rule_id, count in con.execute(
            f"SELECT rule_id, count(*) FROM read_parquet({_sql_literal(out_path)}) "
            "WHERE rule_id IS NOT NULL GROUP BY rule_id"
        ).fetchall():
            n_rows_by_rule[rule_id] = n_rows_by_rule.get(rule_id, 0) + int(count)

    verdicts = check_ranges(con, out_dir, plan, quantiles)

    # The card's OUT slot, in its own order, one entry per rule. `range_check`
    # is the VARIABLE's verdict repeated onto each rule that writes it --
    # deliberately, so that an entry read on its own says whether the values
    # it produced were checked and what the check said, without the reader
    # having to join two files to find out.
    conversions = []
    for rule_id in sorted(plan):
        entry = plan[rule_id]
        n_rows = n_rows_by_rule.get(rule_id, 0)
        conversions.append(
            {
                "rule_id": rule_id,
                "variable": entry["variable"],
                "from": entry["from"],
                "to": entry["to"],
                "factor": entry["factor"],
                "n_rows": n_rows,
                "status": entry["status"],
                "range_check": verdicts.get(entry["variable"]),
            }
        )

    os.makedirs(os.path.dirname(args.out_qc) or ".", exist_ok=True)
    with open(args.out_qc, "w", encoding="utf-8") as fh:
        json.dump(conversions, fh, indent=2)
        fh.write("\n")

    flagged = [
        verdict for verdict in verdicts.values() if verdict["flag"] == "implausible_after_conversion"
    ]
    for verdict in flagged:
        factor = next(
            entry["factor"] for entry in plan.values() if entry["variable"] == verdict["variable"]
        )
        print(
            f"implausible_after_conversion: '{verdict['variable']}' converted with factor {factor} "
            f"has quantiles {verdict['quantiles']} at {verdict['observed']}, outside the pack's "
            f"plausible_range {verdict['plausible_range']} (over {verdict['n_values']} values).",
            file=sys.stderr,
        )

    print(
        f"§6.2 units: {len(plan)} rule(s), "
        f"{sum(1 for e in plan.values() if e['status'] == 'converted')} converted, "
        f"{len(flagged)} flagged implausible_after_conversion",
        file=sys.stderr,
    )

    if flagged and fail_on_implausible_range:
        raise ConversionError(
            "--fail_on_implausible_range is true and "
            f"{len(flagged)} converted distribution(s) fall outside the pack's plausible_range: "
            + "; ".join(
                f"{v['variable']} at {v['observed']} against {v['plausible_range']}" for v in flagged
            )
            + ". A factor that is wrong for the analyte produces values that are plausible in TYPE and "
            "wrong in magnitude, which is the one unit error nothing downstream can see. Fix the "
            "factor, not the range (§6.2 nogo: do not silence the check with a wider range). "
            f"qc/unit_conversions.json in this task's work directory carries the full verdict."
        )

    return 0


if __name__ == "__main__":
    sys.exit(main())
