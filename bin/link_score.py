#!/usr/bin/env python3
"""§3.2 -- score pairs with a Fellegi-Sunter model, missing as its own outcome.

Named alts seam (docs/steps/s3-2.md):
    PairScorer_score(pair) -> (weight, [FieldAgreement])

m and u -- agreement given the SAME person, agreement given DIFFERENT people
-- turn field comparisons into a log-likelihood ratio that is comparable
across fields:

    match_weight = log2( P(pattern | match) / P(pattern | non-match) )
                 = sum_over_fields log2( m_f(level) / u_f(level) )

Everything about the linkage's honesty lives in whether those two
probabilities were ESTIMATED or ASSUMED, which is why link/model.json
records, per field and per level, which of the two happened.

Contract (docs/steps/s3-2.md):
    IN   candidate pairs + the cohort's records
    OUT  link/scores.parquet  (left_id, right_id, match_weight,
                               per_field_agreement)
         link/model.json      the estimated m and u per field, per level
    SIDE none

The Trap, and the reason this file is longer than a binary comparator would
be: agreement is THREE-WAY -- agree | disagree | missing -- and `missing`
gets its own (m, u) pair. Folding it into `disagree` pushes true matches
below the threshold, which looks exactly like under-linkage and gets "fixed"
by moving the wrong knob (§3.3's threshold), which is the wrong knob.

Not here (nogo): this script does not accept or reject a pair -- §3.3 owns
the threshold. And no outcome-flagged column may ever be a comparison
field, even when it would help; _refuse_outcome_fields() enforces that
before any comparison runs.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import random
import re
import sys

import duckdb
import yaml

LEVELS = ("agree", "disagree", "missing")

# A probability is clamped away from 0 and 1 before it is logged. m or u at
# exactly 0 makes match_weight +/-inf, which is not a weight a threshold can
# be compared against and not a number parquet round-trips usefully; the
# clamp is what keeps an unobserved level a very strong signal rather than
# an infinite one.
_EPS = 1e-9

_NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")


def _normalize(s: str) -> str:
    return _NON_ALNUM_RE.sub("", str(s).lower())


# ---------------------------------------------------------------------------
# Comparison spec
# ---------------------------------------------------------------------------
def load_comparisons(path: str) -> list[dict]:
    with open(path, encoding="utf-8") as handle:
        raw = yaml.safe_load(handle) or {}
    fields = raw.get("fields")
    if not isinstance(fields, list) or not fields:
        raise ValueError(f"{path} must declare a non-empty `fields:` list")
    out = []
    for entry in fields:
        # Same YAML 1.1 hazard bin/link_blocking.py documents: a bare `on:`
        # key resolves to boolean true, not the string "on".
        column = entry.get("on", entry.get(True))
        if column is None:
            raise ValueError(f"comparison field '{entry.get('name')}' has no `on:` column")
        spec = {
            "name": str(entry["name"]),
            "column": str(column),
            "compare": str(entry.get("compare", "exact")),
            "m": {level: float(entry["m"][level]) for level in LEVELS},
            "u": {level: float(entry["u"][level]) for level in LEVELS},
        }
        for probs in ("m", "u"):
            total = sum(spec[probs].values())
            if not math.isclose(total, 1.0, abs_tol=1e-6):
                raise ValueError(
                    f"comparison field '{spec['name']}' has {probs} priors summing to {total:.6f}, not 1.0. "
                    "agree/disagree/missing are the three exhaustive outcomes of one comparison "
                    "(§3.2's whole point), so their probabilities have to be a distribution."
                )
        out.append(spec)
    return out


def _refuse_outcome_fields(fields: list[dict], outcome_variables: list[str]) -> None:
    """§3.2 nogo / Global Constraint 1: never include an outcome-flagged
    column as a comparison field, even when it would help. Refused by NAME
    against the pack's outcome-flagged variables (never a hard-coded column
    name), and fatally: silently dropping the field would change the model's
    weights without changing anything a reader of model.json could see."""
    outcome_norm = {_normalize(v): v for v in outcome_variables}
    for field in fields:
        hit = outcome_norm.get(_normalize(field["column"]))
        if hit is not None:
            raise ValueError(
                f"comparison field '{field['name']}' compares column '{field['column']}', which is named "
                f"by the outcome-flagged pack variable '{hit}'. §0's invariant forbids a linkage decision "
                "from being a function of the outcome -- and a linkage that used it would score true "
                "matches higher precisely when they share an outcome, which is the leak the whole "
                "pipeline is built to prevent."
            )


# ---------------------------------------------------------------------------
# Three-way comparison -- the step itself
# ---------------------------------------------------------------------------
def compare_values(left, right, how: str) -> str:
    """agree | disagree | missing. `missing` is returned when EITHER side is
    absent: the pattern being modelled is "this comparison could not be
    made", and it is symmetric."""
    left_missing = left is None or str(left).strip() == ""
    right_missing = right is None or str(right).strip() == ""
    if left_missing or right_missing:
        return "missing"

    a, b = str(left).strip(), str(right).strip()
    if how == "exact":
        return "agree" if a == b else "disagree"
    if how == "exact_ci":
        return "agree" if a.lower() == b.lower() else "disagree"
    if how.startswith("prefix:"):
        n = int(how.split(":", 1)[1])
        return "agree" if a[:n].lower() == b[:n].lower() else "disagree"
    if how.startswith("numeric:"):
        tol = float(how.split(":", 1)[1])
        try:
            return "agree" if abs(float(a) - float(b)) <= tol else "disagree"
        except ValueError:
            # Unparseable is NOT missing: the value is present, it just does
            # not compare numerically. Calling it missing would hide a
            # type problem inside the level that is supposed to mean
            # "absent".
            return "disagree"
    raise ValueError(f"unknown comparison '{how}'")


