#!/usr/bin/env python3
"""§10.1 — collect the per-replicate ledger hashes into tests/invariant/report.json.

The card's assertion is one line -- `assert len(set(hashes)) == 1` -- and the
only interesting engineering here is refusing to report agreement that means
nothing. A `proof` verdict is a claim that N permutations of the outcome
column left the proposer's ledger unmoved, so every way that sentence can be
true-but-empty is ranked AHEAD of the hash count:

  no-outcome-column        A cohort carried no column the pack's outcome
                           variable names in ANY replicate, so every
                           permutation of it was a no-op. This is the
                           switched-off gate of the card's Trap, reached by
                           a fixture change rather than by an edit to the
                           assertion, which is why it has to be detected
                           here and not left to the reader of a green run.
  marginal-moved           Some replicate's written outcome column does not
                           carry the input column's multiset. That is the
                           Trap itself -- a permutation that moved a
                           cohort's marginal -- and a ledger that agreed
                           across such replicates agreed about the wrong
                           experiment. bin/permute_outcome.py already
                           refuses this at source; the report checks the
                           evidence rather than trusting the producer.
  incomplete-permutation   Some cohort was permuted in only SOME replicates.
                           Coverage is a per-replicate property: one
                           permuted replicate out of 100 does not make the
                           other 99 a test of anything, and summing coverage
                           across replicates would let it certify them.
  no-op-permutation        Some (cohort, dataset, column) had the identical
                           row ordering under every seed. A constant column,
                           or a one-row table, shuffles to itself; all N
                           runs are then literally the same run and
                           `n_distinct_hashes == 1` follows trivially.
  no-ledger                The proposer produced no ledger.proposed.yaml, so
                           there is nothing to hash. Zero distinct hashes is
                           not one distinct hash.
  insufficient-permutations
                           Fewer permutations were RUN than
                           --invariant_n_permutations requires. Shortening
                           the range is the cheapest way to weaken this
                           test, so the report names it rather than quietly
                           certifying a 3-seed run as a proof.

Only when none of those hold does the hash count decide: exactly one hash is
a `proof`, anything else is a `leak`. Never "mostly agree" (§10.1 nogo).

`n_permutations` is counted from the permutation manifests -- the work that
was observed -- not from the seed list that was requested. The two are
reported separately (`n_permutations` vs `n_seeds_requested`) because a run
that asked for 100 and permuted 3 must not be able to report 100.
"""
from __future__ import annotations

