# Phase 5 handoff — §6.3 value vocabularies, and the §5 contract it had to reopen

Covers the item the phase-4 handoff named as the last of §0.7 item 8: §6.3,
whose blocker was a §5 contract change rather than a §6 one. **§6 is now
complete.** §0.7 item 9 (§7 derive endpoints) is next, and §6.3's Trap and
§7.3's card constrain each other — read both before starting either.

**The headline: `ledger.confirmed.yaml` grew a field.** Every phase since
phase 0 treated §5's Contract as settled and this one could not. The full
argument, the two alternatives that were rejected, and where each of the six
refusals lives are in
[ADR-005](../adr/0005-the-confirmed-ledger-gains-value-map.md). The short
version is that §6.3's IN slot names "value-map rules", `bin/compile_rules.py`
could only ever emit `column_map`, and its own docstring said why: a
`value_map` "needs a value-level mapping table nothing in
`ledger.confirmed.yaml`'s Contract offers". A value collapse is a
harmonization decision, so it comes from the gate or it does not get made.

## State

* Branch `main`, **pushed** to `github.com/sceriff0/clinarmonize` (public).
  §6.3 itself is `db61367`; five later commits are cluster tooling and are
  described under "Cluster tooling, and the bug it uncovered" below. **No PR
  has ever been opened**, and opening one is still what first fires
  `linting.yml` and `nf-test.yml`, including the known-red nf-core/tools crash
  below. Pushing to `main` fires only `build-container.yml`, and only on
  `containers/**` paths.
* **The pipeline runs green on the cluster under Apptainer** — 14/14 tasks
  through `map` on the §6.3 fixture, 2026-08-19, publishing all five
  `qc/` artefacts including both alluvial plots. That proves §6.3 works inside
  the image; it is **not** Global Constraint 6, which is the test SUITE under
  the container.
* **Suite: 69/69 green** under `-profile test` on the host
  (61 at the end of phase 4). The eight new tests are all in
  `tests/value_map.nf.test`.
* **What §6.3 costs, measured rather than estimated.** The map-scoped
  invariant test went **362s → 433s** (~+20%), and the whole of
  `tests/invariant.nf.test` runs in **724s**. The propose-scoped
  100-permutation test is **231s**, unchanged from the ~227s ADR-004
  recorded — §6.3 touches nothing it measures. Per-process, from that run's
  own `meta/trace.csv`: `VALUE_MAP_PERMUTED` is 100 tasks at **0.25s mean,
  25.1s total**, the same order as `MAP_CONCEPTS_PERMUTED` (0.25s) and
  `CONVERT_UNITS_PERMUTED` (0.30s), against `PROFILE_COLUMNS_PERMUTED`'s
  283.6s. It is one of the cheapest processes in the run.
* **Do NOT use a whole-suite wall clock as a baseline on this host.** The
  full-suite run for this phase reported **7874s** — against phase 4's 1075s
  — and none of it is attributable to §6.3. Other `nextflow run` invocations
  were executing concurrently, and the same `tests/invariant.nf.test` that
  reports 724s in isolation was consuming ~125 minutes of that run. Total
  TASK time across all 1256 tasks of the map-scoped test is ~950s; the rest
  is scheduler overhead under contention. Time individual test files, and
  read `meta/trace.csv` before attributing a slowdown to a stage.
* **Global Constraint 6 is NOT discharged for this tree.** The container run
  has not been repeated since `575219d`, and this phase changed `bin/`,
  `modules/`, `workflows/` and added a sixteenth module. `sbatch
  tools/verify_clinarmonize.sh` is the first thing the next session should
  do, and `tests/value_map.nf.test` is picked up by the fast chunk
  automatically — the chunk lists are derived from disk (`5e0c6fa`), so no
  list needs editing.
* Local runs still need **`export NXF_VER=26.04.6`**. A bare `nextflow`
  resolves 25.04.7 and fails every test on *"Plugin nf-schema@2.8.0 requires
  Nextflow version >=26.04.0"*.
