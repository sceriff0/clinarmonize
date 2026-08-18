# ADR-005 — The confirmed ledger gains `value_map`, because §6.3 cannot be a §6 change

* Status: **accepted and implemented** — `tests/value_map.nf.test` holds it to
  eight cases, and `tests/rules_stable.nf.test` still holds §5.2's own
  done-when unchanged.
* Date: 2026-08-18
* Section: §5.1, §5.2, §6.3
* Amends: the §5 contract every phase since phase 0 has treated as settled

## Context

§6.3's IN slot is "mapped rows with categorical values + **value-map
rules**", and no such rule could exist. `bin/compile_rules.py` emitted
`kind: "column_map"` and nothing else, and its docstring named the reason
rather than leaving it to be rediscovered:

> `value_map` needs a value-level mapping table nothing in
> `ledger.confirmed.yaml`'s Contract offers

§5.2's Contract *does* enumerate `column_map|value_map|unit_convert|derive`
as the enum, so the kind was anticipated. What was missing is the human
decision that produces one. §5.1's ledger Contract carries `cohort_id,
dataset_id, column, decision, variable, concept_id, unit_in, confirmed_by,
rationale, proposed_hash` — every field about a COLUMN, and nowhere to say
*"these four source grades become these two"*.

There were three ways out and two of them are wrong.

**Infer the collapse from the observed value set.** §2.1 already records
each column's distinct values, and the pack declares `domain_values`; a
string-similarity pass over the two would produce groupings that are right
most of the time. That is §5.1's Why, deleted in one line: *"a pipeline that
fills that gap with its own top-ranked guess has quietly deleted the only
review step in the design while still emitting a file called a ledger."* A
value collapse is a harmonization decision — §6.3's own Why is that four
grades collapsing into two is *"sometimes correct and sometimes the
destruction of the effect being studied"* — and a decision of that kind
comes from the gate or it does not get made.

**Put the collapse in the concept pack.** The pack is version-controlled,
reviewed, and already declares each variable's legal values, so a
`value_map:` there would be tempting and would need no §5 change at all. It
is wrong because a collapse is not a property of the target vocabulary; it
is a property of the *pairing* of one cohort's recorded grain with that
vocabulary. Two cohorts recording ECOG differently need two different
collapses into one pack, and a pack-level mapping can express neither
without becoming a per-cohort file — which is the ledger, with the review
step removed.

**Extend the ledger.** Which is what this ADR does.

## Decision

A confirmed row may carry an optional `value_map:` list, one entry per
collapse group:

```yaml
- cohort_id: COHORT_A
  dataset_id: clinical
  column: "ECOG_PS"
  decision: accept
  variable: ecog
  concept_id: 400
  confirmed_by: "a.reviewer"
  rationale: "..."
  proposed_hash: "sha256:…"
  value_map:
    - from: ["0", "1"]        # the source values, as written in the table
      to: "0-1"               # the canonical value; must be one the pack declares
      concept_id: 4000        # the VALUE's standard concept (optional)
      rationale: "..."        # required under --require_rationale
