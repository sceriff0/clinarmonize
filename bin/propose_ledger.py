#!/usr/bin/env python3
"""§4.3 -- write the ranked, deterministic proposal ledger.

Named alts seam (docs/steps/s4-3.md):
    LedgerWriter -- write([Proposal]) -> bytes

IN   propose/candidates.parquet (§4.1) + propose/evidence.parquet (§4.2), the
     SAME replicate's outputs
OUT  ledger.proposed.yaml -- ordered, stable, hashable
SIDE none; identical inputs MUST yield an identical sha256 (this is the only
     thing §10.1's harness measures)

A candidate's combined score is sum(score * effective_weight) over its
evidence.parquet rows where score IS NOT NULL. That arithmetic is deliberately
this simple: R15's redistribution ("excluded from the total, weight
redistributed proportionally across the channels that DID score") is already
resolved into effective_weight by bin/propose_channels.py (Fix round 1, I4 /
Ruling R23) -- 0.0 for every excluded row, so a null score can never be
scored as 0 or drag the total down by omission. Nothing here re-derives
--channel_weights or re-guesses the short-key mapping: CHANNEL_WEIGHT_KEY is
imported from bin/propose_channels.py, the one place that mapping is written
(Fix round 1, I2 there) -- a one-entry drift between two independently
hand-copied mappings would mis-weight every proposal silently.

excluded_candidates is carried straight from candidates.parquet's own column
(§4.1's recall ceiling, already computed and recorded there by
_apply_recall_ceiling) -- never recomputed here, and never conflated with
--ledger_top_k's OWN truncation. The card's Contract example carries exactly
one such field, and it is the recall ceiling's ("the recall ceiling, made
visible"); proposals beyond --ledger_top_k are simply not written into the
`proposals` list, with no separate count of their own.

§0 the invariant, structurally: `variable` is read straight from
candidates.parquet, which already excludes every outcome-flagged pack
variable (§4.1's own nogo, independently re-checked in
bin/propose_channels.py's _assert_never_outcome). This script never even
receives the pack or its outcome flag as an input, so nothing here CAN name
an outcome-flagged variable.

The Trap (this card's own): non-determinism from map iteration order or
unrounded floats, silently disabling the very test this file exists to pass.
Guarded here by: (a) every SQL query below carries an explicit ORDER BY --
DuckDB's row order is not otherwise guaranteed identical run to run; (b)
every score/total is rounded to --ledger_float_precision BEFORE
serialisation, so float-summation-order noise (~1e-16, from summing the same
numbers in a different order) cannot survive into the written bytes; (c) the
top-level list and each proposal's key order are fixed by construction
(PyYAML's sort_keys=False over an explicitly-built, explicitly-ordered
dict/list), never a bare Python dict's insertion order alone and never the
OS's directory-listing order.

Not here (nogo, per the card): no timestamp, hostname, absolute path or run
id anywhere in the output -- there is no call to datetime/time, no hostname
lookup, and no absolute path assembled into any written value (only the
already-relative `propose/confirmation_plots/...svg` path). Never promotes a
proposal to confirmed -- that is §5 and requires a human; this script only
ever WRITES a ranked list, it never reads one back or acts on it.
"""
from __future__ import annotations

import argparse
import json
import sys

import duckdb
import yaml

from propose_channels import CHANNEL_WEIGHT_KEY, _sanitize_filename

# The card's own Contract-table order (also CHANNEL_REGISTRY's insertion
# order in bin/propose_channels.py: name_similarity, type_agreement,
# cardinality, value_overlap, distribution_distance, unit_plausibility).
# Iterating CHANNEL_WEIGHT_KEY in this fixed order is what gives the
# `evidence` map a deterministic key order, independent of whatever order
# DuckDB happens to return evidence.parquet's rows in.
_CHANNEL_ORDER = list(CHANNEL_WEIGHT_KEY.keys())


def _round(value: float | None, precision: int) -> float | None:
    return None if value is None else round(value, precision)


