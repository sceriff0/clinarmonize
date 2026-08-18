# Phase 3 handoff — §10.1 widened to `map`, and the §3 defect it caught

Covers the item the phase-2 handoff named as **the first task of the next
session, ahead of §6.2**: ADR-004's wiring. `--invariant_scope map` now runs,
is held to 100 permutations, and has been observed going **red** under a
deliberate §3 leak. §6.2 and §6.3 are still not started.

**One blocking caveat, stated up front.** F2 was re-run on the cluster before
this phase began and came back clean — but that was against phase 2's tree.
This change adds a **fourteenth** `modules/local/*/environment.yml` and a new
`bin/` script, so `sbatch tools/verify_clinarmonize.sh` has to run again
before Global Constraint 6 can be claimed for this commit. Nothing here has
been executed under a container.

## State

* Branch `main`, HEAD `35044c2`. Still **not pushed**; no PR has ever been
  opened.
* **Suite: 56/56 green** under `-profile test` on the host, 1010s (53 at the
  end of phase 2). The three new tests are two in `tests/invariant.nf.test`
  and one in `tests/invariant_leak_control.nf.test`.
* Local runs need **`export NXF_VER=26.04.6`**. `nextflow.config` pins
  `nf-schema@2.8.0`, which requires Nextflow >= 26.04.0, and a bare
  `nextflow` on this machine resolves 25.04.7 and fails every test with
  *"Failed requirement - Plugin nf-schema@2.8.0 requires Nextflow version
  >=26.04.0"*. `tools/verify_clinarmonize.sh` already exports it (and refuses
  a `25.*` override); nothing outside that script did.

| §0.9 stage | state | entry point |
|---|---|---|
| §1 ingest | built (phase 0) | `subworkflows/local/ingest/` |
| §2 profile | built (phase 0) | `modules/local/profile_columns/` |
| §3 link | built (phase 1); **record_id fixed this phase** | `link_blocking/`, `link_score/`, `link_resolve/` |
| §4 propose | built (phase 0) | `propose_candidates/`, `propose_channels/`, `propose_ledger/` |
| §5 confirm | built (phase 0) | `confirm_ledger/`, `compile_rules/` |
| §6 map | §6.1 built (phase 2); §6.2, §6.3 not | `modules/local/map_concepts/` |
| §7–§9 derive/coverage/emit | not built — §0.7 items 9–12 | — |
| §10.1 invariant harness | **scope widened to `map` this phase** | `permute_outcome/`, `artefact_digest/`, `invariant_report/` |
| §10.2 fixtures | built (phase 1) | `conf/test.config`, `conf/test_full.config` |

`implementedStages()` is unchanged and still checks the requested stage's own
membership rather than walking the graph. Do not "fix" it into a prefix walk.
(This is the fourth handoff to say so.)

## What `--invariant_scope map` is

A scope now names a **list of artefacts**, and a replicate's hash is the
composite of all of them:

```
propose  ->  [ledger.proposed.yaml]           composite == the ledger hash itself
map      ->  [ledger.proposed.yaml, mapped/]  composite == sha256 over both
```

The list lives in `bin/invariant_report.py`'s `SCOPE_ARTEFACTS` and the set of
scopes the workflow will actually RUN lives in `implementedScopes` in
`workflows/harmonize.nf`. **Those two must agree**: one decides which stages
run per replicate, the other decides which artefacts are hashed, and a scope
wired in one but not the other is exactly a `[SUCCESS]` over a claim nothing
measured.

`map` is a **composition**, not a replacement. The ledger is still hashed per
replicate; §6 leaking would not excuse §4 leaking. `n_distinct_hashes` counts
the composite, so the card's one-line assertion still reads literally, and the
`propose` scope's reported numbers are byte-identical to what they were before
this change.

### What a map-scoped replicate runs

```
permute the outcome column WITHIN each cohort   (unchanged)
profile the permuted tables                     PROFILE_COLUMNS_PERMUTED
link    the permuted tables                     LINK_{BLOCKING,SCORE,RESOLVE}_PERMUTED
map     against the BASELINE's ruleset          MAP_CONCEPTS_PERMUTED
digest  mapped/ canonically                     ARTEFACT_DIGEST
```

