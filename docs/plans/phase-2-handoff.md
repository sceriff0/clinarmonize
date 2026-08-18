# Phase 2 handoff — §6.1 map, and the invariant trigger that fired

Covers build-order item 8 of §0.7, partially: `s6-1` in full. `s6-2` and
`s6-3` are not started.

**Two blocking caveats, stated up front.** Nothing here is verified under a
container (unchanged from phase 1, and for the same reason). And §10.1's
widening trigger has FIRED without the widening being wired — see "The
invariant, and the one thing this change owes it", which is the first task of
the next session and takes precedence over §6.2.

## State

* Branch `main`, HEAD `854fd35`. Still **not pushed**; no PR has ever been
  opened.
* **Suite: 53/53 green** under `-profile test` on the host (49 at the end of
  phase 1). The four new tests are `tests/map_concepts.nf.test`.

| §0.9 stage | state | entry point |
|---|---|---|
| §1 ingest | built (phase 0) | `subworkflows/local/ingest/` |
| §2 profile | built (phase 0) | `modules/local/profile_columns/` |
| §3 link | built (phase 1) | `link_blocking/`, `link_score/`, `link_resolve/` |
| §4 propose | built (phase 0) | `propose_candidates/`, `propose_channels/`, `propose_ledger/` |
| §5 confirm | built (phase 0) | `confirm_ledger/`, `compile_rules/` |
| §6 map | **§6.1 built this phase**; §6.2, §6.3 not | `modules/local/map_concepts/` |
| §7–§9 derive/coverage/emit | not built — §0.7 items 9–12 | — |
| §10.1 invariant harness | built (phase 0); **scope must widen, see below** | `permute_outcome/`, `invariant_report/` |
| §10.2 fixtures | built (phase 1) | `conf/test.config`, `conf/test_full.config` |

`implementedStages()` now holds `ingest, profile, link, propose, confirm,
map`. It is a contiguous prefix again and **that is still a coincidence, not
a repair** — the gate checks the requested stage's own membership rather than
walking the graph, because §7–§9 are unbuilt and the situation recurs the
moment any later stage is built out of graph order. Do not "fix" it into a
prefix walk. (This is the third handoff to say so.)

## §6.1 — what map proves

The card's done-when, literally: every row of `mapped/measurement.parquet`
carries a non-null `measurement_source_concept_id`, and
`mapped/_unmapped.parquet` exists even when empty. Asserted non-vacuously —
an empty table would also report zero nulls, so the test asserts rows exist
first.

The fixture is §4.1's generic pack plus §5.2's already-green confirmed
ledger (`SEX_CODE -> sex`, person, concept 100; `WEIGHT_KG -> weight`,
measurement, concept 200), because **`-profile test` cannot reach `map`**:
§5.1's human gate refuses to pass `confirm` without a real
`--confirmed_ledger` under every profile, and `map` sits after `confirm`.
The card's own done-when command (`-profile test,docker --stop_after map`)
therefore cannot run as written, and will not until a confirmed ledger for
the Eunomia fixture exists.

### Three decisions the cards do not settle between them

Each is argued at length in `bin/map_concepts.py`'s docstring; the summary
here is so that a reader knows to go look.

- **The "pinned vocabulary" resolves under Ruling R14**, exactly as it did
  for §4.1: map against the pack's declared variables and `concept_id`s,
  record `pack.vocabulary` as the release id, never resolve it. If the
  proposer and the mapper disagreed about what a concept id denotes, a rule
  confirmed against one would be applied against the other.

- **`<domain>_source_concept_id` is 0**, OMOP's designated "no matching
  concept" (`assets/schema_pack.json` already says so in its own
  `concept_id` description). The nogo forbids inventing a concept for an
  unmapped *value*; the done-when simultaneously forbids leaving the column
  null. With no resolvable vocabulary there is no pre-translation concept to
  look up, and 0 says that truthfully. **This is the one lookup that changes
  the day a vocabulary is vendored.**

- **`person_id` stays VARCHAR against the Contract's `int`.** §3.3 made it a
  content hash of the cluster's sorted membership for the reason §5.2's
  `rule_id` is one. Re-typing it would mean re-deriving identity outside §3
  or numbering clusters — the trap §3.3 already refused.

Also: **`value_as_number` is emitted though this card's Contract snippet
omits it.** §6.2's IN slot is this file's output with "same rows,
value_as_number converted", and a measurement row carrying no measured value
is not a measurement. It is the numeric parse of the verbatim source value,
NULL where it does not parse. No unit conversion (§6.2) and no value
vocabulary (§6.3) happen here.

### What `_unmapped.parquet` counts, and what it does not

A source column with **no confirmed rule** has no standard concept by
definition, and its values go there — recorded as distinct values with an
occurrence count, not one row per occurrence (a 100M-row table with one
unmapped column would otherwise write a 100M-row file).

