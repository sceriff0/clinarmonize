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

`value_map:` -- the §6.3 contract change, and why it is HERE
------------------------------------------------------------
§6.3's IN slot names "value-map rules", and no such rule could exist:
bin/compile_rules.py only ever emitted kind "column_map", and its docstring
says why in as many words -- a value_map "needs a value-level mapping table
nothing in ledger.confirmed.yaml's Contract offers". §5.2's Contract does
enumerate column_map|value_map|unit_convert|derive as the enum, so the KIND
was anticipated; what was missing is the human decision that produces one.

A value collapse is a harmonization decision -- "four source grades
collapsing into two canonical ones is sometimes correct and sometimes the
destruction of the effect being studied" (§6.3's Why) -- so it can only come
from the gate. Inferring it from §2.1's observed `domain_values` would be
this card's own Why deleted in one line: "a pipeline that fills that gap
with its own top-ranked guess has quietly deleted the only review step in
the design while still emitting a file called a ledger."

So a confirmed row may carry an OPTIONAL `value_map:` list, one entry per
collapse group:

    value_map:
      - from: ["0", "1"]        # the source values, as written in the table
        to: "0-1"               # the canonical value; §6.3 checks it against
                                # the pack's domain_values, not this file
        concept_id: 4000        # the VALUE's standard concept (optional)
        rationale: "..."        # required under --require_rationale

The unit is a collapse GROUP and not a value-to-value pair because §6.3's
Contract makes `from` a list and fan_in `len(from)`: a pair-shaped ledger
could not express a fan-in at all, and fan_in is the one field that card's
nogo makes mandatory.

Four refusals live here rather than in §6.3, because all four are questions
the FILE fails to answer and none of them needs a table in hand:

  * a value claimed by two groups   -- which one applies is then a decision
                                       the reviewer did not make, and a
                                       mapper that picked one would make it
                                       silently. Same shape as §6.2's
                                       duplicate-factor refusal.
  * two groups with the same `to`   -- one collapse written twice. A table
                                       carrying two answers to one question
                                       is ambiguous whether or not they agree.
  * a value_map on reject/defer     -- a decision that maps nothing, carrying
                                       instructions for how to map it.
  * an empty `value_map: []`        -- a reviewer who started and stopped.
                                       Treating it as "no value mapping" is
                                       precisely the guessing this card
                                       exists to refuse.

What is NOT checked here: whether `to` is a value the concept pack declares.
This script never reads the pack (§5.1's IN slot is the two ledgers and
nothing else), and §6.3 is where the pack is in hand.

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


def _normalise_value_map(
    row: dict,
    label: str,
    decision: str,
    require_rationale: bool,
) -> list[dict]:
    """Validate one confirmed row's `value_map:` and return it normalised.

    Raises on every malformed or ambiguous shape (see the module docstring
    for the four refusals and why they are all parse-time). Returns [] for a
    row that declares no value_map at all -- which is every row every phase
    before this one wrote, so the field is additive and no existing ledger
    becomes invalid by its absence.

    `from` is SORTED. §5.2's rule_id is a content hash of (kind, from, to,
    params) and its done-when is that reordering the confirmed ledger leaves
    every rule_id unchanged; a collapse group is a SET of source values, so
    writing ["1", "0"] and ["0", "1"] must not produce two different rules
    for one decision. Sorting here is what makes that true structurally
    rather than by asking reviewers to be tidy.
    """
    if "value_map" not in row:
        return []

    if decision not in _ACTIONABLE_DECISIONS:
        raise SystemExit(
            f"ledger.confirmed.yaml {label}: decision '{decision}' carries a value_map. A decision that "
            "maps nothing cannot also say how to map its values; drop the value_map or change the decision."
        )

    groups = row["value_map"]
    if not isinstance(groups, list) or not groups:
        raise SystemExit(
            f"ledger.confirmed.yaml {label}: value_map must be a non-empty list of collapse groups. An "
            "empty one is a review that was started and not finished, and reading it as 'no value mapping' "
            "would be the pipeline supplying the decision (§5.1's Why)."
        )

    normalised: list[dict] = []
    claimed_by: dict[str, str] = {}
    targets: dict[str, int] = {}

    for index, group in enumerate(groups):
        group_label = f"{label} value_map[{index}]"
        if not isinstance(group, dict):
            raise SystemExit(f"ledger.confirmed.yaml {group_label}: each collapse group must be a mapping.")

        source_values = group.get("from")
        if not isinstance(source_values, list) or not source_values:
            raise SystemExit(
                f"ledger.confirmed.yaml {group_label}: 'from' must be a non-empty list of source values. It "
                "is a list and not a single value because §6.3's fan_in is len(from), and a pair-shaped "
                "entry could not record a fan-in at all."
            )
        if any(isinstance(value, (list, dict)) for value in source_values):
            raise SystemExit(f"ledger.confirmed.yaml {group_label}: 'from' must be a flat list of scalars.")

        rendered = [str(value) for value in source_values]
        duplicated = sorted({value for value in rendered if rendered.count(value) > 1})
        if duplicated:
            raise SystemExit(
                f"ledger.confirmed.yaml {group_label}: source value(s) {duplicated} are listed twice in one "
                "group. fan_in is len(from), so a repeated value would inflate the recorded width of the "
                "collapse without widening the collapse."
            )

        target = group.get("to")
        if target is None or not str(target).strip():
            raise SystemExit(
                f"ledger.confirmed.yaml {group_label}: 'to' is the canonical value this group collapses "
                "into and cannot be empty."
            )
        target = str(target)

        for value in rendered:
            if value in claimed_by:
                raise SystemExit(
                    f"ledger.confirmed.yaml {label}: source value '{value}' is claimed by two collapse "
                    f"groups ('{claimed_by[value]}' and '{target}'). Which one applies is a decision this "
                    "file does not make, and §6.3 will not make it silently."
                )
            claimed_by[value] = target

        if target in targets:
            raise SystemExit(
                f"ledger.confirmed.yaml {label}: '{target}' is the target of two collapse groups "
                f"(value_map[{targets[target]}] and value_map[{index}]). One collapse, written once -- two "
                "entries for one target are two answers to a question with one."
            )
        targets[target] = index

        concept_id = group.get("concept_id")
        if concept_id is not None and not isinstance(concept_id, int):
            raise SystemExit(
                f"ledger.confirmed.yaml {group_label}: 'concept_id' must be an integer (the standard "
                f"concept the canonical value denotes); got {concept_id!r}."
            )

        rationale = group.get("rationale")
        if require_rationale and decision == "accept" and not (rationale or "").strip():
            raise SystemExit(
                f"ledger.confirmed.yaml {group_label}: collapsing {rendered} into '{target}' carries no "
                "rationale (--require_rationale). A collapse is the harmonization decision §6.3 exists to "
                "make visible, and the rationale is the only place the reviewer says why this one is safe."
            )

        normalised.append(
            {
                "from": sorted(rendered),
                "to": target,
                "concept_id": concept_id,
                "rationale": rationale,
            }
        )

    # Sorted by target, so decisions.json's own order does not depend on the
    # order the reviewer happened to type the groups in (§4.3's discipline,
    # applied to a list this card newly writes).
    normalised.sort(key=lambda group: group["to"])
    return normalised


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

        value_map = _normalise_value_map(row, label, decision, require_rationale)

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
                # Always present, [] when the row declares none: a key that
                # appears only on rows that use it makes "this column has no
                # value mapping" and "this ledger predates value mapping"
                # the same observation for every reader downstream.
                "value_map": value_map,
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
