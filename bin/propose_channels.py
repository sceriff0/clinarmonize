#!/usr/bin/env python3
"""§4.2 -- score each candidate on six independent evidence channels.

Named alts seam (docs/steps/s4-2.md):
    EvidenceChannel_score(channel_id, ColumnProfile, Candidate) -> (score, Detail)

Today's six default channels (CHANNEL_REGISTRY, in the card's own Contract-
table order): name_similarity, type_agreement, cardinality, value_overlap,
distribution_distance, unit_plausibility. Each is one function of the exact
same shape -- (column_profile, variable, params) -> (score | None, detail) --
registered under its channel id. Adding a seventh channel is one new function
plus one new registry entry; none of the six existing ones are touched.

score is None, never 0, whenever a channel structurally cannot evaluate a
candidate at all (as opposed to evaluating it and finding it implausible,
which IS a real 0.0). Two rulings make this the load-bearing convention here:

  R14 -- value_overlap's reference is the PACK's own domain_values (there is
  no ATHENA release in this repo). A pack variable that declares none simply
  has no value-set evidence to offer; reporting a silent 0 would read as
  active evidence AGAINST the candidate, which is not what "we never
  checked" means.

  R15 -- distribution_distance is circular in phase 0: it is defined against
  CONFIRMED columns for the same concept elsewhere, and §5 confirm (the only
  thing that could ever produce a confirmed column) is not built until a
  later task. This channel is implemented and left enabled -- the Wasserstein
  math is real and will fire the moment a reference exists -- but with no
  reference source wired in yet, every call takes the "no reference" branch,
  every time, in this phase.

The same "no-signal is None, not 0" rule is applied uniformly to every other
channel's own structural gaps (an empty column, a derived variable with no
declared type, a truncated unique-value set, a channel disabled via
--enabled_channels) for the same reason R14 gives: a channel that never
looked must never read like a channel that looked and disapproved.

Fix round 1 (I4 / Ruling R23): R15's redistribution rule -- "excluded from
the weighted total, with its weight redistributed proportionally across the
channels that did score" -- is not left for a caller to re-derive. Every row
also carries a coarse `status` ("scored" / "no-reference" / "could-not-score")
and an `effective_weight`: for a "scored" row, --channel_weights' entry for
that channel, renormalised over only the "scored" rows for that SAME
candidate; 0.0 for every excluded row. A caller (§4.3's ledger) can then
compute a candidate's total as plain `sum(score * effective_weight)` over its
rows -- a pure function of this parquet, never a rule two tasks must
independently remember.

Not here (nogo, per the card): never accepts a proposal (this script only
scores candidates §4.1 already proposed); never tunes channel_weights
against a downstream model's performance -- channel_weights is read only to
compute each row's effective_weight above, never fit against anything.
Never lets a channel read a column the pack marks outcome: true -- structurally
impossible here (candidates.parquet already excludes every outcome-flagged
variable, per §4.1's own nogo), and independently re-checked below
(_assert_never_outcome) precisely because this is the task the brief flags
as most at risk of the invariant leaking back in.
"""
from __future__ import annotations

import argparse
import glob
import json
import math
import os
import re
import sys

import duckdb

# ---------------------------------------------------------------------------
# name_similarity -- Jaro-Winkler over normalised column/variable names.
#
# Distinct failure mode: pure orthography. It does not know what a column
# MEANS, only how its characters compare to a variable's declared name -- so
# it survives a header abbreviation or a typo but not a translation, and it
# carries zero information about the column's actual observed values (the
# Trap's "instance evidence" is exactly what this channel does NOT have).
# ---------------------------------------------------------------------------
_NON_ALNUM_RUN_RE = re.compile(r"[^a-z0-9]+")


def _normalize_name(s: str) -> str:
    return _NON_ALNUM_RUN_RE.sub(" ", s.lower()).strip()