# ---------------------------------------------------------------------------
# u from random pairs (§3.2 Params: link_u_from_random_pairs)
# ---------------------------------------------------------------------------
def estimate_u_from_random_pairs(records: dict, cohorts: dict, fields: list[dict], n_sample: int, seed: int) -> dict:
    """u_f(level) = P(level | the two records are DIFFERENT people).

    Estimated by sampling random within-cohort pairs. Two randomly drawn
    records are overwhelmingly not the same person, so their level
    distribution is a direct estimate of u -- which is the whole reason the
    card's default is `true`: an assumed u is a number nobody measured, and
    it sits inside every match weight.

    Sampling is seeded and the seed is recorded in model.json. An
    unreproducible u makes an unreproducible weight, and a threshold
    compared against an unreproducible weight is not a decision anyone can
    audit. When the whole population of within-cohort pairs is smaller than
    the requested sample, it is ENUMERATED instead and the seed stops
    mattering at all -- see below.
    """
    rng = random.Random(seed)
    counts = {f["name"]: {level: 0 for level in LEVELS} for f in fields}
    ids_by_cohort = {c: sorted(ids) for c, ids in cohorts.items() if len(ids) > 1}
    if not ids_by_cohort:
        return {}

    # How many DISTINCT within-cohort pairs exist at all. Cross-cohort pairs
    # are excluded for the same reason blocking never proposes one (§1.1):
    # they are not candidates, so their agreement rate is not the u this
    # model needs.
    n_distinct = sum(len(ids) * (len(ids) - 1) // 2 for ids in ids_by_cohort.values())

    drawn = 0
    if n_distinct <= n_sample:
        # Enumerate rather than sample. Drawing n_sample times with
        # replacement from a population smaller than n_sample re-counts the
        # same pairs and converges to exactly this answer, only slower and
        # with sampling noise: on a 24-record fixture the default
        # link_random_pair_n of 1e6 would draw a million times from 276
        # distinct pairs. Enumeration makes u EXACT for small inputs and
        # removes the seed from the result entirely, which is why
        # model.json reports random_pairs_drawn alongside it.
        for ids in ids_by_cohort.values():
            for i in range(len(ids)):
                for j in range(i + 1, len(ids)):
                    drawn += 1
                    for field in fields:
                        level = compare_values(
                            records[ids[i]].get(field["column"]),
                            records[ids[j]].get(field["column"]),
                            field["compare"],
                        )
                        counts[field["name"]][level] += 1
    else:
        cohort_keys = list(ids_by_cohort)
        weights = [len(ids_by_cohort[c]) for c in cohort_keys]
        # Bounded by the sample size AND by a multiple of it in attempts, so
        # a degenerate cohort set cannot spin forever.
        for _ in range(n_sample * 4):
            if drawn >= n_sample:
                break
            cohort = rng.choices(cohort_keys, weights=weights, k=1)[0]
            ids = ids_by_cohort[cohort]
            left, right = rng.choice(ids), rng.choice(ids)
            if left == right:
                continue
            drawn += 1
            for field in fields:
                level = compare_values(
                    records[left].get(field["column"]), records[right].get(field["column"]), field["compare"]
                )
                counts[field["name"]][level] += 1
    if drawn == 0:
        return {}
    return {
        name: {level: counts[name][level] / drawn for level in LEVELS}
        for name in counts
    }, drawn


# ---------------------------------------------------------------------------
# EM for m (§3.2 Params: link_em_iterations)
# ---------------------------------------------------------------------------
def estimate_m_by_em(patterns: list[dict], fields: list[dict], u: dict, m_prior: dict, iterations: int) -> tuple[dict, float, int]:
    """Two-class EM over the candidate pairs: each pair is a mixture of
    "same person" and "different people", u is held FIXED at its estimate
    (it was measured on random pairs, which the candidate set is not), and
    m is re-estimated from the responsibilities.

    Returns (m, lambda_, iterations_run, relabelled). `too few EM passes
    leaves m at its prior` is the card's own effect column for
    link_em_iterations, so the number of passes actually run is recorded in
    model.json next to the result.

    LABEL IDENTIFICATION. A two-class mixture likelihood is invariant under
    swapping the two classes: call the match class the non-match class and
    the likelihood is identical, so EM can and does converge to the mirror
    solution. Nothing in the objective distinguishes them. The identifying
    constraint has to come from outside the likelihood, and the domain
    supplies it: MATCHES AGREE MORE OFTEN THAN NON-MATCHES. Without it a run
    converges happily to a model in which agreeing on every field scores
    strongly NEGATIVE -- a linkage that is exactly backwards and still
    reports convergence, which §3.3's histogram would render as a clean
    two-humped picture with the humps the wrong way round. The check and the
    fix are at the bottom of this function, and whether it fired is recorded
    in model.json.
    """
    m = {f["name"]: dict(m_prior[f["name"]]) for f in fields}
    lambda_ = 0.1  # P(match) among CANDIDATE pairs; blocking has already
                   # enriched them well above the population rate.
    if not patterns:
        return m, lambda_, 0, False

    # Collapse to DISTINCT comparison vectors with multiplicities. EM depends
    # on the vector a pair produced, never on which pair produced it, and
    # with F fields at three levels there are at most 3**F distinct vectors
    # (243 for the five-field default) no matter how many pairs there are.
    # A badly blocked run yields hundreds of thousands of pairs; iterating
    # over those directly is twenty million operations per pass for exactly
    # the same answer. The estimates here are IDENTICAL, not approximate --
    # the weights are the multiplicities.
    field_names = [f["name"] for f in fields]
    collapsed: dict[tuple, int] = {}
    for pattern in patterns:
        key = tuple(pattern[name] for name in field_names)
        collapsed[key] = collapsed.get(key, 0) + 1
    distinct = sorted(collapsed)
    counts = [collapsed[key] for key in distinct]
    n_pairs = len(patterns)

    responsibilities: list[float] = []
    for _ in range(iterations):
        # E step
        responsibilities = []  # rebound each pass; the last pass's values
                               # are what the label check below re-uses.
        num_m = {f["name"]: {level: 0.0 for level in LEVELS} for f in fields}
        total_g = 0.0
        for key, count in zip(distinct, counts):
            p_match, p_non = lambda_, 1.0 - lambda_
            for name, level in zip(field_names, key):
                p_match *= max(m[name][level], _EPS)
                p_non *= max(u[name][level], _EPS)
            denom = p_match + p_non
            g = p_match / denom if denom > 0 else 0.0
            responsibilities.append(g)
            total_g += g * count
            for name, level in zip(field_names, key):
                num_m[name][level] += g * count
        # M step -- u is NOT re-estimated here. It was measured on random
        # pairs; re-fitting it on the blocked, deliberately enriched
        # candidate set would drag it toward m and quietly collapse the
        # likelihood ratio toward 1.
        if total_g <= 0:
            break
        for field in fields:
            for level in LEVELS:
                m[field["name"]][level] = max(num_m[field["name"]][level] / total_g, _EPS)
        lambda_ = min(max(total_g / n_pairs, _EPS), 1.0 - _EPS)

    # The identifying constraint. Summed over fields, agreement must be
    # evidence FOR a match; if the fitted model says otherwise, EM landed on
    # the mirror solution and the two classes are simply named the wrong way
    # round. Re-deriving m from the complementary responsibilities (1 - g)
    # is exactly the swap, and it is done once, after convergence, rather
    # than by constraining every M step -- constraining the M step would
    # also forbid a genuinely uninformative field from having
    # m(agree) < u(agree), which is a real thing a field can be.
    agreement_evidence = sum(
        math.log2(max(m[f["name"]]["agree"], _EPS) / max(u[f["name"]]["agree"], _EPS)) for f in fields
    )
    relabelled = False
    if agreement_evidence < 0 and responsibilities:
        relabelled = True
        total_g = 0.0
        num_m = {f["name"]: {level: 0.0 for level in LEVELS} for f in fields}
        for key, count, g in zip(distinct, counts, responsibilities):
            complement = 1.0 - g
            total_g += complement * count
            for name, level in zip(field_names, key):
                num_m[name][level] += complement * count
        if total_g > 0:
            for field in fields:
                for level in LEVELS:
                    m[field["name"]][level] = max(num_m[field["name"]][level] / total_g, _EPS)
            lambda_ = min(max(total_g / n_pairs, _EPS), 1.0 - _EPS)

    return m, lambda_, iterations, relabelled


# ---------------------------------------------------------------------------
# §3.2 -- the PairScorer alts seam. Today's only implementation: the
# Fellegi-Sunter log-likelihood ratio above. The Alternatives table's
# "deterministic rule cascade" and "a supervised classifier" would each
# replace only this function's body -- and the classifier is where the card
# says outcome leakage enters, which is why the refusal above sits outside
# this seam rather than inside it.
# ---------------------------------------------------------------------------
def PairScorer_score(pattern: dict, fields: list[dict], m: dict, u: dict) -> tuple[float, list[dict]]:
    weight = 0.0
    per_field = []
    for field in fields:
        name = field["name"]
        level = pattern[name]
        contribution = math.log2(max(m[name][level], _EPS) / max(u[name][level], _EPS))
        weight += contribution
        per_field.append({"field": name, "level": level, "weight": contribution})
    return weight, per_field


def _load_records(con: duckdb.DuckDBPyConnection, tables: list[dict]) -> tuple[dict, dict]:
    """Same record_id construction as bin/link_blocking.py --
    '<cohort_id>#<dataset_id>#<row_number>' -- so a pair proposed there
    resolves here.

    `records` is a FLAT dict keyed by that id, which is exactly why the
    cohort has to be inside it: without it, the last table read wins, and a
    pair from one cohort is scored against another cohort's row. That is not
    a hypothetical -- it is what the two-cohort §10.1 fixture produced the
    first time ADR-004's map-scoped harness linked it, with `birth_date`
    agreeing (the two cohorts share the value set) while `given` and `family`
    disagreed."""
    records: dict[str, dict] = {}
    cohorts: dict[str, list[str]] = {}
    for spec in tables:
        rel = con.read_csv(spec["path"], header=True, all_varchar=True, sample_size=-1)
        columns = list(rel.columns)
        for index, row in enumerate(rel.fetchall(), start=1):
            record_id = f"{spec['cohort_id']}#{spec['dataset_id']}#{index}"
            records[record_id] = dict(zip(columns, row))
            records[record_id]["__cohort_id"] = spec["cohort_id"]
            cohorts.setdefault(spec["cohort_id"], []).append(record_id)
    return records, cohorts


def _write_scores_parquet(con: duckdb.DuckDBPyConnection, rows: list[dict], out_path: str) -> None:
    """scores.parquet carries a NESTED column -- per_field_agreement is a
    LIST of STRUCT(field, level, weight).

    That is deliberate and it is the contract ("per_field_agreement"), not a
    convenience: a match weight with no per-field breakdown cannot be
    audited, and flattening it into one row per (pair, field) would make the
    pair -- the thing §3.3 thresholds -- stop being the unit of the table.
    It is also the first nested type this pipeline writes, which is why
    tools/parquet_roundtrip_probe.py grew a LIST/STRUCT fixture in the same
    change: the container's duckdb and the cluster host's differ by a minor
    version, and nested encodings are where that difference is real.
    """
    con.execute(
        """
        CREATE OR REPLACE TABLE scores (
            cohort_id            VARCHAR,
            left_id              VARCHAR,
            right_id             VARCHAR,
            match_weight         DOUBLE,
            per_field_agreement  STRUCT(field VARCHAR, level VARCHAR, weight DOUBLE)[]
        )
        """
    )
    # Staged flat, then converted ONCE. A per-row INSERT that builds the
    # nested value in SQL costs one round trip per pair, and a badly blocked
    # run has hundreds of thousands of them -- the write, not the scoring,
    # becomes the slow half of the stage. executemany batches the flat rows;
    # the single CREATE TABLE ... SELECT below does the JSON-to-STRUCT[]
    # conversion for all of them in one pass.
    con.execute(
        """
        CREATE OR REPLACE TABLE scores_staging (
            cohort_id           VARCHAR,
            left_id             VARCHAR,
            right_id            VARCHAR,
            match_weight        DOUBLE,
            per_field_json      VARCHAR
        )
        """
    )
    if rows:
        con.executemany(
            "INSERT INTO scores_staging VALUES (?, ?, ?, ?, ?)",
            [
                (
                    row["cohort_id"],
                    row["left_id"],
                    row["right_id"],
                    row["match_weight"],
                    json.dumps(row["per_field_agreement"]),
                )
                for row in rows
            ],
        )
        con.execute(
            """
            INSERT INTO scores
            SELECT cohort_id, left_id, right_id, match_weight,
                   list_transform(from_json(per_field_json::JSON, '["JSON"]'), x ->
                       {'field': json_extract_string(x, '$.field'),
                        'level': json_extract_string(x, '$.level'),
                        'weight': CAST(json_extract_string(x, '$.weight') AS DOUBLE)})
            FROM scores_staging
            """
        )
    con.execute(f"COPY (SELECT * FROM scores ORDER BY cohort_id, left_id, right_id) TO '{out_path}' (FORMAT PARQUET)")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="§3.2 Fellegi-Sunter scoring")
    parser.add_argument("--tables", required=True)
    parser.add_argument("--pairs", required=True)
    parser.add_argument("--comparison-spec", required=True)
    parser.add_argument("--outcome-variables", required=True)
    parser.add_argument("--em-iterations", type=int, required=True)
    parser.add_argument("--u-from-random-pairs", required=True)
    parser.add_argument("--random-pair-n", type=int, required=True)
    parser.add_argument("--random-pair-seed", type=int, default=0)
    parser.add_argument("--out-scores", required=True)
    parser.add_argument("--out-model", required=True)
    args = parser.parse_args(argv)

    tables = json.loads(args.tables)
    outcome_variables = json.loads(args.outcome_variables)
    u_from_random = str(args.u_from_random_pairs).lower() in ("1", "true", "yes")

    fields = load_comparisons(args.comparison_spec)
    _refuse_outcome_fields(fields, outcome_variables)

    con = duckdb.connect()
    records, cohorts = _load_records(con, tables)

    pairs = con.execute(
        f"SELECT DISTINCT cohort_id, left_id, right_id FROM read_parquet('{args.pairs}') "
        "ORDER BY cohort_id, left_id, right_id"
    ).fetchall()

    # One comparison vector per DISTINCT pair. Blocking emits a row per
    # (pair, rule) because recall is the union of the rules; scoring a pair
    # once per rule that found it would weight a pair by how many rules
    # happened to agree, which is a property of the rule file and not of the
    # records.
    patterns = []
    for cohort_id, left_id, right_id in pairs:
        left, right = records.get(left_id, {}), records.get(right_id, {})
        pattern = {"__cohort_id": cohort_id, "__left": left_id, "__right": right_id}
        for field in fields:
            pattern[field["name"]] = compare_values(
                left.get(field["column"]), right.get(field["column"]), field["compare"]
            )
        patterns.append(pattern)

    m_prior = {f["name"]: dict(f["m"]) for f in fields}
    u_prior = {f["name"]: dict(f["u"]) for f in fields}

    u_source = "assumed_prior"
    u = u_prior
    n_random_drawn = 0
    if u_from_random:
        estimated = estimate_u_from_random_pairs(records, cohorts, fields, args.random_pair_n, args.random_pair_seed)
        if estimated:
            u, n_random_drawn = estimated
            u = {name: {level: max(value, _EPS) for level, value in levels.items()} for name, levels in u.items()}
            u_source = "estimated_from_random_pairs"
        else:
            print(
                "WARN: --link_u_from_random_pairs is set but no within-cohort random pair could be drawn; "
                "falling back to the assumed priors in the comparison spec. model.json records this.",
                file=sys.stderr,
            )

    m, lambda_, em_run, relabelled = estimate_m_by_em(patterns, fields, u, m_prior, args.em_iterations)
    m_source = "estimated_by_em" if patterns and args.em_iterations > 0 else "assumed_prior"

    rows = []
    for pattern in patterns:
        weight, per_field = PairScorer_score(pattern, fields, m, u)
        rows.append(
            {
                "cohort_id": pattern["__cohort_id"],
                "left_id": pattern["__left"],
                "right_id": pattern["__right"],
                "match_weight": weight,
                "per_field_agreement": per_field,
            }
        )

    # link/model.json -- the card's own shape, one entry per field with its
    # levels. Extended with the provenance of each number, because "were
    # these estimated or assumed" is the question the card says the
    # linkage's honesty lives in, and a bare m/u pair cannot answer it.
    model = {
        "fields": [
            {
                "field": field["name"],
                "column": field["column"],
                "compare": field["compare"],
                "levels": [
                    {"level": level, "m": m[field["name"]][level], "u": u[field["name"]][level]}
                    for level in LEVELS
                ],
            }
            for field in fields
        ],
        "provenance": {
            "m_source": m_source,
            "u_source": u_source,
            "em_iterations_run": em_run,
            "random_pairs_drawn": n_random_drawn,
            "random_pair_seed": args.random_pair_seed,
            "lambda_match_rate_among_candidates": lambda_,
            "n_candidate_pairs_scored": len(patterns),
            # True when EM converged to the mirror solution and the classes
            # were swapped back (see estimate_m_by_em). Recorded rather than
            # hidden: it is a statement about how well the candidate set
            # separates, and a run where it fires repeatedly is a run whose
            # blocking rules deserve a second look.
            "em_relabelled_to_identify_match_class": relabelled,
        },
    }
    os.makedirs(os.path.dirname(os.path.abspath(args.out_model)) or ".", exist_ok=True)
    with open(args.out_model, "w", encoding="utf-8") as handle:
        json.dump(model, handle, indent=2, sort_keys=True)
        handle.write("\n")

    _write_scores_parquet(con, rows, args.out_scores)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
