# ADR-004 — The invariant's trigger condition has fired: §6 consumes §3

* Status: **accepted and implemented** — `--invariant_scope map` runs, and
  `tests/invariant.nf.test` holds it to 100 permutations. See
  "Status: as wired" at the foot of this file for what landed and what it
  cost.
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

## Status: as wired

The decision above is implemented. What landed, and the two things that were
learned doing it:

**The wiring.** `LINK_BLOCKING`, `LINK_SCORE`, `LINK_RESOLVE` and
`MAP_CONCEPTS` now carry a `replicate` key through their input and output
tuples, the way `PROPOSE_*` already did, and are invoked a second time under
`*_PERMUTED` aliases on the per-replicate permuted tables. A new
`ARTEFACT_DIGEST` process (`bin/artefact_digest.py`) reduces one replicate's
`mapped/` to the canonical digest described above; it is invoked ONCE over a
channel carrying the baseline and every replicate, so no second call site can
drift from the one that digested the run the replicates are compared against.
`INVARIANT_REPORT` takes a second hash list, and a scope now names a LIST of
artefacts whose per-replicate composite is what `n_distinct_hashes` counts —
so `propose`'s numbers are byte-identical to what they were, and the map
scope is a composition rather than a replacement.

**The baseline is inside the comparison.** The replicate set hashed is the
seeds *plus* the unpermuted baseline (key `null`). This was already true of
the propose scope, incidentally; it is now explicit and enforced by
`missing_ledgers` / `missing_mapped`. Replicates that agree with each other
but not with the run on the real data have shown the leak was deterministic,
not that there was none.

**A run that stops SHORT of the scope is reported, not refused.** Overrun is
refused outright — the later stages would run and publish under a verdict
that never measured them. Stopping short is a legitimate request whose only
danger is being *read* as a proof, so the report names it: verdict
`no-mapped-artefact`, with the unmeasured replicates listed.

### What building it found

**A §3 defect the widening was written to catch, caught.** `record_id` was
`'<dataset_id>#<row_number>'`, but §1.1 rejects only a duplicate
`(cohort_id, dataset_id)` PAIR — so two cohorts each carrying a `clinical`
dataset is a legal samplesheet, and `bin/link_score.py`'s flat `records` dict
let the last table read answer for the first. On the two-cohort §10.1 fixture
this scored 8 of 20 true pairs instead of 20, with `birth_date` agreeing (the
cohorts share the value set) while `given` and `family` disagreed. The id is
now `'<cohort_id>#<dataset_id>#<row_number>'` in all four places that build
it. Nothing downstream of §3 had ever consumed a multi-cohort linkage before
this change, which is precisely the argument for widening the scope: *every
other check in this pipeline could pass with the invariant violated.*

**The fixture had to be made able to link at all.** None of the invariant
cohort set's columns was named by any blocking rule, so every record was a
singleton cluster and `person_id` was a hash of one record id. A map-scoped
run over that fixture would have gone green while measuring nothing —
`person_id` cannot be a function of the outcome if no linkage decision is
ever made. `tests/fixtures/make_fixtures.py` now gives the fixture the link
fixture's own five identity fields (so its proven rule files apply
unchanged), a non-match class so EM has two classes to fit, and one
single-dataset cohort so both multi-record clusters and singletons are
exercised. `tests/invariant.nf.test` asserts `n_persons < n_in` directly, so
the fixture cannot quietly regress to singletons.

### What it costs

100 map-scoped replicates is ~1120 tasks and ~345s locally, against ~400
tasks and ~227s for the propose scope. The committed test runs the full
hundred: `bin/invariant_report.py` names `insufficient-permutations` as its
own verdict because shortening the range is the cheapest way to weaken this
test, and a map scope certified on ten seeds beside a propose scope certified
on a hundred would be a weaker claim wearing the same word.