* `test_data/` is gitignored. Run `python3 tests/fixtures/make_fixtures.py`
  before the suite; this phase added five fixtures.
* A CLI number is a **string** to nf-schema. `--max_unmapped_frac 0.6` is
  rejected as *"Value is [string] but should be [number]"*; the same value in
  a `-params-file` is fine, and so is nf-test's own `params { }` block. This
  bit twice while deriving fixture hashes and is not a defect in anything
  this phase touched — it is worth knowing before diagnosing it a third time.

| §0.9 stage | state | entry point |
|---|---|---|
| §1 ingest | built (phase 0) | `subworkflows/local/ingest/` |
| §2 profile | built (phase 0) | `modules/local/profile_columns/` |
| §3 link | built (phase 1) | `link_blocking/`, `link_score/`, `link_resolve/` |
| §4 propose | built (phase 0) | `propose_candidates/`, `propose_channels/`, `propose_ledger/` |
| §5 confirm | built (phase 0); **Contract extended this phase** | `confirm_ledger/`, `compile_rules/` |
| §6 map | **complete**: §6.1 (phase 2), §6.2 (phase 4), **§6.3 this phase** | `map_concepts/`, `convert_units/`, **`value_map/`** |
| §7–§9 derive/coverage/emit | not built — §0.7 items 9–12 | — |
| §10.1 invariant harness | scope `map`; **now digests the value-mapped artefact** | `permute_outcome/`, `artefact_digest/`, `invariant_report/` |
| §10.2 fixtures | built (phase 1) | `conf/test.config`, `conf/test_full.config` |
| §10.3 generality | not built — §0.7 item 14 | — |

`implementedStages()` and `stageGraph()` are **unchanged**, and §6.3 did not
get a `--stop_after` entry. §6.1/§6.2/§6.3 are parts of the `map` stage as
§3.1–§3.3 are parts of `link`, and there is deliberately no way to stop
between them: a mapped table whose categorical columns still carry each
cohort's own value grain is not a smaller result, it is a result in
unharmonized vocabularies. (This is the sixth handoff to say `implementedStages()`
is not to be "fixed" into a prefix walk.)

## What §5 gained

An optional `value_map:` on a confirmed row, one entry per collapse group:

```yaml
value_map:
  - from: ["0", "1"]        # source values, as written in the table
    to: "0-1"               # canonical value; §6.3 checks it against the pack
    concept_id: 4000        # the VALUE's standard concept (optional)
    rationale: "..."        # required under --require_rationale
```

**Additive, and backwards compatible by construction.** `bin/confirm_ledger.py`
returns `[]` for a row that declares none, which is every row every phase
before this one wrote. No existing fixture, ledger or test changed.

**A group, not a pair.** §6.3's Contract makes `from` a list and `fan_in`
`len(from)`. A pair-shaped ledger could not express a fan-in without
recovering it by grouping on the target — the one field the card's nogo makes
mandatory, derived rather than declared.

**`from` is sorted at the gate**, so `["1","0"]` and `["0","1"]` are one
decision and compile to one `rule_id`. §5.2's done-when (reordering the
ledger leaves every `rule_id` unchanged) now covers reordering *inside* a
group too, structurally rather than by asking reviewers to be tidy.

`bin/compile_rules.py` emits one `value_map` rule per group:

```
from = {cohort_id, dataset_id, column, values: [str]}
to   = {variable, concept_id, value: str}
params = {}
```

`from.values` is inside `from` because `rule_id` is `sha256(kind, from, to,
params)` and two groups on one column must get two ids. `to.concept_id` is the
VALUE's concept, not the column's — the column's is on its own `column_map`
rule. `params` is empty and specifically does not copy `unit_in`: a
categorical value has no unit, and a field in the hash that means nothing
about the rule is a field that can change the id for no reason.

