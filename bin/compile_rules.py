#!/usr/bin/env python3
"""§5.2 -- compile decisions into versioned, addressable mapping rules.

Named alts seam (docs/steps/s5-2.md):
    RuleCompiler -- compile([Decision]) -> [Rule]

IN   ch_confirmed's decisions.json (§5.1's ONLY output this script reads --
     no pack, no ledger.proposed.yaml, no run-level state: the seam's own
     signature is compile([Decision]) -> [Rule], and Decision is exactly
     what decisions.json carries)
OUT  rules/ruleset.json  [ {rule_id, rule_version, kind, from, to, params} ]
SIDE none; rule_id is a content hash, so an unchanged rule keeps its id

Only 'accept' and 'remap' decisions ever produce a rule -- 'reject' and
'defer' have nothing to map, by construction (§5.1 nogo: this script never
promotes anything; it only compiles what a human already decided).

`kind`: "column_map" for every confirmed column, plus one "value_map" per
collapse group the row's `value_map:` declares (§6.3, added in phase 5).

Phase 0 emitted only column_map, and its reasoning is worth keeping because
exactly one of its three clauses stopped being true: "value_map needs a
value-level mapping table nothing in ledger.confirmed.yaml's Contract
offers". §5.1's Contract now offers one -- an optional `value_map:` list of
{from: [str], to: str, concept_id?, rationale?} on the confirmed row, which
is a human decision read from a file and not a table this compiler infers.
That is the ONLY thing that changed. The seam's signature is still
compile([Decision]) -> [Rule] and this script still reads nothing but
decisions.json: no pack, no ledger.proposed.yaml, no vocabulary.

The other two clauses still hold, and neither kind is emitted: unit_convert
needs the variable's CANONICAL unit, which lives in the concept pack this
compiler deliberately never reads (§6.2 does the conversion, with the pack in
hand); derive needs a formula, which is §7's. `unit_in` (the confirmed source
unit, §2.2/§5.1) is still recorded in a column_map rule's `params` --
honestly, as provenance the human resolved, never as a claim that a
conversion factor was computed here.

A value_map rule's shape, and why each half is what it is:

    from = {cohort_id, dataset_id, column, values: [str]}
    to   = {variable, concept_id, value: str}
    params = {}

`from.values` is inside `from` because rule_id is sha256(kind, from, to,
params): two collapse groups on ONE column must get two different ids, and
the only thing distinguishing them is which source values they claim. It
arrives already sorted from §5.1, so writing the group's values in a
different order cannot move a rule_id (this card's own done-when).

`to.concept_id` is the VALUE's standard concept, not the column's. The
column's is on its own column_map rule, and §6.1 already writes it into
<domain>_concept_id; §6.3 writes this one into value_as_concept_id. Reusing
the key name rather than inventing one keeps §5.2's Contract shape
({variable, concept_id}) literal, and `to.value` is what says which of the
two a reader is looking at.

`params` is EMPTY, and specifically does not carry `unit_in`. A categorical
value has no unit, and copying the column's onto its value rules would put a
field in the hash that means nothing about the rule it identifies.

rule_id is `rule_id_prefix + sha256(canonical_json(kind, from, to, params))`
-- the FULL hex digest, not a truncation (truncating to some short length
would itself be an undeclared magic number the card's Params table does not
license; a full digest costs nothing and carries zero collision risk).
rule_id_prefix is purely cosmetic per the card's own Params row; every bit
of the id's STABILITY comes from the digest of (kind, from, to, params), a
canonical (sorted-key, separator-fixed) JSON string built independently of
what order decisions.json's rows happen to arrive in -- reordering the input
therefore cannot move a single character of any rule_id
(tests/rules_stable.nf.test, the card's own Trap: "deriving rule_id from
position or time" is exactly what this construction cannot do, structurally,
not by convention).

Collision policy (`--fail_on_rule_collision`): two DIFFERENT source columns
(within the same cohort_id/dataset_id) compiling a rule for the SAME target
variable is a defect, never a silent merge (nogo: "Never merge two rules
that write the same target cell; fail and name both"). `fail_on_rule_collision`
controls only whether that defect is FATAL (the default) or a loud warning
that still keeps both rules in the output, unmerged -- never whether it is
detected or reported at all.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys

_ACTIONABLE_DECISIONS = {"accept", "remap"}


def _canonical_json(obj) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"))


def _rule(kind: str, from_: dict, to_: dict, params_: dict, rule_id_prefix: str, pack_version: str) -> dict:
    """One rule, with its id derived from EXACTLY (kind, from, to, params).

    The Trap's own guard, and the single place it lives: never a function of
    a decision's position in the input list, never of wall-clock time. Split
    out of compile_rules() below when value_map arrived so that the two kinds
    cannot drift into two hashing constructions -- which would be the same
    defect the Trap names, arriving by duplication instead of by design.
    """
    canonical = _canonical_json({"kind": kind, "from": from_, "to": to_, "params": params_})
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return {
        "rule_id": f"{rule_id_prefix}{digest}",
        "rule_version": pack_version,
        "kind": kind,
        "from": from_,
        "to": to_,
        "params": params_,
    }


def compile_rules(decisions: list[dict], rule_id_prefix: str, pack_version: str) -> list[dict]:
    rules: list[dict] = []
    for decision in decisions:
        if decision["decision"] not in _ACTIONABLE_DECISIONS:
            continue

        from_ = {
            "cohort_id": decision["cohort_id"],
            "dataset_id": decision["dataset_id"],
            "column": decision["column"],
        }
        rules.append(
            _rule(
                "column_map",
                from_,
                {"variable": decision["variable"], "concept_id": decision.get("concept_id")},
                {"unit_in": decision.get("unit_in")},
                rule_id_prefix,
                pack_version,
            )
        )

        # §6.3 -- one rule per collapse group. `kind` is inside the hash, so a
        # value_map rule for the same column gets its own stable id, distinct
        # from its column_map sibling's, without anything here arranging it.
        for group in decision.get("value_map") or []:
            rules.append(
                _rule(
                    "value_map",
                    {**from_, "values": list(group["from"])},
                    {
                        "variable": decision["variable"],
                        "concept_id": group.get("concept_id"),
                        "value": group["to"],
                    },
                    {},
                    rule_id_prefix,
                    pack_version,
                )
            )

    return rules


def _detect_collisions(rules: list[dict]) -> dict[tuple[str, str, str], set[str]]:
    """(cohort_id, dataset_id, to.variable) -> the set of source columns
    writing it; a set larger than one is the collision the nogo names.

    UNCHANGED by §6.3, and verified rather than assumed: a value_map rule and
    its column_map sibling are compiled from the SAME decision row, so they
    carry the same from.column and the same to.variable and add the same
    single element to the same set. Two collapse groups on one column do
    likewise. A value mapping therefore cannot manufacture a collision, and
    -- more importantly -- cannot mask one, because a genuine two-column
    collision is still two distinct columns in the set.
    """
    by_target: dict[tuple[str, str, str], set[str]] = {}
    for rule in rules:
        target_key = (rule["from"]["cohort_id"], rule["from"]["dataset_id"], rule["to"]["variable"])
        by_target.setdefault(target_key, set()).add(rule["from"]["column"])
    return {k: v for k, v in by_target.items() if len(v) > 1}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--decisions", required=True, help="Path to decisions.json (§5.1's output).")
    ap.add_argument(
        "--compile-params",
        required=True,
        help='JSON object: {"rule_id_prefix": str, "fail_on_rule_collision": bool}',
    )
    ap.add_argument("--pack-version", required=True, help="The concept pack's own `version` field.")
    ap.add_argument("--out-ruleset", required=True)
    args = ap.parse_args(argv)

    compile_params = json.loads(args.compile_params)
    rule_id_prefix = compile_params["rule_id_prefix"]
    fail_on_rule_collision = compile_params["fail_on_rule_collision"]

    with open(args.decisions, encoding="utf-8") as fh:
        decisions = json.load(fh)

    rules = compile_rules(decisions, rule_id_prefix, args.pack_version)

    collisions = _detect_collisions(rules)
    if collisions:
        described = "; ".join(
            f"{cohort}/{dataset}/{variable} written by columns {sorted(columns)}"
            for (cohort, dataset, variable), columns in sorted(collisions.items())
        )
        if fail_on_rule_collision:
            raise SystemExit(f"rule collision: two rules write the same target cell (never merged): {described}")
        print(f"WARNING: --fail_on_rule_collision is false; keeping both, unmerged: {described}", file=sys.stderr)

    # Deterministic order -- never DuckDB/dict-iteration incidental order,
    # matching bin/propose_ledger.py's own discipline (§4.3's Trap, the same
    # shape of trap this card's own Trap names for rule_id specifically).
    # (cohort, dataset, column) was a TOTAL order while one column produced
    # exactly one rule. §6.3 makes it one column, many rules, so `kind` and
    # `rule_id` are appended: without them the written order of two value_map
    # rules on one column would fall back to the order their groups happened
    # to arrive in, which is a second thing a reviewer's typing could move.
    # rule_id is itself content-derived, so this order is a function of the
    # rules' content and of nothing else.
    rules.sort(
        key=lambda r: (
            r["from"]["cohort_id"],
            r["from"]["dataset_id"],
            r["from"]["column"],
            r["kind"],
            r["rule_id"],
        )
    )

    os.makedirs(os.path.dirname(args.out_ruleset) or ".", exist_ok=True)
    with open(args.out_ruleset, "w", encoding="utf-8") as fh:
        json.dump(rules, fh, indent=2)
        fh.write("\n")

    return 0


if __name__ == "__main__":
    sys.exit(main())
