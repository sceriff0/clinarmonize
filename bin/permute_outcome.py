#!/usr/bin/env python3
"""§10.1 — the InvariantProbe seam: perturb(inputs, seed) -> inputs.

Permutes, within one table, every column that the concept pack's
`outcome: true` variables name -- and nothing else. §0 is a claim about the
proposer's inputs, so the only honest place to perturb is at the input
boundary: the table's bytes. Everything downstream (profile, link, propose)
re-derives from the perturbed bytes, which is what makes the ledger hash a
real measurement rather than a restatement of the pipeline's own caching.

Why a shuffle and not a rewrite: a within-column shuffle preserves the
column's multiset exactly, so every marginal -- type, cardinality, value set,
quantiles, unit candidates -- is byte-for-byte unchanged. The ONLY thing it
destroys is the row-level pairing between the outcome and every other column.
That is precisely the signal an outcome-aware channel would be reading, so a
ledger that moves under this shuffle moved because something read the joint
distribution. Nothing else can explain it.

Column identification keys on the pack's flag, never on a column name
(§0): the pack declares which of ITS variables is the outcome, and a source
column is an outcome column when its header canonicalises to that variable's
name. Rename the pack variable and the fixture headers together and the
harness follows; hard-code a name here and the invariant stops binding
whatever THIS pack calls an outcome, which is the whole point of the flag.

A table with no such column is passed through untouched and reported as
having permuted nothing. That is not an error here: a cohort is covered when
ANY of its datasets carries the outcome, which is a run-level judgement made
by bin/invariant_report.py, not a per-table one.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import random
import sys

import yaml


def canonical(name: str) -> str:
    """Header/variable name comparison form: case and separators carry no
    meaning across an export boundary (`TIME_TO_EVENT`, `time to event` and
    `TimeToEvent` are one column), but the letters and digits do."""
    return "".join(ch for ch in name.lower() if ch.isalnum())


def outcome_variables(pack_path: str) -> list[str]:
    """The pack's `outcome: true` variables -- the ONLY definition of "the
    outcome variable" anywhere in this pipeline (§0)."""
    with open(pack_path, encoding="utf-8") as fh:
        pack = yaml.safe_load(fh)
    return [v["name"] for v in (pack.get("variables") or []) if v.get("outcome") is True]


def column_rng(seed: int, cohort_id: str, dataset_id: str, column: str) -> random.Random:
    """A permutation that depends on the seed and on WHICH column of WHICH
    table it is shuffling, and on nothing else -- not on task submission
    order, not on how many tables happened to run first. Nextflow runs these
    tasks concurrently and in no fixed order, so a single RNG stream drawn
    across tables would make the permutation a function of the scheduler and
    the run would stop being reproducible from its seed alone.
    """
    material = f"{seed}|{cohort_id}|{dataset_id}|{column}".encode()
    return random.Random(int.from_bytes(hashlib.sha256(material).digest()[:8], "big"))


def read_table(path: str) -> list[list[str]]:
    """Every read of a CSV in this module goes through here, including the
    re-read of what we just wrote. The permuted table is verified as BYTES on
    disk, not as the in-memory list we believe we wrote, because the thing the
    proposer will actually consume is the file."""
    with open(path, newline="", encoding="utf-8", errors="surrogateescape") as fh:
        return list(csv.reader(fh))