**§5.2's collision detector is untouched, and was verified rather than
trusted** (the brief asked for exactly this). It keys on
`(cohort_id, dataset_id, to.variable)` → set of `from.column`. A `value_map`
rule and its `column_map` sibling come from the same decision row, so they add
the same single element to the same set; so do two groups on one column. It
cannot manufacture a collision and — the half that matters more — cannot mask
one, because a genuine two-column collision is still two distinct columns.

`ruleset.json`'s sort key grew to `(cohort, dataset, column, kind, rule_id)`.
The first three were a total order only while one column produced one rule.

**Every existing `column_map` rule_id is byte-identical after the change**,
checked by diffing a ruleset compiled before it against one compiled after:
the hashing construction was extracted into `_rule()` so the two kinds cannot
drift into two constructions, and the canonical JSON it hashes is unchanged.
An audit against an older output still resolves to the right rule, which is
§5.2's Trap and the whole reason `rule_id` is a content hash.

## Where each refusal lives

Six, and the split is not arbitrary: §5.1 refuses what the FILE fails to
answer, §6.3 refuses what needs the pack in hand (§5.1's IN slot is the two
ledgers and nothing else).

| refused at the gate (§5.1) | refused in §6.3 |
|---|---|
| one value claimed by two groups | `to` outside the variable's `domain_values` |
| two groups with the same `to` | a `value_map` on a `derived` variable |
| `value_map` on `reject`/`defer` | (and, across rows, two rules claiming one value) |
| `value_map: []` | |

The `derived` refusal is **§6.3's own Trap as code**. The Trap names *best
response*: an endpoint derived from a scan sequence under a criteria version
(§7.3), where mapping the source's stated string silently imports whichever
criteria that cohort used, unrecorded. Global Constraint 4 forbids naming a
clinical term in `bin/`, so the check is on the pack's `domain: derived`
instead — an endpoint is a derived variable by construction, and the pack's
schema already requires a `derivation` for one. It is unreachable by accident
(§4.1 cannot propose a derived variable; it has no source column) and is there
for the ledger that names one deliberately.

The `domain_values` refusal reads the pack's own schema literally:
`domain_values` is described there as a variable's "enumerated legal values",
so a canonical value invented at the gate is a target vocabulary nothing
declared — the value-level form of §6.2's "never convert to a unit the pack
does not declare". A variable declaring no `domain_values` has nothing to
check against, and the absence of the check is what records that.

## What §6.3 writes

`bin/value_map.py` + `modules/local/value_map/`. Seam: `ValueMapper —
mapValue(str, Rule) -> concept_id?`.

Two columns are appended to every domain table:

* **`value_as_concept_id`** — the rule's `to.concept_id`. **NULL, never 0**,
  on a row no value-map rule claims: 0 is OMOP's designated "no matching
  concept" and is what a rule whose reviewer supplied *no* concept id writes
  (Ruling R14 again — no vocabulary is vendored and nothing can resolve
  `"0-1"`). NULL says something weaker and different: no value mapping applies
  to this row at all, which is every continuous measurement in the run.
* **`value_rule_id`** — §5.2's Why is that "every emitted cell name the rule
  that produced it", and this stage writes a cell. It *is* derivable from
  `(rule_id, source value)` plus the ruleset, since the value sets cannot
  overlap — but provenance that has to be recomputed by re-running the mapping
  is not provenance.

**The canonical value is not written as a third column.** That is the card's
own Alternatives table ("emit both raw and canonical columns"), listed there
with its cost: "widens every domain table and pushes the decision to every
consumer". The verbatim source value stays where §6.1 put it, the canonical
value lives on the rule the row now names, and `qc/value_collapse.json` is the
join between them.

Every domain table gets both columns whether or not any rule touches it — an
empty CASE is `CAST(NULL AS BIGINT)` — so the mapped schema does not depend on
which columns happened to be value-mapped. `mapped/_unmapped.parquet` is
copied through verbatim, as §6.2 copies it: it carries no `rule_id` to match
against and no row to rewrite.

### Three qc artefacts, and why the third exists

`qc/value_collapse.json` is the card's OUT slot, one entry per group:

```json
{ "rule_id": "R-…", "variable": "ecog", "from": ["0","1"], "to": "0-1",
  "n_rows": 55, "fan_in": 2, "value_as_concept_id": 4000,
  "flag": null, "max_fan_in_warn": 3, "alluvial": { … } }
```

`fan_in` is on **every** entry, including the fan-in of 1 — the nogo is "Do
not collapse a value set without writing its fan_in", and a stage that wrote
it only for lossy groups would pass the card's own `jq` unchanged. It is
`len(from)`, computed where it is written and never stored on the rule: a
stored fan_in is a second place the width of a collapse is written down.
`flag` is `wide_collapse` above `--max_fan_in_warn` and is **not fatal** —
that param's effect column names a flag and no exit code. `max_fan_in_warn` is
echoed onto each entry the way §6.2 echoes its quantiles, so an entry read
alone says what it was judged against.

`qc/alluvial_<variable>.png` — one per variable. Hand-written, importing
`Canvas` from `bin/link_resolve.py` rather than copying it: `bin/` scripts
already import each other (`propose_ledger` imports `propose_channels`), and
two zlib/CRC32 encoders in one repo are two things that can disagree. The
FONT is §6.3's own — `link_resolve`'s covers digits and a decimal point
because it labels two numeric thresholds, and this one labels arbitrary source
values. **Every ribbon's pixel geometry is published into the qc entry**, so
`tests/value_map.nf.test` reads the ribbons back off the image. That is §3.3's
shape and it is the only thing that makes this card's nogo enforceable: "the
plot exists" is not an assertion anyone re-checks.

Values that no group claims are drawn on the left in grey with **no ribbon
leaving them**. A picture that omitted them would draw a collapse that looked
complete.

`qc/value_unmapped.json` is the third artefact and the card's OUT slot does
not name it. It exists because `--unmapped_value_policy`'s effect column is
"whether an unmappable value stops the run or is **recorded**", and neither
named artefact can hold it: `value_collapse.json` is a list of collapse groups
whose done-when `jq` indexes it as one, and a value that reached no group is
not a group; `mapped/_unmapped.parquet` is §6.1's record of values with no
standard concept, and these rows *have* one — their column was mapped — so
moving them there would also delete them from a domain table, against the OUT
slot's own "same rows". It is written unconditionally (empty list included) and
**before** the exit under `fail`, so the file exists in the failed task's work
directory; the exit message carries the same values, because an operator
reading a red run should not have to find a work directory.

The policy is scoped to columns the ledger DID value-map. A column with no
value vocabulary has none to be outside of, and counting its values would make
the policy fire on every continuous measurement in the run.

## The publishing switch, again

**§6.3 is now the only publisher of `mapped/`.** This is the same load-bearing
change §6.2 made and it is worth restating because it will recur at §7: two
processes with a `publishDir` into `${params.outdir}/mapped` are resolved by
whichever task finishes last, so `results/mapped/` would carry value-mapped or
unmapped rows depending on task scheduling.

`CONVERT_UNITS` did **not** stop publishing wholesale, unlike `MAP_CONCEPTS`
before it. It emits two artefacts and only one moved: its `publishDir` now
carries a `saveAs` dropping `mapped/` and keeping `qc/unit_conversions.json`,
which is §6.2's own OUT slot and has nothing to do with §6.3. A bare
`enabled: false` would have deleted that file from `results/` as a side effect
of a §6.3 change.

`ch_mapped` is rebound to `VALUE_MAP.out.mapped`, so the single publisher and
the artefact ADR-004's digest is taken over are the same bytes.

## ADR-004: amended a second time, and the argument got sharper

`map` still names `[ledger.proposed.yaml, mapped/]` and `SCOPE_ARTEFACTS` in
`bin/invariant_report.py` is **unchanged**. `implementedScopes` in
`workflows/harmonize.nf` is still `['propose', 'map']`. Both were checked
rather than assumed; §6.3 is a part of the `map` stage, not a stage, so
neither list has a member to gain.