```

**The unit is a collapse GROUP, not a value-to-value pair.** §6.3's Contract
fixes the output shape — `{"from": ["0","1"], "to": "0-1", "fan_in": 2}` —
and `fan_in` is `len(from)`. A pair-shaped ledger (`{"0": "0-1", "1":
"0-1"}`) cannot express a fan-in at all: it would have to be recovered by
grouping on the target, which is the same information arranged so that the
one field the card's nogo makes mandatory is derived rather than declared.
The input shape follows the output shape, and does so because the output
shape is the one the card fixed.

**`fan_in` is not stored.** It is `len(from)`, computed where it is written.
A stored fan_in is a second place the width of a collapse is written down,
and two places is one more than can be kept in step.

**`from` is sorted at the gate.** §5.2's done-when is that reordering the
confirmed ledger leaves every `rule_id` unchanged, and a collapse group is a
set: `["1","0"]` and `["0","1"]` are one decision and must compile to one
rule. Sorting in `bin/confirm_ledger.py` makes that structural rather than a
request that reviewers be tidy.

### Where each refusal lives, and why

Four refusals are §5.1's, because each is a question the FILE fails to
answer and none needs a data table in hand:

| refused at the gate | because |
|---|---|
| one value claimed by two groups | which applies is a decision the reviewer did not make; §6.3 would resolve it by CASE-branch order, silently |
| two groups with the same `to` | one collapse written twice — a table with two answers to one question, the shape §6.2 refuses in its factor table |
| `value_map` on a `reject`/`defer` row | a decision that maps nothing, carrying instructions for how to map it |
| `value_map: []` | a review started and not finished; reading it as "no value mapping" is the pipeline supplying the decision |

Two are §6.3's, because both need the pack, which §5.1 never reads (its IN
slot is the two ledgers and nothing else):

| refused in §6.3 | because |
|---|---|
| `to` outside the variable's `domain_values` | the pack IS the target variable set and `domain_values` is its own word for "enumerated legal values"; a canonical value invented at the gate is a target vocabulary nothing declared — the value-level form of §6.2's "never convert to a unit the pack does not declare" |
| a `value_map` on a `derived` variable | §6.3's own Trap. An endpoint is derived from a scan sequence under a criteria version (§7.3), and mapping the source's stated string imports whichever criteria that cohort used, unrecorded |

That second one deserves its own note, because it is the only place in this
repo where one card's Trap is enforced by a check written for another card's
schema. §6.3's Trap names *best response*, and Global Constraint 4 forbids a
clinical term in `bin/` as much as in `modules/`. The pack's schema already
requires a `derivation` for a `derived` variable, and an endpoint is a
derived variable by construction — so refusing the whole domain catches the
class without the code knowing what a response criterion is. It is also
unreachable by accident: §4.1 cannot propose a derived variable (it has no
source column), so §5 cannot confirm one. The check is there for the ledger
that names one deliberately.

### What is written onto the row

`value_as_concept_id` and `value_rule_id`, appended.

`value_as_concept_id` is NULL — never 0 — on a row no value-map rule claims.
0 is OMOP's designated "no matching concept" and is what a rule whose
reviewer supplied no concept id writes (Ruling R14 again: no vocabulary is
vendored and nothing can resolve `"0-1"`). NULL says something different and
weaker: no value mapping applies to this row at all, which is the ordinary
state of every continuous measurement in the run.

`value_rule_id` is there because §5.2's Why is that *"every emitted cell name
the rule that produced it"* and §6.3 writes a cell. It is derivable — the
value sets cannot overlap, §5.1 refuses that — but provenance that has to be
recomputed by re-running the mapping is not provenance.

The canonical value itself is **not** written as a second column. That is
§6.3's own Alternatives table ("emit both raw and canonical columns"),
listed there with its cost: *"widens every domain table and pushes the
decision to every consumer."* The verbatim source value stays where §6.1 put
it, the canonical value lives on the rule the row now names, and
`qc/value_collapse.json` is the join between them.

## Consequences

**Backwards compatible by construction.** `value_map` is optional and absent
from every ledger written before this phase; `bin/confirm_ledger.py` returns
`[]` for a row that declares none. No existing fixture, test or ledger
changed.

**§5.2's collision detector is untouched, and was verified rather than
trusted.** It keys on `(cohort_id, dataset_id, to.variable)` → the set of
`from.column`. A value_map rule and its column_map sibling are compiled from
the SAME decision row, so they carry the same column and add the same single
element to the same set; two collapse groups on one column do likewise. A
value mapping therefore cannot manufacture a collision, and — the half that
matters more — cannot mask one, because a genuine two-column collision is
still two distinct columns in the set.

**`ruleset.json`'s sort key grew.** `(cohort, dataset, column)` was a total
order while one column produced exactly one rule. It no longer is, so `kind`
and `rule_id` are appended — both content-derived, so the written order stays
a function of the rules' content and of nothing else.

**One thing got harder and is worth saying so.** A reviewer confirming a
categorical column now has two jobs at the gate rather than one, and the
second is unbounded: the collapse groups have to cover the column's value
set, and nothing in §5 tells them what that set is. `--unmapped_value_policy
fail` exists for exactly this and is not the default, because a run that
stops on the first unreviewed code is the right behaviour for a release and
the wrong one for the first pass over a new cohort. §2.1's profile already
records every distinct value; a future §5 change could carry them into the
proposed ledger so the reviewer is filling in a list rather than recalling
one. That is a §4.3 contract change and is deliberately not made here.
