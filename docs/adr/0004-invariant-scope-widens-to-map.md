# ADR-004 — The invariant's trigger condition has fired: §6 consumes §3

* Status: **accepted; implementation outstanding** — `--invariant_scope map`
  is still refused at run time until the wiring below lands
* Date: 2026-08-18
* Section: §10.1, §0, §6.1
* Supersedes in part: [ADR-003](0003-invariant-scope-is-the-proposer.md)

## Context

ADR-003 scoped the harness to `ledger.proposed.yaml`, and its soundness rests
on one condition:

> Every stage after `propose` is outside the measurement. As long as those
> stages only consume the confirmed ledger, that is sound: the ledger is
> inside the scope, so anything derived purely from it inherits the property.

Both handoffs carry the standing warning that names when that stops holding.
`docs/plans/phase-1-handoff.md`:

> §3 *does* compute joint statistics across datasets — m, u and match
> weights are precisely that. **The moment §6 consumes `links.parquet`, the
> invariant's scope has to widen with it, in that change and not after it.**

**That moment is now.** §6.1 (`bin/map_concepts.py`, this change) joins every
source row to `link/links.parquet` to obtain its `person_id`. A `person_id`
is a content hash of a cluster's sorted membership — the output of blocking,
Fellegi–Sunter scoring and thresholding, i.e. a statistic computed jointly
across records. It is the first value in the pipeline that reaches a
published artefact without having passed through `ledger.proposed.yaml`, so
it is the first value ADR-003's soundness argument does not cover.

Nothing here claims a leak exists. §3 refuses, by the pack's `outcome: true`
flag and never by a column name, to block on or compare an outcome-flagged
column, and it refuses by killing the run rather than dropping the rule
(`bin/link_blocking.py`, `bin/link_score.py`). §6.1 reads no outcome flag at
all. The argument for widening is §10.1's own Why: *every other check in
this pipeline could pass with the invariant violated*. A structural
guarantee is precisely the kind of claim the harness exists to test rather
than assert.

## Decision

`invariant_scope = 'map'` holds the invariant's claim over the **mapped
tables** (`mapped/*.parquet` plus `mapped/_unmapped.parquet`), and is the
composition of the `propose` scope with the §3 → §6 edge.

### What a map-scoped replicate must run

The permuted replicate cannot travel the whole graph, and the reason is
worth stating because it is not a shortcut:

**§5 is a human gate and cannot be re-run per replicate.** `ledger.confirmed.yaml`
is authored by a person and carries a `proposed_hash` keyed to the baseline's
`ledger.proposed.yaml`. A permuted replicate produces a different proposed
ledger, so `CONFIRM_LEDGER` would reject it as stale — correctly. There is no
way to confirm 100 permuted replicates and no honest one to fake it.

So a map-scoped replicate holds the **rules fixed at the baseline's ruleset**
and re-runs everything downstream of the permutation that is not the human
gate:

```
for seed in 1..N:
    permute outcome column WITHIN each cohort        # unchanged, §10.1
    profile the permuted tables                      # PROFILE_COLUMNS_PERMUTED, exists
    link the permuted tables                         # LINK_*_PERMUTED, per replicate — NEW
    map, using the BASELINE rules/ruleset.json       # MAP_CONCEPTS_PERMUTED — NEW
    collect a canonical content digest of mapped/    # NEW
assert len(set(digests)) == 1
```

Holding the ruleset fixed is what makes this measure the right thing. The
proposer's own outcome-blindness is already the `propose` scope's claim;
re-deriving rules per replicate would re-measure that and obscure the edge
this scope exists for. What varies here is the permuted bytes flowing through
**linkage** and into **mapping**, which is exactly the newly-exposed surface.

### What is hashed

A **canonical content digest**, not the parquet bytes. Parquet embeds writer
metadata, and a byte digest would make the harness sensitive to the encoder
rather than to the data — the same class of mistake §4.3's rounding and top-k
truncation exist to prevent for the ledger ("a float's last bit cannot change
the hash"). The digest is computed over the rows read back through duckdb,
ordered canonically, with floats rounded at `ledger_float_precision`.

`mapped/_unmapped.parquet` is inside the digest. A leak that moved a value
between a domain table and the unmapped set would otherwise be invisible.

## Status: why this is recorded before it is wired

The wiring is four new aliased process invocations, a per-replicate grouping
of the link tables, and a change to what `INVARIANT_REPORT` consumes — a
change to the single most load-bearing test in the repository. §10.1's own
nogo is "never exclude a channel from the harness to make it pass", and a
harness rushed into a weakened state is a worse outcome than a narrow one
that is honest about being narrow.

Until it lands:

* `--invariant_scope map` is **still refused at run time**, with the
  ADR-003 message. Nothing can report `[SUCCESS]` having measured a scope it
  did not run.
* The `propose` scope is unchanged and still green.
* This is the **first task of the next session**, ahead of §6.2 and §6.3.

The trigger has fired and is written down where a reader can find it. That is
the difference between a known gap and a forgotten one.