`VALUE_MAP_PERMUTED` runs on every replicate and `ARTEFACT_DIGEST` consumes
its output. The §6.2 amendment wrote down the argument that adding the step
was unnecessary because a conversion is a fixed function of the mapped rows.
**That argument is strictly weaker here, and the amendment says so:** a unit
conversion is injective, so `n_distinct(converted) == n_distinct(mapped)`, but
a value collapse is many-to-one *by definition* — that is what `fan_in > 1`
means — so `n_distinct(value-mapped) <= n_distinct(converted)`, strictly. The
two artefacts are genuinely different measurements. Digesting §6.2's output
would be **more** sensitive and would be reporting on bytes the run does not
publish, which is the `[SUCCESS]`-over-an-unmeasured-claim this repo has now
hit twice. The sensitivity that is lost is lost because a human at a gate
asked for it to be lost.

The ruleset and all three §6.3 params are the BASELINE's on every replicate,
exactly as the factor table is.

## The fixture

`tests/fixtures/make_fixtures.py`: `values_pack`, `values_samplesheet`
(+ `values_fixture_table`), `confirm_ledger_values`,
`confirm_ledger_values_ambiguous`, `confirm_ledger_values_undeclared`.

120 rows, two categorical columns in **two different pack domains** — so they
land in two different CDM tables and the fixture exercises §6.3 rewriting more
than one. §6.2's fixture could not: both its analytes are measurements.

The pack declares the **coarse** grain (`ecog: ["0-1", "2+"]`,
`sex: ["male","female","unknown"]`) and the table carries the fine one. That
is not a detail — it is what makes a collapse exist at all, and it has a
consequence worth knowing: §4.1's `value_set` generator cannot fire on either
column (`ECOG_PS`'s observed values share nothing with `["0-1","2+"]`), so
both are proposed by `name_ngram` instead. That is why the pack variable is
named `ecog` and not `performance_status` — `ECOG_PS`/`ecog` share 2 of 4
trigrams, `ECOG_PS`/`performance_status` share none, and a column with no
candidate is absent from `ledger.proposed.yaml` and rejected by
`CONFIRM_LEDGER` as unmatched.

Five collapse groups, chosen so each assertion has something to fail on:

| group | fan_in | n_rows | what it is for |
|---|---|---|---|
| `["0","1"] → "0-1"` | 2 | 55 | a real collapse |
| `["2","3","4"] → "2+"` | 3 | 45 | a **different** width and row count, so a plot drawing constant ribbons is visible |
| `["M","MALE"] → "male"` | 2 | 48 | the second variable, second table |
| `["F","FEMALE"] → "female"` | 2 | 48 | |
| `["U"] → "unknown"` | **1** | 24 | the nogo: a fixture whose every group was lossy could not tell a stage that writes `fan_in` from one that writes it only when something was lost |

`ECOG_PS` also carries `"9"` on 20 rows, claimed by no group — a fifth of the
column, not a rounding error, because a policy tested on one row could be
satisfied by an off-by-one. Under the default (`manifest`) it is recorded;
under `fail` the run stops.

The widest collapse is 3, so **nothing is flagged under the default
`--max_fan_in_warn 3`** and exactly one group is flagged at 2. One fixture,
both behaviours, and the param is demonstrated rather than asserted — §6.2's
`plausible_range_quantiles` discipline.

**The pack trap applies.** `nf-test.config` sets `profile = "test"` and
`conf/test.config` sets `concept_pack` to `assets/packs/omop_cdm53.yaml`, so
every test overrides `concept_pack` explicitly. `_VALUES_FIXTURE_HASH` was
derived empirically by running the pipeline once with that override and
hashing the published `ledger.proposed.yaml`.

## The tests

Written and confirmed failing first (§0.2 step 2). The pre-implementation run
**succeeded through `map` and produced no `qc/value_collapse.json` at all**,
with `rules/ruleset.json` carrying `kinds: ['column_map'] n= 2` — the ledger's
`value_map:` keys were silently ignored by the unmodified `CONFIRM_LEDGER`,
which is exactly what the brief predicted.

Eight, of which the card names one:

| # | what it holds |
|---|---|
| 1 | **the card's done-when**, with the vacuity removed. Every collapse by name, width and row count; `fan_in == len(from)` on every entry; both PNGs exist; **every ribbon read back off the pixels** at both ends; one colour per group |
| 2 | `value_as_concept_id` set, the collapse visible in `mapped/` (two grades → one id), the source value NOT rewritten, `"9"` NULL and not 0, `value_rule_id` naming the right rule, and `ruleset.json` carrying 5 `value_map` + 2 `column_map` rules with distinct ids |
| 3 | `--max_fan_in_warn 2` flags exactly the 3-wide group, records the threshold, and does not fail the run |
| 4 | `--unmapped_value_policy fail` stops the run naming the column, the variable and the value |
| 5 | the default `manifest` writes `qc/value_unmapped.json` with the right count |
| 6 | `--emit_alluvial false` suppresses the PNGs and **says so** on every entry, rather than silently omitting them |
| 7 | a value claimed by two groups is refused at the gate, naming BOTH groups |
| 8 | a canonical value the pack does not declare is refused, naming the value and the variable |

Tests 2–8 are not padding, for the reason §6.2's four extra tests are not: a
done-when that exercises one path passes against a stage that does one thing.
The card's `jq` is `>= 0`, which is vacuously true of the empty list, of a
file with no `fan_in` key, and of a stage that recorded nothing.

## §11.1

Three params, each a `nextflow_schema.json` property with that exact default,
`domain` as `minimum`/`maximum` or `enum`, `effect` as `help_text`. Verified
live, not by reading:

* `--help` renders all three with their defaults.
* `--max_fan_in_warn 25` (via a params file) is rejected: *"25 is greater than
  20"*.
* `--unmapped_value_policy drop` is rejected: *"Expected any of [manifest,
  fail]"*.