def _jaro(s1: str, s2: str) -> float:
    if s1 == s2:
        return 1.0
    len1, len2 = len(s1), len(s2)
    if len1 == 0 or len2 == 0:
        return 0.0
    match_distance = max(0, max(len1, len2) // 2 - 1)
    s1_matches = [False] * len1
    s2_matches = [False] * len2
    matches = 0
    for i in range(len1):
        start = max(0, i - match_distance)
        end = min(i + match_distance + 1, len2)
        for j in range(start, end):
            if s2_matches[j] or s1[i] != s2[j]:
                continue
            s1_matches[i] = True
            s2_matches[j] = True
            matches += 1
            break
    if matches == 0:
        return 0.0
    k = 0
    transpositions = 0
    for i in range(len1):
        if not s1_matches[i]:
            continue
        while not s2_matches[k]:
            k += 1
        if s1[i] != s2[k]:
            transpositions += 1
        k += 1
    transpositions //= 2
    return (matches / len1 + matches / len2 + (matches - transpositions) / matches) / 3.0


def _jaro_winkler(s1: str, s2: str, p: float = 0.1, max_prefix: int = 4) -> float:
    # p=0.1 and a 4-char prefix cap are the Jaro-Winkler DEFINITION's own
    # constants (Winkler 1990), not a domain magic number this pipeline
    # chose -- changing them would stop being Jaro-Winkler.
    jaro = _jaro(s1, s2)
    prefix = 0
    for a, b in zip(s1, s2):
        if a != b:
            break
        prefix += 1
        if prefix == max_prefix:
            break
    return jaro + prefix * p * (1 - jaro)


def score_name_similarity(profile: dict, variable: dict, params: dict) -> tuple[float | None, dict]:
    del params
    normalized_column = _normalize_name(profile["column"])
    normalized_variable = _normalize_name(variable["name"])
    if not normalized_column or not normalized_variable:
        return None, {"status": "no-signal", "reason": "one side normalises to the empty string"}
    score = _jaro_winkler(normalized_column, normalized_variable)
    return score, {
        "status": "scored",
        "normalized_column": normalized_column,
        "normalized_variable": normalized_variable,
        "jaro_winkler": score,
    }


# ---------------------------------------------------------------------------
# type_agreement -- the profiler's own inferred_type vs. the variable's
# declared statistical type.
#
# Distinct failure mode: an ID column read as a measurement. This channel
# knows nothing about names or values, only the coarse SHAPE of the data
# (§2.1's inferred_type: int/float/bool/date/str/empty) -- a column can pass
# every other channel and still be the wrong kind of thing.
# ---------------------------------------------------------------------------
_TYPE_EXPECTATIONS = {
    "continuous": {"int", "float"},
    "nominal": {"str", "int", "bool"},
    "ordinal": {"str", "int", "bool"},
    "boolean": {"bool", "int", "str"},
}


def score_type_agreement(profile: dict, variable: dict, params: dict) -> tuple[float | None, dict]:
    del params
    inferred_type = profile.get("inferred_type")
    if inferred_type == "empty":
        return None, {"status": "no-signal", "reason": "column has no non-missing values to type-check"}
    variable_type = variable.get("type")
    if not variable_type:
        return None, {"status": "no-signal", "reason": "variable declares no statistical type (derived, no type)"}
    expected = _TYPE_EXPECTATIONS.get(variable_type)
    if expected is None:
        return None, {"status": "no-signal", "reason": f"unrecognised variable type '{variable_type}'"}
    score = 1.0 if inferred_type in expected else 0.0
    return score, {
        "status": "scored",
        "inferred_type": inferred_type,
        "variable_type": variable_type,
        "expected_inferred_types": sorted(expected),
    }


# ---------------------------------------------------------------------------
# cardinality -- unique-count ratio vs. the expected domain size.
#
# Distinct failure mode: a categorical stored as continuous. This channel
# never looks at the VALUES themselves (that is value_overlap's job) or the
# column's name -- only how many distinct values it holds, which survives a
# full rename and even a recoding, so long as the number of categories does
# not change. That instance-level independence from naming/translation is
# exactly the Trap's "cardinality ... is instance evidence".
# ---------------------------------------------------------------------------
def score_cardinality(profile: dict, variable: dict, params: dict) -> tuple[float | None, dict]:
    del params
    n_rows = profile.get("n_rows") or 0
    n_unique = profile.get("n_unique")
    if not n_rows or n_unique is None:
        return None, {"status": "no-signal", "reason": "no row count to compute a cardinality ratio against"}

    domain_values = variable.get("domain_values")
    if domain_values:
        expected_cardinality = len(domain_values)
        denom = max(n_unique, expected_cardinality)
        score = 1.0 - (abs(n_unique - expected_cardinality) / denom) if denom else None
        return score, {
            "status": "scored",
            "basis": "domain_values",
            "n_unique": n_unique,
            "expected_cardinality": expected_cardinality,
            "n_rows": n_rows,
        }

    if variable.get("type") == "continuous":
        unique_ratio = n_unique / n_rows
        score = min(1.0, unique_ratio)
        return score, {
            "status": "scored",
            "basis": "continuous-high-uniqueness",
            "n_unique": n_unique,
            "n_rows": n_rows,
            "unique_ratio": unique_ratio,
        }

    return None, {
        "status": "no-signal",
        "reason": "no domain_values and the variable is not continuous; no expected cardinality to compare against",
    }


# ---------------------------------------------------------------------------
# value_overlap -- Jaccard of the column's observed unique-value set vs. the
# variable's domain_values (Ruling R14: the pack's domain_values stands in
# for "the concept's ATHENA value set" -- there is no ATHENA release here).
#
# Distinct failure mode: a correctly-named column holding a different coding
# system (e.g. ICD vs. a local code list) -- name, type and even cardinality
# can all agree while the actual values never overlap. Instance evidence,
# same as cardinality, but over the VALUES rather than their count.
# ---------------------------------------------------------------------------
def score_value_overlap(profile: dict, variable: dict, params: dict) -> tuple[float | None, dict]:
    del params
    domain_values = variable.get("domain_values")
    if not domain_values:
        return None, {
            "status": "no-signal",
            "reason": "variable declares no domain_values (R14: the pack is the only value-set source in this phase)",
        }
    if profile.get("unique_truncated"):
        return None, {
            "status": "no-signal",
            "reason": "column's unique-value set was truncated at max_unique_listed; no full set to compare",
        }
    unique_values = profile.get("unique_values")
    if not unique_values:
        return None, {"status": "no-signal", "reason": "column carries no observed values"}

    normalized_domain = {str(v).strip().lower() for v in domain_values}
    normalized_unique = {str(v).strip().lower() for v in unique_values}
    union = normalized_domain | normalized_unique
    if not union:
        return None, {"status": "no-signal", "reason": "both value sets are empty after normalisation"}
    intersection = normalized_domain & normalized_unique
    score = len(intersection) / len(union)
    return score, {
        "status": "scored",
        "jaccard": score,
        "n_domain_values": len(normalized_domain),
        "n_unique_values": len(normalized_unique),
        "n_intersection": len(intersection),
    }


# ---------------------------------------------------------------------------
# distribution_distance -- Wasserstein distance vs. CONFIRMED columns for the
# same concept elsewhere.
#
# Distinct failure mode (once a reference exists): a scale flip that leaves
# name, type and cardinality all intact -- e.g. the same lab value reported
# in different units without a header hint, or a column whose values were
# silently rescaled upstream. §5 confirm is what would ever produce a
# CONFIRMED column, and confirm is not built until a later task (Ruling
# R15), so confirmed_by_concept is always empty here and this channel always
# takes the "no reference" branch -- implemented for real (the Wasserstein
# approximation below is not a stub), never reachable in phase 0.
# ---------------------------------------------------------------------------
def _wasserstein_approx_from_quantiles(quantiles_a: list[float], quantiles_b: list[float]) -> float | None:
    # A 1-Wasserstein APPROXIMATION over the same seven quantile probes every
    # ColumnProfile already carries (p0, p1, p25, p50, p75, p99, p100 --
    # §2.1's compute_quantiles), rather than the raw samples this pipeline
    # never stores: the mean absolute gap between matched quantile points.
    if not quantiles_a or not quantiles_b or len(quantiles_a) != len(quantiles_b):
        return None
    return sum(abs(a - b) for a, b in zip(quantiles_a, quantiles_b)) / len(quantiles_a)


def score_distribution_distance(profile: dict, variable: dict, params: dict) -> tuple[float | None, dict]:
    confirmed_by_concept = params.get("confirmed_by_concept") or {}
    concept_id = variable.get("concept_id")
    reference_quantiles = confirmed_by_concept.get(concept_id) if concept_id is not None else None
    if not reference_quantiles:
        return None, {
            "status": "no-reference",
            "reason": (
                "no CONFIRMED columns exist for this concept yet "
                "(§5 confirm is not built in phase 0; Ruling R15)"
            ),
        }

    distance = _wasserstein_approx_from_quantiles(profile.get("quantiles") or [], reference_quantiles)
    if distance is None:
        return None, {"status": "no-signal", "reason": "quantile shapes did not align with the reference"}
    # Smaller distance -> more plausible. A generic decay with no assumed
    # domain scale (no hard-coded "typical" magnitude anywhere -- §10.3).
    score = 1.0 / (1.0 + distance)
    return score, {"status": "scored", "wasserstein_approx": distance, "reference_n": len(reference_quantiles)}


# ---------------------------------------------------------------------------
# unit_plausibility -- does the column's own §2.2 candidate unit fit the
# variable's declared unit directly, or only after one of
# --unit_factor_candidates' conversions?
#
# Distinct failure mode: months recorded where days are expected -- and
# nothing else catches it. Name, type, cardinality and value_overlap are all
# blind to units entirely; this channel is the only one that ever looks at
# candidate_units at all.
#
# A small, generic (not disease-specific) UCUM family table: calendar time
# units keyed to days (30.4375 d/mo, 365.25 d/a -- the card's own listed
# factors), and SI-prefix mass units keyed to grams (the card's 0.001/1000).
# Neither table entry names a disease, a gene or a cohort -- they are the
# UCUM grammar the card's own Docs section points at.
# ---------------------------------------------------------------------------
_UNIT_FAMILY_BASE = {
    "d": ("time", 1.0),
    "wk": ("time", 7.0),
    "mo": ("time", 30.4375),
    "a": ("time", 365.25),
    "mg": ("mass", 0.001),
    "g": ("mass", 1.0),
    "kg": ("mass", 1000.0),
}

# A fits-only-after-conversion candidate is real evidence but strictly
# weaker than a direct fit -- an implementation detail of this default alt's
# scoring curve, not a card Params row (the card's own params are the factor
# LIST tried, not how much a converted fit is discounted).
_CONVERSION_DISCOUNT = 0.5


def score_unit_plausibility(profile: dict, variable: dict, params: dict) -> tuple[float | None, dict]:
    variable_unit = variable.get("unit")
    if not variable_unit:
        return None, {"status": "no-signal", "reason": "variable declares no unit"}
    candidate_units = profile.get("candidate_units") or []
    if not candidate_units:
        return None, {"status": "no-signal", "reason": "column carries no candidate units (§2.2 found none)"}

    factor_candidates = params.get("unit_factor_candidates") or [1.0]

    best_score = None
    best_extra = None
    for cu in candidate_units:
        ucum = cu.get("ucum")
        confidence = cu.get("confidence", 0.0)
        candidate_score = None
        reason = None
        if ucum == variable_unit:
            candidate_score = confidence
            reason = "direct match: column's own candidate unit equals the variable's declared unit"
        else:
            family_a = _UNIT_FAMILY_BASE.get(ucum)
            family_b = _UNIT_FAMILY_BASE.get(variable_unit)
            if family_a and family_b and family_a[0] == family_b[0]:
                implied_factor = family_a[1] / family_b[1]
                if implied_factor and any(
                    math.isclose(implied_factor, f, rel_tol=0.01) or (f and math.isclose(1.0 / implied_factor, f, rel_tol=0.01))
                    for f in factor_candidates
                    if f
                ):
                    candidate_score = confidence * _CONVERSION_DISCOUNT
                    reason = f"fits only after a {implied_factor:.4f}x conversion ({ucum} -> {variable_unit})"
        if candidate_score is None:
            continue
        if best_score is None or candidate_score > best_score:
            best_score = candidate_score
            best_extra = {"matched_ucum": ucum, "matched_confidence": confidence, "reason": reason, "source": cu.get("source")}

    if best_score is None:
        return 0.0, {
            "status": "scored",
            "reason": f"no candidate unit matches or converts to '{variable_unit}'",
            "expected_unit": variable_unit,
            "candidate_units_considered": candidate_units,
        }

    detail = {"status": "scored", "expected_unit": variable_unit, "candidate_units_considered": candidate_units}
    detail.update(best_extra)
    return best_score, detail


# ---------------------------------------------------------------------------
# §4.2 -- EvidenceChannel alts seam. CHANNEL_REGISTRY is the ENTIRE swap
# surface: a seventh channel is one more function plus one more entry here,
# nothing else in this file (or its caller) changes. Order matches the
# card's own Contract table, which is also the order rows are emitted in and
# bars are drawn in the confirmation plot (R16) -- deterministic either way,
# but keeping them in the card's order is one less thing to cross-reference.
# ---------------------------------------------------------------------------
CHANNEL_REGISTRY = {
    "name_similarity": score_name_similarity,
    "type_agreement": score_type_agreement,
    "cardinality": score_cardinality,
    "value_overlap": score_value_overlap,
    "distribution_distance": score_distribution_distance,
    "unit_plausibility": score_unit_plausibility,
}

# Fix round 1 (I2): the ONE machine-readable home for the card's short
# --channel_weights keys (name/type/card/value/dist/unit, verbatim from the
# Params table) mapped to CHANNEL_REGISTRY's long channel ids. Nothing else
# in this file, its caller, or tests/propose_channels.nf.test restates this
# mapping -- effective_weight below is computed from it once, here, and
# every other consumer (the test, eventually §4.3) reads effective_weight
# off evidence.parquet rather than re-deriving the mapping itself.
CHANNEL_WEIGHT_KEY = {
    "name_similarity": "name",
    "type_agreement": "type",
    "cardinality": "card",
    "value_overlap": "value",
    "distribution_distance": "dist",
    "unit_plausibility": "unit",
}


def EvidenceChannel_score(channel_id: str, column_profile: dict, variable: dict, params: dict) -> tuple[float | None, dict]:
    return CHANNEL_REGISTRY[channel_id](column_profile, variable, params)


# ---------------------------------------------------------------------------
# R16 -- a deterministic, hand-rolled SVG confirmation plot: one bar per
# channel, stdlib only (no matplotlib -- not in §0.8's toolchain pins,
# and Global Constraint 8 forbids introducing one silently). No timestamp,
# hostname, absolute path or run id anywhere in the markup -- every byte is a
# pure function of candidate_key and the six (score, detail) pairs, so two
# runs over the same inputs produce byte-identical SVGs.
# ---------------------------------------------------------------------------
_UNSAFE_FILENAME_RE = re.compile(r"[^A-Za-z0-9_.-]+")


def _sanitize_filename(candidate_key: str) -> str:
    return _UNSAFE_FILENAME_RE.sub("_", candidate_key)


def _svg_escape(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def render_confirmation_plot_svg(candidate_key: str, channel_scores: list[tuple[str, float | None, dict]]) -> str:
    width, height = 640, 60 + 40 * len(channel_scores)
    margin_left, margin_right, margin_top = 200, 20, 40
    plot_w = width - margin_left - margin_right
    plot_h = height - margin_top - 20
    n = len(channel_scores)
    bar_h = plot_h / n if n else plot_h

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        f'<rect x="0" y="0" width="{width}" height="{height}" fill="#ffffff"/>',
        f'<text x="{width / 2:.1f}" y="20" font-family="sans-serif" font-size="14" text-anchor="middle" fill="#111111">{_svg_escape(candidate_key)}</text>',
        f'<line x1="{margin_left}" y1="{margin_top}" x2="{margin_left}" y2="{margin_top + plot_h}" stroke="#333333" stroke-width="1"/>',
        f'<line x1="{margin_left}" y1="{margin_top + plot_h}" x2="{margin_left + plot_w}" y2="{margin_top + plot_h}" stroke="#333333" stroke-width="1"/>',
    ]

    for i, (channel_id, score, _detail) in enumerate(channel_scores):
        y = margin_top + i * bar_h
        gap = bar_h * 0.25
        bar_y = y + gap / 2
        bar_full_h = bar_h - gap
        parts.append(
            f'<text x="4" y="{y + bar_h / 2 + 4:.1f}" font-family="sans-serif" font-size="12" fill="#222222">{_svg_escape(channel_id)}</text>'
        )
        if score is None:
            parts.append(
                f'<rect x="{margin_left}" y="{bar_y:.1f}" width="{plot_w}" height="{bar_full_h:.1f}" '
                f'fill="none" stroke="#999999" stroke-width="1" stroke-dasharray="4,3"/>'
            )
            parts.append(
                f'<text x="{margin_left + 6}" y="{bar_y + bar_full_h / 2 + 4:.1f}" font-family="sans-serif" font-size="12" fill="#999999">not scored</text>'
            )
        else:
            clamped = max(0.0, min(1.0, score))
            w = plot_w * clamped
            color = "#2a6f97" if clamped >= 0.5 else "#b23a48"
            parts.append(f'<rect x="{margin_left}" y="{bar_y:.1f}" width="{w:.1f}" height="{bar_full_h:.1f}" fill="{color}"/>')
            parts.append(
                f'<text x="{margin_left + w + 6:.1f}" y="{bar_y + bar_full_h / 2 + 4:.1f}" font-family="sans-serif" font-size="12" fill="#222222">{score:.3f}</text>'
            )

    parts.append("</svg>")
    return "".join(parts)


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


# Fix round 1 (I4 / Ruling R23): the 4-value `status` a channel's own detail
# dict carries ("scored", "no-reference", "no-signal", "disabled") collapses
# to the 3-value column the ruling names -- "no-signal" and "disabled" are
# both species of "we never evaluated this candidate at all", which is the
# distinction that matters for weight redistribution (both get 0.0 below);
# "no-reference" stays its own value because it is R15's specific case.
def _coarse_status(detail_status: str) -> str:
    if detail_status == "scored":
        return "scored"
    if detail_status == "no-reference":
        return "no-reference"
    return "could-not-score"


# Fix round 1 (I4 / Ruling R23): R15's redistribution, computed ONCE per
# candidate so a caller never has to. channel_weights' short keys are
# resolved to channel ids via CHANNEL_WEIGHT_KEY (I2); a "scored" channel's
# effective_weight is its raw weight divided by the sum of raw weights over
# every "scored" channel for this SAME candidate (so the six effective
# weights for one candidate sum to 1.0, or are all 0.0 in the degenerate
# case where nothing scored at all); every "no-reference" or
# "could-not-score" row gets 0.0 -- excluded, never a silent share of the
# total.
def _effective_weights(channel_statuses: dict[str, str], channel_weights: dict[str, float]) -> dict[str, float]:
    raw_weight = {
        channel_id: channel_weights[CHANNEL_WEIGHT_KEY[channel_id]] for channel_id in CHANNEL_REGISTRY
    }
    scored_total = sum(raw_weight[c] for c, status in channel_statuses.items() if status == "scored")
    effective: dict[str, float] = {}
    for channel_id, status in channel_statuses.items():
        if status == "scored" and scored_total > 0:
            effective[channel_id] = raw_weight[channel_id] / scored_total
        else:
            effective[channel_id] = 0.0
    return effective


def _write_evidence_parquet(rows: list[dict], out_path: str) -> None:
    con = duckdb.connect()
    con.execute(
        """
        CREATE TABLE evidence (
            candidate_key    VARCHAR,
            channel          VARCHAR,
            score            DOUBLE,
            detail           VARCHAR,
            status           VARCHAR,
            effective_weight DOUBLE
        )
        """
    )
    if rows:
        con.executemany(
            "INSERT INTO evidence VALUES (?, ?, ?, ?, ?, ?)",
            [
                (r["candidate_key"], r["channel"], r["score"], r["detail"], r["status"], r["effective_weight"])
                for r in rows
            ],
        )
    con.execute(f"COPY evidence TO '{out_path}' (FORMAT PARQUET)")


def _assert_never_outcome(variable_name: str, outcome_names: set[str], cohort_id: str, dataset_id: str, column: str) -> None:
    # Defence in depth (§0 the invariant): candidates.parquet already
    # excludes every outcome-flagged variable (§4.1's own nogo), so this
    # should never fire. It is re-checked here, independently, because this
    # task is the one the brief names as most at risk of the invariant
    # leaking back in -- a bug two files away must not go unnoticed here.
    if variable_name in outcome_names:
        raise RuntimeError(
            f"candidates.parquet names outcome-flagged variable '{variable_name}' as a candidate "
            f"for {cohort_id}.{dataset_id}.{column} -- §0 the invariant forbids this."
        )


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--candidates", required=True, help="Path to one replicate's candidates.parquet (§4.1 output).")
    ap.add_argument("--profile-glob", required=True, help="Glob over that SAME replicate's collected *.json column profiles.")
    ap.add_argument("--pack-variables", required=True, help="JSON list of the pack's variable dicts.")
    ap.add_argument(
        "--channel-params",
        required=True,
        help='JSON object: {"channel_weights": {...}, "enabled_channels": [...], '
        '"unit_factor_candidates": [...], "emit_confirmation_plots": bool}',
    )
    ap.add_argument("--out-evidence", required=True)
    ap.add_argument("--out-plots-dir", required=True)
    args = ap.parse_args(argv)

    all_variables = json.loads(args.pack_variables)
    variables_by_name = {v["name"]: v for v in all_variables}
    outcome_names = {v["name"] for v in all_variables if v.get("outcome")}

    channel_params = json.loads(args.channel_params)
    enabled_channels = set(channel_params["enabled_channels"])
    unit_factor_candidates = channel_params["unit_factor_candidates"]
    emit_confirmation_plots = channel_params["emit_confirmation_plots"]
    # Fix round 1 (I1 / I4): channel_weights IS read now -- to compute each
    # row's effective_weight (R15's redistribution, precomputed once here
    # rather than left for every future consumer to re-derive; see
    # _effective_weights above). Still never fit against anything (§4.2's
    # own nogo); still no total is computed or ranked here -- only the
    # per-row weight a total would use.
    channel_weights = channel_params["channel_weights"]
    scorer_params = {
        "unit_factor_candidates": unit_factor_candidates,
        # §5 confirm does not exist in this phase (Ruling R15): there is no
        # source of confirmed reference columns to populate this from, ever,
        # in phase 0. Always empty, on purpose.
        "confirmed_by_concept": {},
    }

    profiles_by_key: dict[tuple[str, str, str], dict] = {}
    for path in sorted(glob.glob(args.profile_glob)):
        cohort_id, dataset_id = _parse_cohort_dataset(path)
        with open(path, encoding="utf-8") as fh:
            profiles = json.load(fh)
        for p in profiles:
            profiles_by_key[(cohort_id, dataset_id, p["column"])] = p

    con = duckdb.connect()
    candidate_rows = con.execute(
        "SELECT DISTINCT cohort_id, dataset_id, \"column\", variable "
        f"FROM read_parquet('{args.candidates}') "
        "WHERE variable IS NOT NULL "
        'ORDER BY cohort_id, dataset_id, "column", variable'
    ).fetchall()

    os.makedirs(args.out_plots_dir, exist_ok=True)

    evidence_rows: list[dict] = []
    for cohort_id, dataset_id, column, variable_name in candidate_rows:
        _assert_never_outcome(variable_name, outcome_names, cohort_id, dataset_id, column)

        variable = variables_by_name.get(variable_name)
        if variable is None:
            raise RuntimeError(f"candidates.parquet names variable '{variable_name}', which is not in the pack.")
        profile = profiles_by_key.get((cohort_id, dataset_id, column))
        if profile is None:
            raise RuntimeError(f"no profile found for {cohort_id}.{dataset_id}.{column}.")

        candidate_key = f"{cohort_id}::{dataset_id}::{column}::{variable_name}"

        channel_scores: list[tuple[str, float | None, dict]] = []
        channel_details: dict[str, dict] = {}
        channel_statuses: dict[str, str] = {}
        for channel_id in CHANNEL_REGISTRY:
            if channel_id not in enabled_channels:
                # §4.2 Params: "disabling one is recorded in the ledger, not
                # silent." Every candidate still gets a row for a disabled
                # channel -- score null, detail says why -- rather than the
                # row simply never existing.
                score, detail = None, {"status": "disabled", "reason": "excluded via --enabled_channels"}
            else:
                score, detail = EvidenceChannel_score(channel_id, profile, variable, scorer_params)
            channel_scores.append((channel_id, score, detail))
            channel_details[channel_id] = detail
            channel_statuses[channel_id] = _coarse_status(detail["status"])

        # Fix round 1 (I4 / Ruling R23): R15's redistribution, computed once
        # per candidate over its six channel statuses just gathered above.
        effective_weights = _effective_weights(channel_statuses, channel_weights)
        for channel_id, score, detail in channel_scores:
            evidence_rows.append(
                {
                    "candidate_key": candidate_key,
                    "channel": channel_id,
                    "score": score,
                    "detail": json.dumps(detail, sort_keys=True),
                    "status": channel_statuses[channel_id],
                    "effective_weight": effective_weights[channel_id],
                }
            )

        if emit_confirmation_plots:
            svg = render_confirmation_plot_svg(candidate_key, channel_scores)
            svg_path = os.path.join(args.out_plots_dir, f"{_sanitize_filename(candidate_key)}.svg")
            with open(svg_path, "w", encoding="utf-8") as fh:
                fh.write(svg)

    _write_evidence_parquet(evidence_rows, args.out_evidence)
    return 0


if __name__ == "__main__":
    sys.exit(main())