def _sha256(values: list[str]) -> str:
    h = hashlib.sha256()
    for value in values:
        h.update(value.encode("utf-8", errors="surrogateescape"))
        h.update(b"\x1f")  # unit separator: "a","bc" cannot collide with "ab","c"
    return h.hexdigest()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--table", required=True)
    ap.add_argument("--pack", required=True)
    ap.add_argument(
        "--seeds",
        required=True,
        help="Comma-separated replicate seeds. Every seed is an independent "
        "shuffle of the same input table, so one invocation covers the whole "
        "replicate axis for this dataset. Batched deliberately: shuffling a "
        "column N times is one pure function called N times, and paying a "
        "container start for each would make a 100-permutation run cost 100x "
        "its own work -- which is how a test that must run on every push "
        "(\u00a714.2) becomes a test somebody moves to a nightly.",
    )
    ap.add_argument("--cohort-id", required=True)
    ap.add_argument("--dataset-id", required=True)
    ap.add_argument(
        "--out-prefix",
        required=True,
        help="Outputs are <prefix>.p<seed>.csv and <prefix>.p<seed>.permutation.json. "
        "The seed is in the filename because that is what carries the replicate id "
        "back across the process boundary into the workflow's channel.",
    )
    args = ap.parse_args(argv)

    seeds = [int(s) for s in args.seeds.split(",") if s.strip()]
    if not seeds:
        print("refusing to run with no seeds", file=sys.stderr)
        return 1

    targets = outcome_variables(args.pack)
    wanted = {canonical(name) for name in targets}

    rows = read_table(args.table)

    if not rows:
        print(f"refusing to permute an empty table: {args.table}", file=sys.stderr)
        return 1

    header, body = rows[0], rows[1:]

    # A ragged row is refused, not repaired. csv.reader accepts one silently,
    # and a short row would make the write-back lossy: the shuffled value
    # aimed at a missing cell has nowhere to land, so the table on disk would
    # lose a real value and keep a phantom empty one -- a DIFFERENT value
    # under every seed. That is a permutation that moves the cohort's outcome
    # marginal (the §10.1 Trap) arriving through the CSV parser rather than
    # through the shuffle, and this module's nogo ("never converts, rounds,
    # retypes or reformats a value") is only true if it cannot happen.
    ragged = [n for n, row in enumerate(body, start=2) if len(row) != len(header)]
    if ragged:
        print(
            f"refusing to permute a ragged table: {args.table} has {len(header)} header "
            f"field(s) but line(s) {', '.join(str(n) for n in ragged[:5])}"
            f"{' ...' if len(ragged) > 5 else ''} differ. Permuting a ragged column "
            f"cannot preserve its marginal.",
            file=sys.stderr,
        )
        return 1

    indices = [i for i, name in enumerate(header) if canonical(name) in wanted]
    originals = {i: [row[i] for row in body] for i in indices}

    # The marginal of the column AS GIVEN, hashed once before anything is
    # shuffled. This -- not the shuffle's own output -- is what every
    # replicate's written column is checked against below. Hashing
    # sorted(shuffle(x)) against sorted(x) computed from the same list is an
    # identity and can never fail; hashing the bytes that reached disk against
    # the bytes that arrived can, which is the only version of this guard
    # worth having.
    baseline_marginal = {i: _sha256(sorted(originals[i])) for i in indices}

    for seed in seeds:
        for index in indices:
            column = header[index]

            # The permutation itself. Shuffling a COPY of this one table's
            # values and writing them back into this one table is what
            # "WITHIN each cohort" means operationally: no value ever leaves
            # the table it came from, so no value can cross a cohort
            # boundary, so no cohort's outcome marginal can move (§10.1
            # Trap). Permuting per-table rather than per-cohort is strictly
            # stronger than the card requires and cannot fail in the
            # direction the Trap warns about.
            #
            # Shuffled from `originals`, never from the previous seed's
            # output: each replicate must be an independent function of its
            # own seed, not of the order the seeds happened to be processed
            # in.
            shuffled = list(originals[index])
            column_rng(seed, args.cohort_id, args.dataset_id, column).shuffle(shuffled)

            for row, value in zip(body, shuffled):
                row[index] = value

        out_table = f"{args.out_prefix}.p{seed}.csv"
        with open(out_table, "w", newline="", encoding="utf-8", errors="surrogateescape") as fh:
            writer = csv.writer(fh, lineterminator="\n")
            writer.writerow(header)
            writer.writerows(body)

        # Re-read the file just written and measure THAT. Everything reported
        # about this replicate is a measurement of the artefact the rest of
        # the pipeline will read, so a value lost, duplicated, retyped or
        # reformatted between the shuffle and the disk is caught here rather
        # than being reported as a preserved marginal.
        written = read_table(out_table)
        if not written or written[0] != header:
            print(f"refusing: {out_table} did not round-trip its header", file=sys.stderr)
            return 1
        written_body = written[1:]
        if len(written_body) != len(body):
            print(
                f"refusing: {out_table} has {len(written_body)} data row(s), "
                f"{args.table} had {len(body)}",
                file=sys.stderr,
            )
            return 1

        permuted: list[dict] = []
        for index in indices:
            column = header[index]
            values = [row[index] if index < len(row) else "" for row in written_body]
            marginal = _sha256(sorted(values))
            if marginal != baseline_marginal[index]:
                print(
                    f"refusing: column '{column}' of {out_table} (seed {seed}) does not "
                    f"carry the same multiset of values as {args.table}. The permutation "
                    f"moved this cohort's outcome marginal, which is the one thing it "
                    f"must never do (§10.1 Trap).",
                    file=sys.stderr,
                )
                return 1

            permuted.append(
                {
                    "column": column,
                    "n_rows": len(values),
                    # The marginal of the INPUT column, and the marginal of
                    # the column as written. They are recorded separately and
                    # compared above so the report carries both sides of the
                    # comparison rather than one side twice.
                    "baseline_marginal_sha256": baseline_marginal[index],
                    "marginal_sha256": marginal,
                    # The same written values in row order: MUST differ
                    # between seeds, or the "permutation" was a no-op and the
                    # harness is measuring nothing.
                    "ordered_sha256": _sha256(values),
                }
            )

        manifest = {
            "replicate": seed,
            "cohort_id": args.cohort_id,
            "dataset_id": args.dataset_id,
            "n_rows": len(body),
            "outcome_variables": targets,
            "columns": permuted,
        }
        with open(f"{args.out_prefix}.p{seed}.permutation.json", "w", encoding="utf-8") as fh:
            json.dump(manifest, fh, indent=2, sort_keys=True)
            fh.write("\n")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