import argparse
import glob
import json
import sys


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
    ap.add_argument(
        "--seeds",
        required=True,
        help="Comma-separated list of the replicate seeds this run resolved "
        "--permute_outcome_seed to. The workflow has already parsed the "
        "spec and hands over the resolved list, so there is exactly one "
        "parser for `int or range` in this pipeline and no way for a "
        "second one to drift from it.",
    )
    ap.add_argument("--n-required", required=True, type=int)
    ap.add_argument("--scope", required=True)
    ap.add_argument("--out-report", required=True)
    args = ap.parse_args(argv)

    seeds = [int(s) for s in args.seeds.split(",") if s.strip()]
    if not seeds:
        print("refusing to report on a run with no seeds", file=sys.stderr)
        return 1

    records: list[dict] = []
    # cohort -> the set of replicates in which at least one of its columns was
    # permuted. A SET of replicates, not a count: a cohort is covered when
    # ANY of its datasets carries the outcome (so this unions across
    # datasets), but it must be covered in EVERY replicate (so a count summed
    # over replicates would let one permuted replicate vouch for 99 that were
    # never touched).
    cohort_replicates: dict[str, set[int]] = {}
    for path in sorted(glob.glob(args.permutation_glob)):
        with open(path, encoding="utf-8") as fh:
            manifest = json.load(fh)
        cohort = manifest["cohort_id"]
        replicate = manifest["replicate"]
        cohort_replicates.setdefault(cohort, set())
        for column in manifest["columns"]:
            cohort_replicates[cohort].add(replicate)
            records.append(
                {
                    "replicate": replicate,
                    "cohort_id": cohort,
                    "dataset_id": manifest["dataset_id"],
                    "column": column["column"],
                    "n_rows": column["n_rows"],
                    "baseline_marginal_sha256": column.get("baseline_marginal_sha256"),
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
    distinct = sorted(set(ledger_hashes.values()))

    # Observed, not requested: what this run actually permuted.
    observed_replicates = sorted({r["replicate"] for r in records})
    n_permutations = len(observed_replicates)

    seed_set = set(seeds)
    uncovered = sorted(c for c, reps in cohort_replicates.items() if not reps)
    incomplete = {
        cohort: sorted(seed_set - reps)
        for cohort, reps in cohort_replicates.items()
        if reps and (seed_set - reps)
    }

    # The Trap, checked against the evidence: the written column's multiset
    # must equal the input column's. A record whose producer did not report a
    # baseline is treated as a mismatch -- an unverifiable marginal is not a
    # preserved one.
    marginal_moved = [
        {
            "replicate": r["replicate"],
            "cohort_id": r["cohort_id"],
            "dataset_id": r["dataset_id"],
            "column": r["column"],
        }
        for r in records
        if r["baseline_marginal_sha256"] is None
        or r["baseline_marginal_sha256"] != r["marginal_sha256"]
    ]

    # A permutation that permuted nothing. Only meaningful with more than one
    # seed: a single-seed run has nothing to differ from.
    groups: dict[tuple, set[str]] = {}
    for r in records:
        groups.setdefault((r["cohort_id"], r["dataset_id"], r["column"]), set()).add(r["ordered_sha256"])
    no_op = (
        [
            {"cohort_id": c, "dataset_id": d, "column": col}
            for (c, d, col), orderings in sorted(groups.items())
            if len(orderings) == 1
        ]
        if len(seeds) > 1
        else []
    )

    if uncovered:
        verdict = "no-outcome-column"
    elif marginal_moved:
        verdict = "marginal-moved"
    elif incomplete:
        verdict = "incomplete-permutation"
    elif no_op:
        verdict = "no-op-permutation"
    elif missing:
        verdict = "no-ledger"
    elif n_permutations < args.n_required:
        verdict = "insufficient-permutations"
    elif len(distinct) == 1:
        verdict = "proof"
    else:
        verdict = "leak"

    report = {
        # The card's OUT slot names these two. Everything below them exists
        # so a failing run says why instead of only how much.
        "n_permutations": n_permutations,
        "n_distinct_hashes": len(distinct),
        "verdict": verdict,
        "scope": args.scope,
        "n_permutations_required": args.n_required,
        "n_seeds_requested": len(seeds),
        "seeds": seeds,
        "replicates_permuted": observed_replicates,
        "ledger_hashes": ledger_hashes,
        "missing_ledgers": missing,
        "cohorts_without_outcome_column": uncovered,
        "cohorts_with_incomplete_replicates": {k: incomplete[k] for k in sorted(incomplete)},
        "marginal_moved": marginal_moved,
        "no_op_permutations": no_op,
        "permutations": records,
    }
    with open(args.out_report, "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=2)
        fh.write("\n")

    # Loud on stdout as well as in the file: a run reaching this point with a
    # non-proof verdict has produced a report nobody is required to open, and
    # the nf-test assertion that fails on it reads better with the reason
    # already in the log.
    print(f"invariant: verdict={verdict} n_permutations={n_permutations} n_distinct_hashes={len(distinct)}")
    if uncovered:
        print(
            f"invariant: cohort(s) {', '.join(uncovered)} carry no column named by the "
            f"concept pack's outcome variable -- every permutation was a no-op, so this "
            f"run proves nothing about the invariant.",
            file=sys.stderr,
        )
    if marginal_moved:
        print(
            f"invariant: {len(marginal_moved)} permuted column(s) do not carry their "
            f"input column's multiset -- the permutation moved a cohort's outcome "
            f"marginal (§10.1 Trap), so no hash agreement across these replicates "
            f"means anything.",
            file=sys.stderr,
        )
    if incomplete:
        for cohort in sorted(incomplete):
            print(
                f"invariant: cohort {cohort} was permuted in only "
                f"{len(seed_set) - len(incomplete[cohort])} of {len(seed_set)} "
                f"replicate(s); replicate(s) {incomplete[cohort][:5]}"
                f"{' ...' if len(incomplete[cohort]) > 5 else ''} left it untouched.",
                file=sys.stderr,
            )
    if no_op:
        print(
            f"invariant: {len(no_op)} column(s) had the identical row ordering under "
            f"every seed -- the permutation changed nothing, so all replicates are the "
            f"same run and one hash proves nothing.",
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