`unmapped_value_policy` is an `enum`, not an `array`, so it does **not** widen
the nf-core/tools 4.1.0 blast radius below. `emit_alluvial` is a boolean and
`max_fan_in_warn` an integer; neither does either.

## Cluster tooling, and the bug it uncovered

Five commits after `db61367`, none of which touch the pipeline: `e0e33fe`,
`57192c0`, `0db1c51`, `f1e8152`, `637a61b`.

`tools/run_pipeline.sh` runs the pipeline with the checkout on one filesystem
and the run on another. `tools/sbatch_run_pipeline.sh` wraps it for SLURM.
Neither has a site path in it; both read `RUN_DIR`, `SRC_DIR`,
`CLINARMONIZE_CACHE` and `CLINARMONIZE_BIND` from the caller. The README gained
a "Running on an HPC cluster" section.

`tools/verify_clinarmonize.sh` had **two absolute paths from one machine baked
in** — a container cache under a named scratch project and an nf-test workdir
under a named user — in a public repo. Neither was load-bearing. Both now
resolve `$CLINARMONIZE_CACHE` → `$SCRATCH` → `$HOME`.

Four things only came out of running it on a real cluster, and each is the kind
that is silent from the submitting shell:

* **`RUN_DIR=... sbatch script.sh` does not reach the job** unless the site
  runs `--export=ALL`. `RUN_DIR` is now an argument (`--run-dir=PATH`), because
  a command line is the one thing SLURM always preserves.
* **SLURM executes a spooled copy of a batch script**, so `${BASH_SOURCE[0]}`
  cannot locate the repo. `$SLURM_SUBMIT_DIR` can.
* **A bare CLI number is a string to nf-schema.** `--max_unmapped_frac 0.6`
  is rejected as *"Value is [string] but should be [number]"*; the same value
  in a `-params-file` validates. This is recorded twice in this document
  because both a README example and a demo command got it wrong.
