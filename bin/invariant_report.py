#!/usr/bin/env python3
"""§10.1 — collect each replicate's scoped artefact hashes into tests/invariant/report.json.

The card's assertion is one line -- `assert len(set(hashes)) == 1` -- and the
only interesting engineering here is refusing to report agreement that means
nothing. A `proof` verdict is a claim that N permutations of the outcome
column left the scope's artefacts unmoved, so every way that sentence can be
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
  no-ledger                Some replicate produced no ledger.proposed.yaml,
                           so there is nothing to hash. Zero distinct hashes
                           is not one distinct hash.
  no-mapped-artefact       Scope 'map' only. Some replicate produced no
                           mapped/ digest -- the run stopped short of the
                           stage the scope is named for, or the mapper died
                           on that replicate. Reporting `proof` from the
                           ledgers alone would certify a scope half of which
                           never ran, which is the exact failure ADR-003's
                           enum refusal and ADR-004's run-time gate both
                           exist to prevent.
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

------------------------------------------------------------------------
Scopes, and why one hash per replicate is a COMPOSITE (ADR-004)
------------------------------------------------------------------------

ADR-003 scoped the harness to `ledger.proposed.yaml`. ADR-004 widened it to
'map', and was explicit that the widened scope is *the composition of the
propose scope with the §3 -> §6 edge* -- not a replacement for it. §6.1
consumes `link/links.parquet` for `person_id`, which is a statistic computed
jointly across records; the ledger remains just as much inside the claim as
it was before.

So a scope names a LIST of artefacts, and a replicate's hash is the digest of
all of them together:

    propose  ->  [ledger]           composite == the ledger hash itself
    map      ->  [ledger, mapped]   composite == sha256 over both

The composite is what `n_distinct_hashes` counts, so the card's one-line
assertion still reads literally and the 'propose' scope's numbers are
byte-identical to what they were before this file learned about scopes. The
per-artefact counts are reported alongside it, because "the composite moved"
is not an answer to "which stage leaked".

The BASELINE replicate (key 'null' -- the unpermuted run) is required and
hashed like any other. That is what makes this a test of outcome-blindness
rather than of self-consistency: 100 permuted replicates that agree with each
other but not with the run on the real data have not shown the outcome was
ignored, only that the leak was deterministic.
"""
from __future__ import annotations

import argparse
import glob
import hashlib
import json
import sys

# The key the workflow's `"${replicate}=..."` renders for the baseline, whose
# replicate is Groovy null. Named rather than spelled inline in four places:
# it is a value crossing a language boundary, and a bare "null" literal in a
# comparison reads like a mistake every time anyone new sees it.
BASELINE = "null"

# Which artefacts each --scope holds its claim over, in the order they enter
# the composite. ADR-003 for 'propose', ADR-004 for 'map'. Adding a scope is a
# design change requiring an ADR (the workflow refuses any scope it has no
# wiring for, before this file is ever reached) -- this table is where the
# decision is recorded once the ADR exists.
SCOPE_ARTEFACTS = {
    "propose": ["ledger"],
    "map": ["ledger", "mapped"],
}

# Artefact -> the verdict reported when some replicate did not produce it.
MISSING_VERDICT = {
    "ledger": "no-ledger",
    "mapped": "no-mapped-artefact",
}

ARTEFACT_LABEL = {
    "ledger": "ledger.proposed.yaml",
    "mapped": "mapped/",
}