`--max_unmapped_frac` is counted over **non-null source values in every
admitted column**. A null cell is excluded from both sides: it is not "a
source value with no standard concept", and counting it would make the gate
fire on sparsity rather than on coverage.

**This has a consequence nobody has hit yet.** The gate measures whether the
confirmed ruleset covers the data in front of it, which is close to what §8
coverage will report. The fixture is deliberately half-unmapped (two of four
columns), so `-profile test`-shaped data with a partial ledger will trip the
0.2 default. That is the intended behaviour, but §8 should be built knowing
this gate already exists and where its denominator sits.

**The outcome-flagged variable's column is always unmapped**, on every run,
and not by a check in `bin/map_concepts.py`: §4.1 refuses to propose it, so
§5 cannot confirm it, so no rule for it can reach §6. Nothing in the mapper
reads the outcome flag or branches on it — there is no decision here that
*could* be a function of the outcome, which is stronger than checking for
one.

## The invariant, and the one thing this change owes it

**Read `docs/adr/0004-invariant-scope-widens-to-map.md` before doing anything
else.**

ADR-003's soundness rests on every post-`propose` stage consuming only the
confirmed ledger. §6.1 breaks that condition: it joins `link/links.parquet`
for `person_id`, which is a statistic computed jointly across records and the
first value to reach a published artefact without passing through
`ledger.proposed.yaml`. Both prior handoffs named this exact trigger and said
to widen §10.1 **in the same change**.

The widening is **specified and not wired.** ADR-004 records the design in
implementable detail, including the part that is not obvious:

> §5 is a human gate and cannot be re-run per replicate. A permuted replicate
> produces a different proposed ledger, so `CONFIRM_LEDGER` would reject it as
> stale — correctly. So a map-scoped replicate holds the rules fixed at the
> **baseline's ruleset** and re-runs profile → link → map on the permuted
> bytes. What varies is what flows through linkage into mapping, which is
> exactly the newly-exposed surface.

and that the artefact hashed is a **canonical content digest** of `mapped/`
(including `_unmapped.parquet`), not the parquet bytes — parquet embeds
writer metadata, and a byte digest makes the harness sensitive to the encoder
rather than the data, the same mistake §4.3's rounding exists to prevent.

Why it was recorded rather than rushed: it is four new aliased process
invocations, a per-replicate grouping of the link tables, a change to
`INVARIANT_REPORT`'s input contract, and a **new confirmed-ledger fixture for
the invariant fixture whose `proposed_hash` has to be derived empirically**.
§10.1's own nogo is "never exclude a channel from the harness to make it
pass", and a harness rushed into a weakened state is worse than a narrow one
that is honest about being narrow.

Until it lands, `--invariant_scope map` **stays refused at run time**, so no
run can report `[SUCCESS]` having measured a scope it did not run. Expect the
widened test to be slow: the 100-permutation test is already 225s with only
profile + propose, and link is roughly 7s per replicate on the fixture.

### A safety property this change silently deleted, now made explicit

`tests/invariant.nf.test`'s last test asserts that a seeded run aimed **past**
its own `invariant_scope` is refused. That property was being enforced
*incidentally* — by `map` being unimplemented, so the stage gate caught it on
the way past. **Building §6.1 made `map` implemented and the refusal
disappeared**: `--permute_outcome_seed 1..100 --stop_after map` ran all the
way through the mapper while `INVARIANT_REPORT` still measured only
`ledger.proposed.yaml`, and reported `[SUCCESS]`. A reader who asked for
`--stop_after map` and got a green invariant report would reasonably have
concluded the mapper was measured.

`workflows/harmonize.nf` now refuses scope-overrun **explicitly**, and the
test asserts the message says `runs PAST` and does *not* say `not
implemented` — so it can no longer pass for the incidental reason. This is
worth internalising as a pattern: **a gate test that names an unbuilt stage
is testing the build state, not the gate.** Two such tests have now been
found this way (`tests/ingest.nf.test`'s, which moved `map` → `derive`, and
this one).

## ADRs now exist

`docs/adr/` was created this phase because **ADR-003 was cited in three code
sites and existed nowhere** — not in git, not in the gitignored
`.superpowers/` ledger. A comment reading "requiring a superseding ADR"
pointed at nothing anyone could open, which is §9.2's own failure mode
(a reference recorded without the thing it resolves to). ADR-003 is written
retrospectively from what the code already enforces and records no new
decision. ADR-001/002 were not found and are not reconstructed; numbering
starts at 0003 rather than renumbering, because the three citing code sites
are the whole reason the directory exists.

Rulings (R1..R31) remain a separate, finer-grained phase-0 record in
`.superpowers/`. Where one is load-bearing for code in git — R14, the pinned
vocabulary — it is now **restated in full** in the file that depends on it
rather than cited by number.

## Traps found while building