def build_ledger_entries(
    candidates_parquet: str,
    evidence_parquet: str,
    ledger_top_k: int,
    ledger_float_precision: int,
) -> list[dict]:
    con = duckdb.connect()

    # DISTINCT (cohort_id, dataset_id, column, variable): exactly the join
    # key evidence.parquet's own builder computes (bin/propose_channels.py),
    # so re-deriving candidate_key the SAME way -- never parsing it back
    # apart, since a raw column header can itself contain '::' -- is
    # guaranteed to match evidence.parquet's own candidate_key values.
    candidate_rows = con.execute(
        'SELECT DISTINCT cohort_id, dataset_id, "column", variable, concept_id, excluded_candidates '
        f"FROM read_parquet('{candidates_parquet}') "
        "WHERE variable IS NOT NULL "
        'ORDER BY cohort_id, dataset_id, "column", variable'
    ).fetchall()

    evidence_by_key: dict[str, dict[str, tuple[float | None, float]]] = {}
    for candidate_key, channel, score, effective_weight in con.execute(
        "SELECT candidate_key, channel, score, effective_weight "
        f"FROM read_parquet('{evidence_parquet}') "
        "ORDER BY candidate_key, channel"
    ).fetchall():
        evidence_by_key.setdefault(candidate_key, {})[channel] = (score, effective_weight)

    by_column: dict[tuple[str, str, str], list[dict]] = {}
    for cohort_id, dataset_id, column, variable, concept_id, excluded_candidates in candidate_rows:
        candidate_key = f"{cohort_id}::{dataset_id}::{column}::{variable}"
        channel_rows = evidence_by_key.get(candidate_key)
        if not channel_rows or set(channel_rows) != set(CHANNEL_WEIGHT_KEY):
            raise RuntimeError(
                f"evidence.parquet carries no complete six-channel row set for candidate "
                f"'{candidate_key}'. candidates.parquet and evidence.parquet must be the "
                f"SAME replicate's outputs."
            )

        total = sum(score * weight for score, weight in channel_rows.values() if score is not None)
        evidence_map = {
            CHANNEL_WEIGHT_KEY[channel_id]: _round(channel_rows[channel_id][0], ledger_float_precision)
            for channel_id in _CHANNEL_ORDER
        }

        proposal = {
            "variable": variable,
            "concept_id": concept_id,
            "total": _round(total, ledger_float_precision),
            "evidence": evidence_map,
            "confirmation_plot": f"propose/confirmation_plots/{_sanitize_filename(candidate_key)}.svg",
            "excluded_candidates": excluded_candidates,
        }
        by_column.setdefault((cohort_id, dataset_id, column), []).append(proposal)

    ledger: list[dict] = []
    for cohort_id, dataset_id, column in sorted(by_column):
        proposals = by_column[(cohort_id, dataset_id, column)]
        # -total, then variable name: a deterministic tie-break so two
        # candidates that round to the identical total never depend on
        # DuckDB's row order to decide which one appears first (the Trap).
        proposals.sort(key=lambda p: (-(p["total"] if p["total"] is not None else float("-inf")), p["variable"]))
        ledger.append(
            {
                "cohort_id": cohort_id,
                "dataset_id": dataset_id,
                "column": column,
                # Params (docs/steps/s4-3.md): "proposals written per
                # column; the rest are counted, not listed" -- counted via
                # excluded_candidates above (the recall ceiling's own
                # count), never via a second, --ledger_top_k-derived field
                # the card's Contract does not name.
                "proposals": proposals[:ledger_top_k],
            }
        )
    return ledger


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--candidates", required=True, help="Path to one replicate's candidates.parquet (§4.1 output).")
    ap.add_argument("--evidence", required=True, help="Path to that SAME replicate's evidence.parquet (§4.2 output).")
    ap.add_argument(
        "--ledger-params",
        required=True,
        help='JSON object: {"ledger_top_k": int, "ledger_float_precision": int}',
    )
    ap.add_argument("--out-ledger", required=True)
    args = ap.parse_args(argv)

    ledger_params = json.loads(args.ledger_params)
    ledger_top_k = ledger_params["ledger_top_k"]
    ledger_float_precision = ledger_params["ledger_float_precision"]

    ledger = build_ledger_entries(args.candidates, args.evidence, ledger_top_k, ledger_float_precision)

    # sort_keys=False: every dict's key order is fixed by construction above
    # (the card's own Contract order), not re-sorted alphabetically by
    # PyYAML. No timestamp/hostname/path/run id is ever assembled into
    # `ledger`, so none can leak into these bytes either.
    with open(args.out_ledger, "w", encoding="utf-8") as fh:
        yaml.safe_dump(ledger, fh, sort_keys=False, default_flow_style=False, allow_unicode=True)

    return 0


if __name__ == "__main__":
    sys.exit(main())