def _parse_hashes(entries: list[str]) -> dict[str, str]:
    """"<replicate>=<sha256>" pairs into a dict. The replicate is a string
    key throughout, including the baseline's 'null': these come off a Nextflow
    channel as text, and re-typing them here would only add a second place
    where the baseline's key has to be recognised."""
    out: dict[str, str] = {}
    for entry in entries:
        replicate, _, digest = entry.partition("=")
        out[replicate.strip()] = digest.strip()
    return out


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
        "--mapped-hash",
        action="append",
        default=[],
        metavar="REPLICATE=SHA256",
        help="One per replicate that produced a mapped/ artefact digest "
        "(ADR-004). A CANONICAL content digest computed by "
        "bin/artefact_digest.py, never a digest of the parquet bytes -- see "
        "that script's docstring for why the distinction is load-bearing. "
        "Empty unless --scope holds a claim over 'map'.",
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

    if args.scope not in SCOPE_ARTEFACTS:
        # Unreachable from the pipeline -- workflows/harmonize.nf refuses an
        # unwired scope before any task is scheduled. Checked anyway, because
        # the alternative is silently reporting a scope this file does not
        # know the artefacts of, which is precisely a [SUCCESS] over an
        # unmeasured claim.
        print(
            f"invariant: --scope '{args.scope}' names no artefact set. Known scopes: "
            f"{', '.join(sorted(SCOPE_ARTEFACTS))}.",
            file=sys.stderr,
        )
        return 1
    artefacts = SCOPE_ARTEFACTS[args.scope]

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

    hashes = {
        "ledger": _parse_hashes(args.ledger_hash),
        "mapped": _parse_hashes(args.mapped_hash),
    }

    # Every replicate the scope's claim covers: the seeds, plus the BASELINE.
    # The baseline is not decoration -- see the module docstring. Ordered with
    # the baseline first so the report reads the way the experiment does.
    required = [BASELINE] + [str(seed) for seed in seeds]

    missing = {
        artefact: [rep for rep in required if rep not in hashes[artefact]]
        for artefact in artefacts
    }

    # One hash per replicate, over every artefact in the scope. For 'propose'
    # this is the ledger hash unchanged -- deliberately NOT sha256 of a
    # one-element composite, so that scope's reported hashes stay the same
    # values they have always been and an old report is still comparable with
    # a new one.
    composite: dict[str, str] = {}
    for rep in required:
        present = [hashes[a].get(rep) for a in artefacts]
        if any(h is None for h in present):
            continue
        if len(artefacts) == 1:
            composite[rep] = present[0]
        else:
            blob = "\n".join(f"{a}={h}" for a, h in zip(artefacts, present))
            composite[rep] = hashlib.sha256(blob.encode()).hexdigest()

    distinct = sorted(set(composite.values()))
    distinct_per_artefact = {
        artefact: len(sorted({hashes[artefact][rep] for rep in required if rep in hashes[artefact]}))
        for artefact in artefacts
    }

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

    # Ranked ahead of the hash count, worst-confound first. The missing-
    # artefact checks run in the scope's own artefact order, so a map-scoped
    # run that produced no ledger says 'no-ledger' rather than blaming the
    # stage that never got the chance to run.
    missing_verdict = next(
        (MISSING_VERDICT[a] for a in artefacts if missing[a]),
        None,
    )

    if uncovered:
        verdict = "no-outcome-column"
    elif marginal_moved:
        verdict = "marginal-moved"
    elif incomplete:
        verdict = "incomplete-permutation"
    elif no_op:
        verdict = "no-op-permutation"
    elif missing_verdict:
        verdict = missing_verdict
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
        # What the scope's claim is actually held over, so a report can be
        # read years later without also reading this file.
        "scope_artefacts": [ARTEFACT_LABEL[a] for a in artefacts],
        "n_permutations_required": args.n_required,
        "n_seeds_requested": len(seeds),
        "seeds": seeds,
        "replicates_permuted": observed_replicates,
        "ledger_hashes": hashes["ledger"],
        "mapped_hashes": hashes["mapped"],
        "composite_hashes": composite,
        "n_distinct_hashes_per_artefact": distinct_per_artefact,
        "missing_ledgers": missing.get("ledger", []),
        "missing_mapped": missing.get("mapped", []),
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
    print(
        f"invariant: verdict={verdict} scope={args.scope} "
        f"({', '.join(ARTEFACT_LABEL[a] for a in artefacts)}) "
        f"n_permutations={n_permutations} n_distinct_hashes={len(distinct)}"
    )
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
    for artefact in artefacts:
        absent = missing[artefact]
        if absent:
            print(
                f"invariant: {len(absent)} of {len(required)} replicate(s) "
                f"(baseline included) produced no {ARTEFACT_LABEL[artefact]}, so there is "
                f"nothing to hash for them: {absent[:5]}{' ...' if len(absent) > 5 else ''}",
                file=sys.stderr,
            )
    if verdict == "leak":
        for artefact in artefacts:
            if distinct_per_artefact.get(artefact, 0) > 1:
                print(
                    f"invariant: {ARTEFACT_LABEL[artefact]} took "
                    f"{distinct_per_artefact[artefact]} distinct values across the "
                    f"{len(required)} replicate(s) -- the outcome column moved it.",
                    file=sys.stderr,
                )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
