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

`kind` (phase 0): every compiled rule is "column_map". The Contract's own
worked example shows the OTHER three kinds (value_map, unit_convert, derive)
as enum members, not literal output for this pack -- and none of them is
derivable from a Decision alone without information this seam's signature
does not carry: value_map needs a value-level mapping table nothing in
ledger.confirmed.yaml's Contract offers; unit_convert needs a real
conversion factor, which requires knowing the variable's CANONICAL unit
(the concept pack), a lookup this compiler deliberately does not perform
(see above -- compile([Decision]) -> [Rule], nothing else); derive needs a
formula. `unit_in` (the confirmed source unit, §2.2/§5.1) is still recorded
in `params` -- honestly, as provenance the human resolved, never as a claim
that a conversion factor was computed here. §6 map (not built in this
phase) is where an actual unit conversion, with the pack's canonical unit in
hand, belongs.

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
        to_ = {
            "variable": decision["variable"],
            "concept_id": decision.get("concept_id"),
        }
        kind = "column_map"
        params_ = {"unit_in": decision.get("unit_in")}

        # Content hash of EXACTLY (kind, from, to, params) -- the Trap's own
        # guard. Never a function of this decision's position in the input
        # list, never of wall-clock time.
        canonical = _canonical_json({"kind": kind, "from": from_, "to": to_, "params": params_})
        digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        rule_id = f"{rule_id_prefix}{digest}"

        rules.append(
            {
                "rule_id": rule_id,
                "rule_version": pack_version,
                "kind": kind,
                "from": from_,
                "to": to_,
                "params": params_,
            }
        )

    return rules


def _detect_collisions(rules: list[dict]) -> dict[tuple[str, str, str], set[str]]:
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
    rules.sort(key=lambda r: (r["from"]["cohort_id"], r["from"]["dataset_id"], r["from"]["column"]))

    os.makedirs(os.path.dirname(args.out_ruleset) or ".", exist_ok=True)
    with open(args.out_ruleset, "w", encoding="utf-8") as fh:
        json.dump(rules, fh, indent=2)
        fh.write("\n")

    return 0


if __name__ == "__main__":
    sys.exit(main())