- **`CREATE VIEW` cannot be prepared in duckdb.** A parameterised
  `read_csv(?)` inside a `CREATE OR REPLACE VIEW` raises `Binder Error:
  Unexpected prepared parameter`. `bin/link_blocking.py` already solved this
  by `con.register()`-ing a relation and creating the view over it; §6.1 does
  the same.

- **`hash()` is salted per process.** A view name built from
  `abs(hash((cohort_id, dataset_id)))` changes between runs, so any error
  message quoting one is unreproducible. `_view_key()` builds the name from
  the ids themselves instead.

- **Two record_id constructions already exist and are assumed equal.**
  `bin/link_blocking.py` builds `<dataset>#<n>` with SQL `row_number() OVER
  ()`; `bin/link_score.py` rebuilds it with Python
  `enumerate(rel.fetchall(), start=1)`. They agree **only because duckdb's
  `preserve_insertion_order` defaults to true**. §6.1 joins on those ids, so
  it now asserts a cardinality guard: every source row must find a person, or
  the run dies naming the drift. A silent mismatch would attach the wrong
  person to every row rather than merely dropping some — this is a §3
  surface, and the guard is the cheapest honest response to it from §6.

- **`tools/parquet_roundtrip_probe.py` needed widening for TIMESTAMP**, as
  its own header instructed: `<domain>_datetime` is the first one any stage
  writes. The fixture covers a real datetime, a NULL one (today's common
  case, since no fixture column carries a datetime), microsecond precision,
  and a pre-epoch value. Sabotage-checked at **one microsecond** — the probe
  goes red. Still only run 1.5.5 → 1.5.5; the cross-version claim is
  unproven, see below.

## What is not done

**F2 is still open, and still could not be closed on this machine.** Docker
runs, but `docker run --platform linux/amd64 alpine uname -m` still never
returns — re-confirmed this session at a 45s timeout. The pinned image is
amd64-only. Every result above comes from a host environment pinned to the
container's versions (duckdb 1.5.5, pyyaml 6.0.2), **without container
isolation**. Global Constraint 6 is undemonstrated for a third phase running.

**First action for the next session, on the cluster:**

```
sbatch tools/verify_clinarmonize.sh
```

The harness globs `modules/local/*/environment.yml` and picks up
`map_concepts/`'s copy automatically (thirteen now, not twelve), and section
6d runs the newly widened probe — including the TIMESTAMP fixtures — before
any test.

**Also not done, and deliberately:**

- **§10.1's widening.** The single most important item; see above and
  ADR-004. Ahead of §6.2 in priority.
- **§6.2 and §6.3 are untouched.** Note two things they will need that do not
  exist yet: `assets/schema_pack.json` has **no `plausible_range`**, which
  §6.2's post-condition requires, so §6.2 extends the pack schema; and
  `assets/ucum_factors.yaml` (the `unit_conversion_table` default) does not
  exist. §6.3's alluvial plot inherits §3.3's precedent — hand-written PNG,
  because §0.8 pins the image to duckdb + PyYAML and adding matplotlib means
  re-pinning thirteen module copies.
- **`cdm_version` is inert.** Validated against its enum and recorded;
  nothing branches on it, because across the five-table subset this stage
  emits the generic shape is identical in 5.3 and 5.4. Said out loud in the
  script rather than faked.
- **`docs/output.md` is still nf-core template boilerplate** (FastQC,
  MultiQC). It documents neither `mapped/` nor `link/` nor `rules/` — a
  pre-existing gap §6.1 did not introduce and did not widen.
- **`tests/map_concepts.nf.test` does not snapshot `versions.yml`**, so it
  adds a twelfth `nf_test_content` lint failure to the eleven already behind
  the nf-core/tools crash. Same as every other test in `tests/` except
  `default.nf.test`.
- **The `.subscribe { error(...) }` gate** carried forward from phase 0 is
  unchanged. §6 gained no such pattern.

## Known-red, already diagnosed — do not re-investigate

Unchanged from phase 1: `.github/workflows/linting.yml` fails on the first PR
because nf-core/tools 4.1.0 corrupts `array` and `object` schema defaults
(`docs/upstream/nf-core-tools-array-defaults.md`, drafted and **not filed** —
filing is the maintainer's call). 25-plus ordinary lint failures sit behind
that crash.

**One thing to know when editing `nextflow_schema.json`:** it is
hand-formatted with inline arrays and literal UTF-8 `§`. Round-tripping it
through `json.dump` reformats the entire file (227 insertions for a
four-property addition) and escapes every `§`. Insert as text.

## Housekeeping

* `.superpowers/` is gitignored and holds phase 0's ledger and rulings.
  Nothing from phase 1 or phase 2 was written there.
* Commit messages use gitmoji `:shortcode:` prefixes.
* Do not push, open a PR, or file an upstream issue without being asked.