* **`${VAR:-default}` substitutes on empty as well as unset**, so an explicit
  `CONDA_ENV=` could not mean "already active". `${VAR-default}`, one dash, is
  the only form that distinguishes them.

### The bind bug — a real defect, confirmed on the cluster

Under `-profile singularity`, any input outside an auto-mounted path fails:

```
IOException: No files found that match the pattern "/beegfs/.../values_fixture.csv"
```

The file exists. `LINK_BLOCKING`, `LINK_SCORE`, `LINK_RESOLVE` and
`MAP_CONCEPTS` take their source tables as `val(tables_json)` — a JSON string
of absolute paths — so Nextflow never learns they are files and
`singularity.autoMounts` has nothing to bind. It survived a full container
verification because that run's fixtures lived under the checkout in `$HOME`,
which auto-mounts.

**`SINGULARITY_BIND`/`APPTAINER_BIND` do not fix it**, and this cost a
round-trip: Nextflow invokes the engine with a cleaned environment
(`env - PATH=... singularity exec ...`), so an exported bind list is wiped. The
`SINGULARITYENV_*` forwarding visible in every task log exists *because* of
that clearing — it was the clue in the first failure. The working mechanism is
`singularity.runOptions`, which `tools/run_pipeline.sh` now generates into
`$RUN_DIR/.clinarmonize-binds.config` and passes with `-c`.

That is a workaround in the launcher, **not a fix in the pipeline**. Anyone
running `nextflow run` directly still hits it, and so will real cohort data on
scratch. The fix is to make those four inputs real staged `path` inputs, which
is a §3/§6.1 contract change and wants its own task.

## What is not done — the remaining ladder

| §0.7 item | ladder | cards | state |
|---|---|---|---|
| 1–7 | §11.1, §1, §2, §10.1, §4, §5, §3 | — | done (phases 0–1) |
| 8 map | §6 | `s6-1` ✅ `s6-2` ✅ `s6-3` ✅ | **complete** |
| 9 derive endpoints | §7 | `s7-1`, `s7-2`, `s7-3` | not started |
| 10 coverage | §8 | `s8-1`, `s8-2`, `s8-3` | not started |
| 11 emit + manifest + provenance | §9 | `s9-1`, `s9-2` | not started |
| 12 report | §9.3 | `s9-3` | not started |
| 13 container pins + versions | §11.2 | `s11-2` | pins done as modules land; **the floating-tag audit has never run** |
| 14 generality run + grep | §10.3 | `s10-3` | not started; params seeded in phase 0 |

Known blockers, per item:

**§7 will take `mapped/` over as publisher, and must take the digest with
it.** This is now three phases in a row (§6.2, §6.3, and whichever part of §7
first rewrites the mapped tables). The checklist is: move the `publishDir`
(with a `saveAs` if the previous writer publishes anything else), rebind
`ch_mapped`, add the `*_PERMUTED` alias, add it to the disabled-publish
selector in `conf/modules.config`, point `ARTEFACT_DIGEST` at the new output,
and amend ADR-004. Missing any one of them is a `[SUCCESS]` over bytes the run
does not publish.

**§7.3 and §6.3 constrain each other, and §6.3 has taken its side of it.** A
`value_map` on a `derived` variable is refused with a message naming §7.3.
Whoever builds §7.3 should read that refusal: it is the assumption §7.3 is
being built against, and if §7.3 decides differently, that refusal is the
thing to change.

**§8.3 will need an ADR.** The standardized-difference forest legitimately
reads outcome variables *for reporting*, which is why `--invariant_scope all`
is refused today. Expect to have to say, in writing, where the harness's claim
stops and why that is not a relaxation.

**§9.3's Quarto pin is contradicted in the repo.** §0.8 of the spec pins
**1.10.18**; `docs/plans/phase-0.md`'s Global Constraint 8 says **1.7.33**.
Nothing has needed Quarto yet. The spec is authoritative.