§5 is **not** aliased and never re-runs. It is a human gate:
`ledger.confirmed.yaml` carries a `proposed_hash` keyed to the baseline's
`ledger.proposed.yaml`, and a permuted replicate produces a different proposed
ledger that `CONFIRM_LEDGER` would reject as stale — correctly. So the rules
are held fixed at the baseline's ruleset and what varies is exactly what flows
through linkage into mapping.

To make that chaining safe, `LINK_BLOCKING`, `LINK_SCORE`, `LINK_RESOLVE` and
`MAP_CONCEPTS` now carry a `replicate` key through their input and output
tuples, the way `PROPOSE_*` already did. Without it the three link processes
could only be chained by channel POSITION, and a queue channel's order is task
completion order — replicate 7's pairs would be scored against replicate 3's
records the first time two tasks finished out of order.

### The digest, and why it is not `sha256(file)`

`bin/artefact_digest.py`. ADR-004 is explicit: parquet embeds writer metadata,
so a byte digest measures the encoder. It would go red on a duckdb upgrade, a
compression-codec change, or a row-group boundary moving, and every one of
those reads as `leak`. A harness that cries leak for reasons that are not
leaks gets muted.

So the digest is over the data, read back through duckdb: per file in filename
order, columns in declared order **with their types**, floats rendered at
`ledger_float_precision`, rows sorted by their own canonical encoding (so row
order is not part of it, and duplicates survive — a multiset, not a set).
Fields are US-joined and **tagged** `V<value>` / `N` before joining, which is
what makes the row encoding injective: untagged, `("a", NULL)` and
`(NULL, "a")` collide, and a leak that moved a value between columns would
hash identically.

Verified by hand while building: bumping ONE occurrence count in
`_unmapped.parquet` moves the artefact digest; reordering rows and perturbing
a float's last bit do not.

`mapped/_unmapped.parquet` is inside the digest, per ADR-004 — a leak moving a
value between a domain table and the unmapped set would otherwise be
invisible.

### The baseline is inside the comparison

The replicate set hashed is the seeds **plus** the unpermuted baseline (key
`null`). This was already true of the propose scope, incidentally; it is now
explicit and enforced by `missing_ledgers` / `missing_mapped`. Replicates that
agree with each other but not with the run on the real data have shown the
leak was deterministic, not that there was none.

### Stopping SHORT of the scope is reported, not refused

Overrun is refused outright — the later stages would run and publish under a
verdict that never measured them, which is phase 2's finding. Stopping short
is different: it is a legitimate request (the propose half genuinely runs and
its numbers are real), and the only danger is being *read* as a proof of the
whole scope. So the report names it: verdict `no-mapped-artefact`, with the
unmeasured replicates listed. Refusing it at the gate would also have made the
existing `--stop_after profile` tests illegal, and those measure permutation
mechanics rather than any proof.

## The §3 defect this caught

**`record_id` was `'<dataset_id>#<row_number>'` and is not unique.** §1.1
rejects only a duplicate `(cohort_id, dataset_id)` PAIR, so two cohorts each
carrying a `clinical` dataset is a legal samplesheet — and the §10.1 fixture
is exactly that. `bin/link_score.py`'s `records` is a flat dict keyed on that
id, so the last table read answered for the first:

```
record_id 'clinical#1' is claimed by BOTH:
  COHORT_A/clinical row 1: given=AAA family=LLLLLL birth_date=1940-01-01
  COHORT_B/clinical row 1: given=HHH family=SSSSSS birth_date=1940-01-01
```

which scored **8 of 20** true pairs instead of 20, with `birth_date` agreeing
(the two cohorts share the value set) while `given` and `family` disagreed.
`bin/link_resolve.py` had it worse — `record_ids` seeds the union-find and
`cohort_of` is keyed on the same id, so a collision both shrinks the
collapse-ratio denominator and can put two cohorts' rows in one cluster.
`bin/map_concepts.py`'s `record_person` is a third flat map on it.

