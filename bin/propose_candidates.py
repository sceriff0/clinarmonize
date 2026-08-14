#!/usr/bin/env python3
"""§4.1 -- generate candidate concepts from the pack's declared variable set.

Named alts seam (docs/steps/s4-1.md):
    CandidateGenerator_generate(ColumnProfile, Pack) -> [Candidate]

Ruling R14 (no ATHENA release exists in this repo, and the card's own first
line is "the pack IS the target variable set"): candidates are generated
against the PACK's declared variables and their concept_ids, never against a
resolved vocabulary. The pack's `vocabulary` key (the pinned release id) is
read and recorded by the caller (modules/local/propose_candidates/main.nf,
in versions.yml) -- this script never touches it and never resolves it.

Today's three cheap generators (candidate_generators default, s4-1 Params):
    name_ngram  -- character-trigram overlap between the column's raw header
                   and the variable's name. Generic string similarity, no
                   domain vocabulary.
    value_set   -- the column's observed unique-value set overlaps the
                   variable's domain_values. The card's Trap: an
                   institution-local header (VAR_0114) carries no name
                   signal at all, so a generator list without this one
                   silently loses those columns.
    unit        -- the column's candidate_units (from §2.2) overlap the
                   variable's declared unit.

Not here (nogo): never scores or ranks a candidate -- that is §4.2. Never
generates a candidate for a variable the pack marks outcome: true, checked
BEFORE any generator ever sees that variable, so no harmonization decision
here is ever a function of which column happens to look like the outcome
(§0 the invariant). Never falls back to an unpinned vocabulary.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import re
import sys

import duckdb

# A generic string-similarity cutoff, not a domain constant: it decides
# nothing about clinical meaning, only how much of two normalised strings'
# character-trigram sets must coincide before name_ngram treats them as a
# hit. §4.1's Params table lists no generator-specific threshold, so this is
# an implementation detail of the seam's default alternative, swappable
# alongside it (see the Alternatives table: embedding retrieval replaces
# this whole function).
NAME_NGRAM_THRESHOLD = 0.2

# value_set's own analogous cutoff: the fraction of a column's OBSERVED
# unique values that must be drawn from the variable's domain_values before
# the column counts as evidence for that variable. Majority, not unanimity:
# a value set can carry a stray "unknown"/"other" outside the pack's
# enumerated domain without failing to propose the otherwise-obvious match.
VALUE_SET_OVERLAP_THRESHOLD = 0.5

_NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")


def _normalize(s: str) -> str:
    return _NON_ALNUM_RE.sub("", s.lower())


def _char_trigrams(s: str) -> set[str]:
    norm = _normalize(s)
    if not norm:
        return set()
    if len(norm) < 3:
        return {norm}
    return {norm[i : i + 3] for i in range(len(norm) - 2)}


def _name_ngram_match(column_name: str, variable_name: str) -> bool:
    a, b = _char_trigrams(column_name), _char_trigrams(variable_name)
    if not a or not b:
        return False
    overlap = len(a & b) / len(a | b)
    return overlap >= NAME_NGRAM_THRESHOLD


def _value_set_match(unique_values: list[str] | None, domain_values: list | None) -> bool:
    if not domain_values or not unique_values:
        return False
    normalized_domain = {str(v).strip().lower() for v in domain_values}
    normalized_unique = {str(v).strip().lower() for v in unique_values}
    if not normalized_unique:
        return False
    overlap = normalized_unique & normalized_domain
    return len(overlap) / len(normalized_unique) >= VALUE_SET_OVERLAP_THRESHOLD


def _unit_match(candidate_units: list[dict], variable_unit: str | None) -> bool:
    if not variable_unit:
        return False
    ucums = {c["ucum"] for c in candidate_units}
    return variable_unit in ucums


# ---------------------------------------------------------------------------
# §4.1 -- CandidateGenerator alts seam. Today's only implementation: the
# three cheap generators above, gated by --candidate_generators (s4-1
# Params). The Alternatives table's "embedding retrieval" and "full cross
# product" would each replace only this function's body.
# ---------------------------------------------------------------------------
def CandidateGenerator_generate(column_profile: dict, variables: list[dict], enabled_generators: set[str]) -> list[dict]:
    column_name = column_profile["column"]
    unique_values = column_profile.get("unique_values") or []
    candidate_units = column_profile.get("candidate_units") or []

    proposals: list[dict] = []
    for variable in variables:
        var_name = variable["name"]
        matched_by: list[str] = []

        if "name_ngram" in enabled_generators and _name_ngram_match(column_name, var_name):
            matched_by.append("name_ngram")
        if "value_set" in enabled_generators and _value_set_match(unique_values, variable.get("domain_values")):
            matched_by.append("value_set")
        if "unit" in enabled_generators and _unit_match(candidate_units, variable.get("unit")):
            matched_by.append("unit")

        for generator_id in matched_by:
            proposals.append(
                {
                    "variable": var_name,
                    "concept_id": variable.get("concept_id"),
                    "generator_id": generator_id,
                }
            )
    return proposals


def _apply_recall_ceiling(proposals: list[dict], max_candidates_per_column: int) -> list[dict]:
    """§4.1 Params: max_candidates_per_column is the recall ceiling on
    DISTINCT variables proposed for one column -- not on the number of
    generator hits, which can legitimately exceed it when several
    generators agree on the same variable. Variables beyond the ceiling are
    dropped, in a fixed alphabetical order: a deterministic bound, not a
    score (§4.1 nogo: "do not score or rank -- §4.2 owns that")."""
    distinct_variables = sorted({p["variable"] for p in proposals})
    if len(distinct_variables) <= max_candidates_per_column:
        return proposals
    kept = set(distinct_variables[:max_candidates_per_column])
    return [p for p in proposals if p["variable"] in kept]


def _parse_cohort_dataset(profile_path: str) -> tuple[str, str]:
    base = os.path.basename(profile_path)
    stem = base[: -len(".json")] if base.endswith(".json") else base
    cohort_id, sep, dataset_id = stem.rpartition(".")
    if not sep or not cohort_id or not dataset_id:
        raise ValueError(
            f"cannot parse cohort/dataset id from profile filename '{base}' "
            "(expected '<cohort_id>.<dataset_id>.json')"
        )
    return cohort_id, dataset_id


def _write_candidates_parquet(rows: list[dict], out_path: str) -> None:
    con = duckdb.connect()
    con.execute(
        """
        CREATE TABLE candidates (
            cohort_id    VARCHAR,
            dataset_id   VARCHAR,
            "column"     VARCHAR,
            variable     VARCHAR,
            concept_id   BIGINT,
            generator_id VARCHAR
        )
        """
    )
    if rows:
        con.executemany(
            'INSERT INTO candidates VALUES (?, ?, ?, ?, ?, ?)',
            [
                (r["cohort_id"], r["dataset_id"], r["column"], r["variable"], r["concept_id"], r["generator_id"])
                for r in rows
            ],
        )
    con.execute(f"COPY candidates TO '{out_path}' (FORMAT PARQUET)")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--profile-glob", required=True, help="Glob over one replicate's collected *.json column profiles.")
    ap.add_argument("--pack-variables", required=True, help="JSON list of the pack's variable dicts (name, domain, concept_id, type, domain_values, unit, outcome, ...).")
    ap.add_argument(
        "--generator-params",
        required=True,
        help='JSON object: {"max_candidates_per_column": int, "candidate_generators": [str, ...]}',
    )
    ap.add_argument("--out-candidates", required=True)
    args = ap.parse_args(argv)

    all_variables = json.loads(args.pack_variables)
    generator_params = json.loads(args.generator_params)
    max_candidates_per_column = generator_params["max_candidates_per_column"]
    enabled_generators = set(generator_params["candidate_generators"])

    # §4.1 nogo, enforced structurally: an outcome-flagged variable never
    # reaches a generator, so no generator's match logic can ever be a
    # function of which column happens to look like the outcome (§0).
    variables = [v for v in all_variables if not v.get("outcome")]

    rows: list[dict] = []
    for path in sorted(glob.glob(args.profile_glob)):
        cohort_id, dataset_id = _parse_cohort_dataset(path)
        with open(path, encoding="utf-8") as fh:
            profiles = json.load(fh)

        for column_profile in profiles:
            proposals = CandidateGenerator_generate(column_profile, variables, enabled_generators)
            proposals = _apply_recall_ceiling(proposals, max_candidates_per_column)

            if not proposals:
                # §4.1 SIDE: a column with zero candidates is emitted with a
                # null concept_id -- recorded, not silently dropped, so the
                # recall ceiling's exclusions stay auditable.
                rows.append(
                    {
                        "cohort_id": cohort_id,
                        "dataset_id": dataset_id,
                        "column": column_profile["column"],
                        "variable": None,
                        "concept_id": None,
                        "generator_id": None,
                    }
                )
            else:
                for p in proposals:
                    rows.append(
                        {
                            "cohort_id": cohort_id,
                            "dataset_id": dataset_id,
                            "column": column_profile["column"],
                            "variable": p["variable"],
                            "concept_id": p["concept_id"],
                            "generator_id": p["generator_id"],
                        }
                    )

    rows.sort(key=lambda r: (r["cohort_id"], r["dataset_id"], r["column"], r["variable"] or "", r["generator_id"] or ""))
    _write_candidates_parquet(rows, args.out_candidates)
    return 0


if __name__ == "__main__":
    sys.exit(main())