**A §5 usability gap this phase created and did not close.** A reviewer
confirming a categorical column now has two jobs at the gate, and the second is
unbounded: the collapse groups have to cover the column's value set, and
nothing in §5 tells them what that set is. §2.1's profile already records every
distinct value; carrying them into `ledger.proposed.yaml` would turn recall
into review. That is a §4.3 contract change and was deliberately not made here.
`--unmapped_value_policy fail` is the stopgap and is not the default, because
stopping on the first unreviewed code is right for a release and wrong for a
first pass over a new cohort.

Carried, unchanged, and not widened by this phase:

* **`docs/output.md` is still nf-core template boilerplate**, now also not
  documenting the three `qc/value_*` artefacts — on top of `mapped/`, `link/`,
  `rules/` and `qc/`.
* **`CHANGELOG.md` is still the untouched nf-core template.**
* **The new tests do not snapshot `versions.yml`**, adding to the
  `nf_test_content` lint failures already sitting behind the nf-core/tools
  crash.
* **Source tables are passed by absolute path, not staged — and this is a real
  bug, not a curiosity.** `LINK_BLOCKING`, `LINK_SCORE`, `LINK_RESOLVE` and
  `MAP_CONCEPTS` take `val(tables_json)`, a JSON string of absolute paths,
  rather than staged `path` inputs. Nextflow therefore does not know they are
  files, and `singularity.autoMounts` has nothing to bind. Under
  `-profile singularity` any input outside an auto-mounted path (`$HOME`,
  `/tmp`, the work dir) is simply **not there** inside the container:

  ```
  IOException: No files found that match the pattern "/beegfs/.../values_fixture.csv"
  ```

  Confirmed on the cluster 2026-08-18, with the fixtures on scratch. It
  survived a full container verification because that run's fixtures lived
  under the checkout in `$HOME`, which auto-mounts — the failure needs inputs
  outside `$HOME` to appear at all, and no run had ever had them. **It affects
  real cohort data on scratch exactly as much as it affects fixtures.**

  `tools/run_pipeline.sh` works around it by generating a
  `singularity.runOptions` config binding `RUN_DIR`, `SRC_DIR` and
  `$CLINARMONIZE_BIND`, passed with `-c`. The obvious route — exporting
  `SINGULARITY_BIND` / `APPTAINER_BIND` — was tried first and does **not**
  work: Nextflow launches the engine with a cleaned environment
  (`env - PATH=... singularity exec ...`), which is why it forwards task
  variables through the `SINGULARITYENV_` prefix at all, and an exported bind
  list is wiped before singularity reads it. That is
  a workaround in the launcher, not a fix in the pipeline: anyone invoking
  `nextflow run` directly still hits it. The fix is to make those four inputs
  real staged `path` inputs, which is a §3/§6.1 contract change and wants its
  own task.

  `CONVERT_UNITS` and `VALUE_MAP` do **not** extend the problem — their
  `mapped_in/` and factor table are real staged `path` inputs.
* **`tools/parquet_roundtrip_probe.py` still does not cover DECIMAL.** §6.3
  did not need it: `value_as_concept_id` is BIGINT and `value_rule_id` is
  VARCHAR, both already covered. The next stage that writes a DECIMAL has to
  widen the probe first.

## Known-red, already diagnosed — do not re-investigate

Unchanged: `.github/workflows/linting.yml` fails on the first PR because
nf-core/tools 4.1.0 corrupts `array` and `object` schema defaults
(`docs/upstream/nf-core-tools-array-defaults.md`, drafted and **not filed** —
filing is the maintainer's call). 25-plus ordinary lint failures sit behind
that crash. This phase added no `array` default, so the blast radius is
unchanged.

`nextflow_schema.json` is hand-formatted with inline arrays and literal UTF-8
`§`. This phase added 24 lines as text and deleted none; nothing was
round-tripped through `json.dump`.

## Housekeeping

* `.superpowers/` is gitignored and holds phase 0's ledger and rulings.
  Nothing from phases 1–5 was written there.
* Commit messages use gitmoji `:shortcode:` prefixes.
* Do not push, open a PR, or file an upstream issue without being asked.
