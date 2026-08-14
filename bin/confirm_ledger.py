#!/usr/bin/env python3
"""§5.1 -- accept a confirmed ledger and refuse to guess when it is absent.

Named alts seam (docs/steps/s5-1.md):
    ConfirmationSource -- confirm([Proposal]) -> [Decision]

Today's only implementation reads a human-authored ledger.confirmed.yaml
(the Contract's own shape: proposed + a decision + who + why) rather than
generating one. This script is the "read it and refuse to guess" half of
that seam; the *absence* of a confirmed ledger at all is handled entirely in
Groovy (workflows/harmonize.nf), one level up -- that is a run-shape decision
("do not even start this process"), not a per-row parsing one, and it never
becomes a magic auto-accept boundary because the CLI never offers a flag
that could produce a Decision by itself (see workflows/harmonize.nf's own
comment on the point).

IN   --confirmed  ledger.confirmed.yaml (a plain YAML list, one row per
     confirmed column -- decision: accept | reject | remap | defer)
     --proposed   ledger.proposed.yaml (§4.3's output, for the staleness
     check ONLY -- its per-column content is never read back here)
OUT  --out-decisions decisions.json -- every confirmed row, validated and
     normalised, sorted by (cohort_id, dataset_id, column); the full audit
     trail (including reject/defer rows), not pre-filtered to "accept" --
     bin/compile_rules.py (§5.2) is the one place that decides which
     decisions become rules.
SIDE exits non-zero, with the diff, when the confirmed ledger is stale;
     exits non-zero, at parse time, on an accept with no rationale
     (--require_rationale) or a row that names a column
     ledger.proposed.yaml does not carry (the Trap, below).

The Trap (this card's own): matching confirmed rows to proposals by row
index. A pack bump reorders ledger.proposed.yaml's entries, and index-based
pairing silently re-points every decision at a different column. Guarded
here structurally, not by convention: this script NEVER pairs the two files
by position. Each confirmed row already carries its own
(cohort_id, dataset_id, column) key; ledger.proposed.yaml is read only to
build a KEYED lookup set, checked by membership, never zipped or indexed
against the confirmed list.

Not here (nogo, per the card): never auto-confirms anything (there is no
code path in this file that can manufacture a Decision without a
human-authored row to read); never lets --allow_stale_ledger paper over a
row naming a column absent from ledger.proposed.yaml entirely -- that is a
different failure (a typo or a genuinely wrong key) from "the pack bumped
and the ranking shifted", and the escape hatch is scoped to the latter only;
never edits ledger.proposed.yaml (opened read-only, exactly once, to hash
and index it).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys

import yaml

_VALID_DECISIONS = {"accept", "reject", "remap", "defer"}
_ACTIONABLE_DECISIONS = {"accept", "remap"}


def _sha256_of_file(path: str) -> str:
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


def _load_yaml_list(path: str, what: str) -> list[dict]:
    with open(path, encoding="utf-8") as fh:
        loaded = yaml.safe_load(fh)
    if loaded is None:
        return []
    if not isinstance(loaded, list):
        raise SystemExit(f"{what} at '{path}' must be a YAML list; got {type(loaded).__name__}.")
    return loaded


def validate_and_normalise(
    confirmed_rows: list[dict],
    proposed_keys: set[tuple[str, str, str]],
    computed_hash: str,
    require_rationale: bool,
    allow_stale_ledger: bool,
) -> list[dict]:
    unmatched: list[str] = []
    mismatched: list[str] = []
    rationale_violations: list[str] = []
    decision_violations: list[str] = []
    variable_violations: list[str] = []
    normalised: list[dict] = []

    for i, row in enumerate(confirmed_rows):
        row_number = i + 1
        for required in ("cohort_id", "dataset_id", "column", "decision"):
            if required not in row or row[required] in (None, ""):
                raise SystemExit(f"ledger.confirmed.yaml row {row_number}: missing required field '{required}'.")

        key = (row["cohort_id"], row["dataset_id"], row["column"])
        label = f"row {row_number} ({key[0]}/{key[1]}/{key[2]})"

        decision = row["decision"]
        if decision not in _VALID_DECISIONS:
            decision_violations.append(f"{label}: decision '{decision}' is not one of {sorted(_VALID_DECISIONS)}")
            continue

        # The Trap's guard: membership in a KEYED set built from
        # ledger.proposed.yaml, never a positional pairing of the two lists.
        if key not in proposed_keys:
            unmatched.append(f"{label}: no column with this (cohort_id, dataset_id, column) exists in ledger.proposed.yaml")

        if decision in _ACTIONABLE_DECISIONS and not (row.get("variable") or "").strip():
            variable_violations.append(f"{label}: decision '{decision}' requires a non-empty 'variable'")

        row_hash = row.get("proposed_hash")
        if row_hash != f"sha256:{computed_hash}":
            mismatched.append(f"{label}: proposed_hash={row_hash!r}, current ledger.proposed.yaml hashes to 'sha256:{computed_hash}'")

        if require_rationale and decision == "accept" and not (row.get("rationale") or "").strip():
            rationale_violations.append(f"{label}: decision 'accept' carries no rationale")

        normalised.append(
            {
                "cohort_id": row["cohort_id"],
                "dataset_id": row["dataset_id"],
                "column": row["column"],
                "decision": decision,
                "variable": row.get("variable"),
                "concept_id": row.get("concept_id"),
                "unit_in": row.get("unit_in"),
                "confirmed_by": row.get("confirmed_by"),
                "rationale": row.get("rationale"),
            }
        )

    if decision_violations:
        raise SystemExit("ledger.confirmed.yaml carries invalid decision value(s):\n  " + "\n  ".join(decision_violations))

    if unmatched:
        raise SystemExit(
            "ledger.confirmed.yaml carries row(s) matched to no column in ledger.proposed.yaml "
            "(matched by (cohort_id, dataset_id, column), never by row position -- the card's own Trap):\n  "
            + "\n  ".join(unmatched)
        )

    if variable_violations:
        raise SystemExit("ledger.confirmed.yaml carries row(s) with no target variable:\n  " + "\n  ".join(variable_violations))

    if mismatched:
        diff = "\n  ".join(mismatched)
        if allow_stale_ledger:
            print(
                f"WARNING: --allow_stale_ledger is set; proceeding despite {len(mismatched)} stale proposed_hash row(s):\n  {diff}",
                file=sys.stderr,
            )
        else:
            raise SystemExit(
                f"ledger.confirmed.yaml is stale against the current ledger.proposed.yaml "
                f"({len(mismatched)} row(s) disagree). Re-review against the current proposals, "
                f"or pass --allow_stale_ledger if this drift is expected (e.g. a pack bump that "
                f"did not change these proposals):\n  {diff}"
            )

    if rationale_violations:
        raise SystemExit(
            "ledger.confirmed.yaml carries accept row(s) with no rationale (--require_rationale):\n  "
            + "\n  ".join(rationale_violations)
        )

    normalised.sort(key=lambda r: (r["cohort_id"], r["dataset_id"], r["column"]))
    return normalised


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--confirmed", required=True, help="Path to ledger.confirmed.yaml.")
    ap.add_argument("--proposed", required=True, help="Path to the SAME run's ledger.proposed.yaml (staleness check only).")
    ap.add_argument(
        "--confirm-params",
        required=True,
        help='JSON object: {"require_rationale": bool, "allow_stale_ledger": bool}',
    )
    ap.add_argument("--out-decisions", required=True)
    args = ap.parse_args(argv)

    confirm_params = json.loads(args.confirm_params)
    require_rationale = confirm_params["require_rationale"]
    allow_stale_ledger = confirm_params["allow_stale_ledger"]

    computed_hash = _sha256_of_file(args.proposed)
    confirmed_rows = _load_yaml_list(args.confirmed, "ledger.confirmed.yaml")
    proposed_rows = _load_yaml_list(args.proposed, "ledger.proposed.yaml")
    proposed_keys = {(entry["cohort_id"], entry["dataset_id"], entry["column"]) for entry in proposed_rows}

    decisions = validate_and_normalise(
        confirmed_rows,
        proposed_keys,
        computed_hash,
        require_rationale,
        allow_stale_ledger,
    )

    with open(args.out_decisions, "w", encoding="utf-8") as fh:
        json.dump(decisions, fh, indent=2)
        fh.write("\n")

    return 0


if __name__ == "__main__":
    sys.exit(main())
