#!/usr/bin/env python3
"""§10.1 — collect the per-replicate ledger hashes into tests/invariant/report.json.

The card's assertion is one line -- `assert len(set(hashes)) == 1` -- and the
only interesting engineering here is refusing to report agreement that means
nothing. Three ways a green invariant test can be a lie, all of them ranked
ahead of "the hashes agree":

  no-outcome-column        No table carried a column the pack's outcome
                           variable names, so every permutation was a no-op
                           and all N runs were the same run. This is the
                           switched-off gate of the card's Trap, reached by
                           a fixture change rather than by an edit to the
                           assertion, which is why it has to be detected
                           here and not left to the reader of a green run.
  no-ledger                The proposer produced no ledger.proposed.yaml, so
                           there is nothing to hash. Zero distinct hashes is
                           not one distinct hash.
  insufficient-permutations
                           Fewer seeds were run than --invariant_n_permutations
                           requires. Shortening the range is the cheapest way
                           to weaken this test, so the report names it rather
                           than quietly certifying a 3-seed run as a proof.

Only when none of those hold does the hash count decide: exactly one hash is
a `proof`, anything else is a `leak`. Never "mostly agree" (§10.1 nogo).
"""
from __future__ import annotations

import argparse
import glob
import json
import sys


def parse_seeds(spec: str) -> list[int]:
    """`7` -> [7]; `1..100` -> [1, ..., 100]. The same two forms the
    --permute_outcome_seed schema property accepts."""
    spec = spec.strip()
    if ".." in spec:
        low, _, high = spec.partition("..")
        return list(range(int(low), int(high) + 1))
    return [int(spec)]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--permutation-glob", required=True)
    ap.add_argument(
        "--ledger-hash",
        action="append",
        default=[],
        metavar="REPLICATE=SHA256",
        help="One per replicate that produced a ledger.proposed.yaml. Absent "
        "replicates are reported as missing rather than skipped.",
    )
    ap.add_argument("--seed-spec", required=True)
    ap.add_argument("--n-required", required=True, type=int)
    ap.add_argument("--scope", required=True)
    ap.add_argument("--out-report", required=True)
    args = ap.parse_args(argv)

    seeds = parse_seeds(args.seed_spec)

    records: list[dict] = []
    # cohort -> how many outcome columns were permuted anywhere in it. A
    # cohort is covered when ANY of its datasets carries the outcome, so this
    # counts across datasets and only a cohort totalling zero is a defect.
    per_cohort: dict[str, int] = {}
    for path in sorted(glob.glob(args.permutation_glob)):
        with open(path, encoding="utf-8") as fh:
            manifest = json.load(fh)
        cohort = manifest["cohort_id"]
        per_cohort.setdefault(cohort, 0)
        for column in manifest["columns"]:
            per_cohort[cohort] += 1
            records.append(
                {
                    "replicate": manifest["replicate"],
                    "cohort_id": cohort,
                    "dataset_id": manifest["dataset_id"],
                    "column": column["column"],
                    "n_rows": column["n_rows"],
                    "marginal_sha256": column["marginal_sha256"],
                    "ordered_sha256": column["ordered_sha256"],
                }
            )
    records.sort(key=lambda r: (r["replicate"], r["cohort_id"], r["dataset_id"], r["column"]))

    ledger_hashes: dict[str, str] = {}
    for entry in args.ledger_hash:
        replicate, _, digest = entry.partition("=")
        ledger_hashes[replicate.strip()] = digest.strip()

    missing = [seed for seed in seeds if str(seed) not in ledger_hashes]
    uncovered = sorted(cohort for cohort, count in per_cohort.items() if count == 0)
    distinct = sorted(set(ledger_hashes.values()))

    if uncovered:
        verdict = "no-outcome-column"
    elif missing:
        verdict = "no-ledger"
    elif len(seeds) < args.n_required:
        verdict = "insufficient-permutations"
    elif len(distinct) == 1:
        verdict = "proof"
    else:
        verdict = "leak"

    report = {
        # The card's OUT slot names these two. Everything below them exists
        # so a failing run says why instead of only how much.
        "n_permutations": len(seeds),
        "n_distinct_hashes": len(distinct),
        "verdict": verdict,
        "scope": args.scope,
        "n_permutations_required": args.n_required,
        "seeds": seeds,
        "ledger_hashes": ledger_hashes,
        "missing_ledgers": missing,
        "cohorts_without_outcome_column": uncovered,
        "permutations": records,
    }
    with open(args.out_report, "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=2)
        fh.write("\n")

    # Loud on stdout as well as in the file: a run reaching this point with a
    # non-proof verdict has produced a report nobody is required to open, and
    # the nf-test assertion that fails on it reads better with the reason
    # already in the log.
    print(f"invariant: verdict={verdict} n_permutations={len(seeds)} n_distinct_hashes={len(distinct)}")
    if uncovered:
        print(
            f"invariant: cohort(s) {', '.join(uncovered)} carry no column named by the "
            f"concept pack's outcome variable -- every permutation was a no-op, so this "
            f"run proves nothing about the invariant.",
            file=sys.stderr,
        )
    if missing:
        print(
            f"invariant: {len(missing)} of {len(seeds)} replicate(s) produced no "
            f"ledger.proposed.yaml, so there is nothing to hash.",
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