The id is now `'<cohort_id>#<dataset_id>#<row_number>'` in all four places
that build it. The format is **not contractual** — §3.3's OUT slot names
`[source_row_id]` and no card pins its shape — so this is an implementation
fix, not a contract change. The one test that pinned literals
(`tests/link_score.nf.test`) now spells them `COHORT_LINK#encounter#1`.

**Nothing downstream of §3 had ever consumed a multi-cohort linkage before
this change.** Every prior link/map fixture is single-cohort, and the
two-cohort §10.1 fixture never reached `link`. That is the argument for
widening the scope, arriving as evidence: *every other check in this pipeline
could pass with the invariant violated.*

## The fixture had to be made able to link at all

None of the invariant cohort set's columns was named by any blocking rule, so
every record was a singleton cluster and `person_id` was a hash of one record
id. A map-scoped run over that fixture would have gone green **while measuring
nothing** — `person_id` cannot be a function of the outcome if no linkage
decision is ever made. Same family of vacuity as the report's own
`no-outcome-column` and `no-op-permutation` verdicts.

`tests/fixtures/make_fixtures.py` now gives every invariant table the link
fixture's own five identity fields (`block_key, given, middle, family,
birth_date`), in the same lower case, **so `link_blocking_rules.yaml` and
`link_comparisons.yaml` apply unchanged** — a re-cased second copy would be
two rule sets to keep in step, and the one that drifted would drift silently.
Built to make the model do real work:

* COHORT_A/clinical row *i* and COHORT_A/treatment row *i* are one person —
  the true-match class.
* `middle` is missing on some rows and different on others, so all three
  comparison levels appear in one candidate set.
* COHORT_A/treatment carries two EXTRA records sharing a `block_key` with a
  clinical record and agreeing on nothing else — the non-match class. Without
  it the two-class mixture has one class and EM's `m` collapses toward 1.
* COHORT_B has one dataset and distinct keys, so its twelve records stay
  singletons — both shapes §6.1's `person_id` join has to handle.

Result: 54 records → 34 persons, 20 linked pairs, `collapse_ratio` 0.63.
`tests/invariant.nf.test` asserts `n_persons < n_in` and `n_linked_pairs > 0`
directly, so the fixture cannot quietly regress to singletons.

**`test_data/` is gitignored.** These fixtures do not appear in the diff;
`tests/fixtures/make_fixtures.py` is the tracked source and must be re-run
(`python3 tests/fixtures/make_fixtures.py`) before the suite, as every test
header already says.

### The confirmed-ledger fixture, and the pack trap behind it

`confirm_ledger_invariant.yaml` is new, and its `proposed_hash` is derived
empirically. **It is derived against `assets/packs/omop_cdm53.yaml`, not
`assets/packs/minimal.yaml`** — `nf-test.config` sets `profile = "test"` and
`conf/test.config` sets `concept_pack` to the OMOP pack, so *every test in
`tests/` that does not override `concept_pack` proposes against that pack,
whatever the pipeline default is*. The first version of this fixture was
derived against `minimal.yaml`, matched four of its six rows to nothing, and
`CONFIRM_LEDGER` rejected the file — correctly, and that is how the trap was
found. Worth remembering for §6.2/§6.3's own fixtures.

## The negative control now covers both scopes

`tests/invariant_leak_control.nf.test` has two tests, and the second is not
redundant:

| sabotage | artefact that moves | scope that can see it |
|---|---|---|
| §4.2 `EvidenceChannel_score` (phase 0) | `ledger.proposed.yaml` | propose, map |
| §3.2 `PairScorer_score` (**new**) | `mapped/` only | **map only** |

The §3.2 sabotage makes a pair's match weight `±40` drawn from the two
records' own outcome values, so which pairs cross `--match_threshold` — and
therefore every `person_id`, and therefore every mapped row — is a genuine
function of the outcome. `PROPOSE_CANDIDATES` consumes profiles, not links, so
**the ledger does not move**, and the test asserts exactly that split:

```groovy
assert report.n_distinct_hashes_per_artefact.ledger == 1
assert report.n_distinct_hashes_per_artefact.mapped  > 1
assert report.verdict == 'leak'
```

A propose-scoped run of that same sabotage would report `proof`. That is the
observation that makes the widening worth having, and it is the reason the
control was written rather than the wiring simply being trusted: **widening a
harness and never observing the new half go red is the same as not widening
it.**

Two construction notes. The perturbation is per-PAIR, not a constant shift: a
constant moves every weight together and can leave the thresholding decisions
— the only thing that reaches `person_id` — unchanged, which is the same false
negative the phase-0 control hit on its first attempt. And it is large enough
to cross the threshold, because thresholding is a step function and a nudge
too small to move a weight past `--match_threshold` changes no cluster at all.

## Cost

100 map-scoped replicates is ~1120 tasks, measured at **345s** locally,
against ~400 tasks and ~227s for the propose scope. Full suite: **1010s for
56 tests**, up from 53 tests at the end of phase 2 — budget ~17 minutes.

The committed test runs the full hundred rather than ten.
`bin/invariant_report.py` names `insufficient-permutations` as its own verdict
because shortening the range is the cheapest way to weaken this test, and a
map scope certified on ten seeds beside a propose scope certified on a hundred
would be a weaker claim wearing the same word, `proof`. (The leak control runs
ten, and says why: it observes a hash SPLIT, which one differing replicate
already establishes.)

## What is not done

**F2 / Global Constraint 6, for this tree.** See the caveat at the top. The
harness picks up `modules/local/artefact_digest/environment.yml` automatically
(fourteen copies now, not thirteen; it is byte-identical to
`containers/duckdb-pyyaml/environment.yml`, checked). Docker on the phase-2
machine still hangs on `--platform linux/amd64`; nothing about that changed.

**The permuted tables are passed by absolute path, not staged.** The
per-replicate `tables_json` names the permuted CSVs by their path inside
`PERMUTE_OUTCOME`'s work directory, exactly as the baseline's `tables_json`
names the samplesheet's input paths — the identical code path the aliasing
exists to preserve. Under `singularity.autoMounts` (what the cluster uses)
this resolves; under `-profile docker`, an unmounted path would not, and that
is **a pre-existing property of §3/§6 that this change extends to a second set
of files rather than a new defect**. The honest fix is to declare the tables
as staged `path` inputs on all four processes and build the JSON from staged
names — which also fixes the baseline — but it needs a decision about
basename collisions between two datasets, and it is a §3 contract change, not
§10.1 wiring. Deliberately not smuggled into this change.

**§6.2 and §6.3 are untouched.** Unchanged from the phase-2 handoff, and its
two warnings still stand: `assets/schema_pack.json` has no `plausible_range`,
which §6.2's post-condition requires, and `assets/ucum_factors.yaml` does not
exist. §6.3's alluvial plot inherits §3.3's hand-written-PNG precedent.

**`--invariant_scope all` is still refused**, and the refusal message now
names the reason rather than the phase: §8.3's standardized-difference forest
legitimately reads outcome variables for REPORTING, so an end-to-end scope
would go red honestly and then get relaxed — and a harness that has been
relaxed once is a harness nobody trusts again.

**`docs/output.md` is still nf-core template boilerplate**, now also not
documenting `mapped/`, `link/`, `rules/` — unchanged and not widened.

**`CHANGELOG.md` is still the untouched nf-core template.** No phase has
maintained it; this one did not start.

**The new tests do not snapshot `versions.yml`**, adding to the
`nf_test_content` lint failures already sitting behind the nf-core/tools
crash.

## Known-red, already diagnosed — do not re-investigate

Unchanged: `.github/workflows/linting.yml` fails on the first PR because
nf-core/tools 4.1.0 corrupts `array` and `object` schema defaults
(`docs/upstream/nf-core-tools-array-defaults.md`, drafted and **not filed** —
filing is the maintainer's call). 25-plus ordinary lint failures sit behind
that crash.

`nextflow_schema.json` is hand-formatted with inline arrays and literal UTF-8
`§`. This phase edited exactly one `help_text` string **as text** and the diff
is one line; round-tripping through `json.dump` would have reformatted 227.

## Housekeeping

* `.superpowers/` is gitignored and holds phase 0's ledger and rulings.
  Nothing from phases 1–3 was written there.
* Commit messages use gitmoji `:shortcode:` prefixes.
* Do not push, open a PR, or file an upstream issue without being asked.
