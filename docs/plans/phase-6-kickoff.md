# Phase 6 kickoff — §7 derive, and the §1.1 contract change it needs

Paste-ready brief for a fresh session. It assumes no memory of any previous
conversation; everything it needs is either here or named by path.

**The headline, so you do not discover it three hours in:** §7.2 and §7.3
cannot be built as §7 changes alone. Both name `samplesheet` in the "set by"
column of their Params tables — `event_coding` and `response_criteria` — and
the samplesheet has five columns, none of them either. **You are reopening
§1.1's contract**, exactly as phase 5 had to reopen §5.1's. Read "The blocker"
below before writing anything.

There is a second, larger question underneath it, and it is not a
configuration one: **§7 is the first stage that produces the outcome
variable.** §10.1's whole harness exists to permute that variable and prove
nothing upstream depends on it. Where the harness's claim stops, and why that
is a boundary rather than a relaxation, has to be written down. See "The
invariant meets the thing it measures".

## Read first, in this order

1. `docs/plans/phase-0.md` — the spec, the eight Global Constraints, the build
   protocol. All eight are binding on every task.
2. `docs/plans/phase-5-handoff.md` — current state. Read the top block, "The
   bind bug", and "What is not done" in full; they carry the traps and the
   whole remaining ladder.
3. `docs/steps/s7-1.md`, `s7-2.md`, `s7-3.md` — the three cards you are
   implementing. Then `s1-1.md`, because you are changing its contract.
4. `docs/adr/0005-the-confirmed-ledger-gains-value-map.md` — not because §7
   depends on it, but because it is the worked example of the move you are
   about to repeat: a stage that could not be built until the contract of an
   earlier stage grew a field. Its "Where each refusal lives" split is the
   pattern to copy.
5. `docs/adr/0004-invariant-scope-widens-to-map.md`, including **both**
   amendments at the end. You will probably have to amend it again.

## State at the time of writing

* Branch `main`, HEAD `637a61b`, **pushed** to
  `github.com/sceriff0/clinarmonize` (public). No PR has ever been opened;
  opening one is what first fires `linting.yml` and `nf-test.yml`, including
  the known-red crash below. Pushing to `main` fires only
  `build-container.yml`, and only on `containers/**` paths.
* **Host: 69/69 green** under `-profile test`.
* **Global Constraint 6 is NOT discharged.** The last container verification
  was `575219d` (phase 4, 61/61). Phase 5 changed `bin/`, `modules/`,
  `workflows/` and added a sixteenth module. `sbatch tools/verify_clinarmonize.sh`
  is the first thing to do — before writing any §7 code, so a red result is
  attributable to phase 5 rather than to you.
* The **pipeline** does run green on the cluster under Apptainer (14/14 through
  `map`, 2026-08-19). That is not the same claim as the suite.
* Local runs need **`export NXF_VER=26.04.6`**. `nextflow.config` pins
  `nextflowVersion = '!>=26.04.6'` and the `!` makes a mismatch an abort.
* `test_data/` is **gitignored**. Run `python3 tests/fixtures/make_fixtures.py`
  before the suite, every time.
* **A bare CLI number is a string to nf-schema.** `--fail_on_rising_km false`
  and `--min_date_precision month` are fine; any numeric param on the CLI is
  not. Use `-params-file`.

| §0.9 stage | state |
|---|---|
| §1 ingest, §2 profile, §4 propose, §5 confirm | built (phase 0; §5.1's Contract extended in phase 5) |
| §3 link | built (phase 1) |
| §6 map | **complete** — §6.1 (phase 2), §6.2 (phase 4), §6.3 (phase 5) |
| §7 derive | **yours** — §0.7 item 9 |
| §8 coverage, §9 emit | not built — §0.7 items 10–12 |
| §10.1 invariant harness | scope `propose` and `map`; digests the post-§6.3 artefact |
| §10.2 fixtures | built (phase 1) |
| §10.3 generality | not built — §0.7 item 14 |

## The blocker: two params have nowhere to live

`assets/schema_input.json` declares exactly five columns, all required:

```
cohort_id, dataset_id, role, path, holdout
```

§7.2's Params table says `event_coding` is **set by samplesheet**, defaulting
to `{censored: 0, event: 1}`, and its own nogo is *"Do not infer event_coding
from the data"* — with the Trap spelling out why inference is impossible:
`{1,2}` is a perfectly plausible shifted flag, so the value set alone cannot
distinguish the two codings. §7.3's `response_criteria` is likewise **set by
samplesheet**, and its nogo is *"Do not default an unscored cohort to RECIST
1.1"*.

So both are per-cohort human declarations that cannot be inferred and cannot be
defaulted. That is the same shape as §6.3's value maps, and it resolves the
same way: the contract that carries human declarations has to grow.

Two things make this harder than phase 5's change, and both are worth deciding
deliberately rather than discovering:

* **`event_coding` is a MAP, in a CSV.** The samplesheet is a flat CSV
  validated by `assets/schema_input.json`. A nested `{censored: 0, event: 1}`
  does not fit a CSV cell without an encoding decision (a `censored:0;event:1`
  string? two columns `event_code` / `censor_code`? a sidecar YAML keyed by
  cohort_id?). §1.1's own Trap and its `role` column are worth re-reading
  before choosing.
* **It is per COHORT, not per (cohort, dataset).** The samplesheet's unit is a
  dataset — §1.1 rejects a duplicate `(cohort_id, dataset_id)` pair, so one
  cohort legitimately appears on several rows. A per-cohort declaration
  repeated on every row of that cohort must either be checked for consistency
  or be moved somewhere the repetition cannot disagree with itself. Silently
  taking the first row's value is the defect this bullet exists to prevent.

### The pack has no derivation spec either

§7.2's IN slot is "normalised dates + **pack variables with domain: derived**",
and it needs "the pack's event condition". What the pack actually carries
today, verified in `assets/packs/clinical_core.yaml`:

```yaml
{'name': 'mortality', 'domain': 'derived',
 'derivation': 'vital_status_at_last_contact', 'outcome': True}
```

`derivation` is a NAME and nothing else — `assets/schema_pack.json` types it as
a string matching `^[a-z][a-z0-9_]*$`. There is no event condition, no time
zero, no source column reference. Decide early whether §7.2 reads a derivation
spec from an extended pack schema, or whether `derivation` stays a name that
selects one of a fixed set of implementations. Both are defensible; only one of
them is what you will have built by the end.

## The invariant meets the thing it measures

§10.1 permutes the outcome column within each cohort and asserts one hash. Every
scope so far has been sound because no stage under measurement ever *read* the
outcome. §7 does more than read it: **§7.2 derives `time_days` and `event`,
which ARE the outcome.**

Nothing here says the harness must widen. It says the opposite is now a claim
that has to be argued rather than assumed, and the argument has to be written
down — because ADR-003 made exactly this kind of soundness argument for the
proposer, and ADR-004 had to widen when it stopped holding. Three specific
things to work out and record:

* A `derive`-scoped replicate would permute the outcome and then *derive the
  outcome from the permuted values*. Every replicate would differ, correctly,
  and the harness would report a leak that is not one. So `derive` is probably
  not a scope — but "probably not" is not what the report says today, and
  `--invariant_scope` currently refuses anything but `propose` and `map` with a
  message naming the reason.
* §7.1 rewrites `mapped/` (its OUT slot is "same rows + `(<col>_date,
  <col>_precision)`"). If it does that in place, `mapped/` has a **fourth**
  writer and ADR-004's digest must follow it. If it writes elsewhere, say so.
* `docs/plans/phase-5-handoff.md` already flags that §8.3 will need an ADR for
  the standardized-difference forest reading outcomes *for reporting*. §7's
  boundary and §8.3's are the same boundary seen from two sides. Consider
  writing one ADR that fixes it once.

## Three things §7 must not break

**1. Exactly one process may publish `mapped/`.** This is now three phases in a
row. `MAP_CONCEPTS` and `CONVERT_UNITS` are `publishDir = [enabled: false]` for
`mapped/` (CONVERT_UNITS still publishes its own `qc/`, via a `saveAs`, and a
bare `enabled: false` there would delete §6.2's OUT slot as collateral).
`VALUE_MAP` publishes. If §7.1 rewrites the same tables, move the publish to
the last writer and adjust — do not add a fourth `publishDir` into
`${params.outdir}/mapped`.

**2. ADR-004's digest follows the last writer of `mapped/`.** `ch_mapped` is
rebound at each stage of §6 and `ARTEFACT_DIGEST` consumes it. A new writer
needs a `*_PERMUTED` alias, an entry in the disabled-publish selector in
`conf/modules.config`, and the rebind — or the harness digests a stale artefact
and reports `[SUCCESS]` over bytes the run does not publish.

**3. `implementedStages()` must gain `derive`, and `SCOPE_ARTEFACTS` must
agree with `implementedScopes`.** `derive` is already in `stageGraph()` (it is
one of §0.9's nine) but not in `implementedStages()`, so `--stop_after derive`
is refused today — and §7.2's own done-when is
`nextflow run . -profile test,docker --stop_after derive`. Adding it is
required. Do **not** add a tenth entry to `stageGraph()`; every
`graph.indexOf(...)` comparison depends on that list being §0.9's nine.

## Build protocol — §0.2, binding

Write the failing test from each card's `done` slot **first**, and confirm it
fails, before any implementation. Implement to `contract`, at the paths
`contract` names. Nothing more. Honour `nogo` — absent instruction is not
licence.

§7's done-whens are four commands across three cards:

```
nf-test test tests/dates.nf.test                      # §7.1
nf-test test tests/endpoints_inverted.nf.test         # §7.2
nextflow run . -profile test,docker --stop_after derive
nf-test test tests/response_criteria.nf.test          # §7.3
```

Each of them exercises exactly one path. §6.2 shipped five tests for a
one-command done-when and §6.3 shipped eight, for the reason both handoffs
give: *a done-when that only exercises one path passes against a stage that
does one thing*. §7.1 in particular has three params whose effect column makes
a claim (`partial_date_policy`, `min_date_precision`, `reject_future_dates`)
and a done-when that tests none of them.

§11.1: every Params row across the three cards becomes a
`nextflow_schema.json` property with that exact default; `domain` becomes
`minimum`/`maximum` or `enum`; `effect` becomes `help_text`. A schema default
differing from a process default is a defect. **`event_coding`'s default is an
OBJECT** — see the known-red section, because that one has a cost.

## Traps that will cost you a session each

* **The bind bug is live and is not yours, but it will bite you.** Under
  `-profile singularity`, §3's and §6.1's source tables are passed as
  `val(tables_json)` — absolute paths Nextflow does not know are files — so
  nothing binds their directory and any input outside `$HOME` fails with
  `IOException: No files found that match the pattern ...`. Run through
  `tools/run_pipeline.sh`, which generates a `singularity.runOptions` config.
  `SINGULARITY_BIND`/`APPTAINER_BIND` do **not** work: Nextflow launches the
  engine with `env -`. If §7 adds a process taking paths as values, it inherits
  the bug; make them staged `path` inputs.
* **The pack trap.** `nf-test.config` sets `profile = "test"` and
  `conf/test.config` sets `concept_pack` to `assets/packs/omop_cdm53.yaml`, so
  **every test that does not override `concept_pack` proposes against that
  pack**. A `proposed_hash` derived against the wrong pack makes
  `CONFIRM_LEDGER` reject the whole file — correctly. Every test in
  `tests/units.nf.test` and `tests/value_map.nf.test` overrides it explicitly;
  copy that. Derive the hash empirically by running once and hashing the
  published `ledger.proposed.yaml`.
* **§7.3 and §6.3 already constrain each other, and §6.3 has taken its side.**
  `bin/value_map.py` **refuses** a `value_map` on a pack variable whose domain
  is `derived`, with a message naming §7.3. That is the assumption §7.3 is
  being built against; if §7.3 decides differently, that refusal is the thing
  to change, not to work around.
* **The KM plot is not blocked, and do not reach for a plotting library.**
  `bin/link_resolve.py` hand-writes a PNG with a zlib/CRC32 encoder, and
  `bin/value_map.py` imports its `Canvas` rather than duplicating it (sibling
  imports across `bin/` work — `propose_ledger` has imported
  `propose_channels` since phase 0). Both publish the pixel geometry they drew
  at so a test asserts on the image. Copy that shape. §0.8 pins no plotting
  dependency and adding one needs saying so in the report.
* **`record_id` is `'<cohort_id>#<dataset_id>#<row_number>'`**, built in four
  places (`link_blocking.py`, `link_score.py`, `link_resolve.py`,
  `map_concepts.py`). If you add a fifth site it must match;
  `map_concepts.py`'s cardinality guard is what catches drift.
* **Do not "fix" `implementedStages()` into a prefix walk.** It deliberately
  checks the requested stage's own membership rather than walking the graph,
  because §0.7 builds out of graph order. This is the seventh handoff to say
  so.
* **Do not remove §4.3's rounding or top-k truncation.** They exist so "a
  float's last bit cannot change the hash".
* **Widen `tools/parquet_roundtrip_probe.py` when a stage first writes a type
  it does not cover.** LIST/STRUCT and TIMESTAMP are covered. **DECIMAL is
  not** — and §7.1 writes dates, §7.2 writes `time_days`. A DATE column is not
  covered either. Check before the first cluster run, because the probe dies
  before any test if it is wrong.
* **`.subscribe { error(...) }`** — the confirm gate uses one.
  `error()` inside a subscribe callback runs on a dataflow thread and does not
  surface the way a failing process does. If you reuse the pattern, convert it
  to a real process.

## Verifying

Local:

```bash
export NXF_VER=26.04.6
python3 tests/fixtures/make_fixtures.py
nf-test test tests/<the-test-you-wrote>.nf.test
nf-test test                                    # full suite
```

The full suite's wall clock is not a stable number on a loaded host — phase 5
measured 7874s under contention and 724s for `tests/invariant.nf.test` alone.
Time individual files, and read a run's own `meta/trace.csv` before attributing
a slowdown to a stage.

Cluster, for Global Constraint 6:

```bash
cd <repo root> && mkdir -p logs
sbatch tools/verify_clinarmonize.sh              # everything
CHUNK=fast sbatch tools/verify_clinarmonize.sh   # the quick chunk only
```

Submit from the repo root — it uses `$(pwd)` and writes its report there. Chunk
lists are derived from disk, so a new `tests/*.nf.test` is picked up
automatically and the verdict names its own scope.

To run the pipeline itself on a cluster (checkout and run on different
filesystems):

```bash
sbatch --partition=<yours> tools/sbatch_run_pipeline.sh \
    --run-dir=/path/to/scratch/run
```

Pass `--run-dir` as an ARGUMENT. `RUN_DIR=... sbatch ...` reaches the job only
where the site runs `--export=ALL`.

The verify harness dies before any test on two preconditions: conda-spec drift
between `containers/duckdb-pyyaml/environment.yml` and the **sixteen** module
copies, and a container→host parquet round-trip. Either failure makes
everything below it meaningless, not merely red.

If a cluster job dies with an empty stderr and a `report.txt` that stops
mid-section, the cause is almost certainly a shell option imported from the
site profile. `bb35174` neutralises them (`set +e +u +o pipefail` after sourcing
`~/.bashrc`); `|| true` does not, because `set -u` makes the shell *exit*.

## Known-red, already diagnosed — do not re-investigate

* **`.github/workflows/linting.yml` will fail on the first PR.** Not your bug:
  nf-core/tools 4.1.0 corrupts `array` and `object` schema defaults. Full
  diagnosis, reproducer and patches are in
  `docs/upstream/nf-core-tools-array-defaults.md`. That issue is **drafted, not
  filed** — filing is the maintainer's call, so do not file it unprompted.
  **§7.2's `event_coding` default is an OBJECT and will widen the blast
  radius**, which is worth knowing before you choose its encoding: two scalar
  columns would not.
* **25-plus ordinary lint failures sit behind that crash**, mostly
  `nf_test_content` "does not snapshot a 'versions.yml' file". New tests add to
  it unless they snapshot `versions.yml`; none written since phase 1 have.
* **`nextflow_schema.json` is hand-formatted** with inline arrays and literal
  UTF-8 `§`. Edit it as text. Round-tripping through `json.dump` reformats it
  wholesale and buries your addition.
* **`docs/output.md` is still nf-core template boilerplate** and documents none
  of `mapped/`, `link/`, `rules/` or `qc/`. **`CHANGELOG.md` is still the
  untouched template.** No phase has maintained either. The README's
  "Running on an HPC cluster" section is the one part that is current.
* **§9.3's Quarto pin is contradicted in the repo**: §0.8 of the spec pins
  1.10.18, `docs/plans/phase-0.md` says 1.7.33. The spec is authoritative.

## Carried defects worth fixing if §7 touches them

* **Source tables are passed by absolute path, not staged** —
  `LINK_BLOCKING`, `LINK_SCORE`, `LINK_RESOLVE`, `MAP_CONCEPTS`. Confirmed to
  break real runs under singularity (see the trap above). The fix is a §3/§6.1
  contract change.
* **A §5 usability gap phase 5 created.** A reviewer confirming a categorical
  column must now cover its whole value set with collapse groups, and nothing
  tells them what that set is. §2.1's profile records every distinct value;
  carrying them into `ledger.proposed.yaml` would turn recall into review. That
  is a §4.3 contract change.

## Housekeeping

* `.superpowers/` is **gitignored** — phase 0's ledger, all 31 rulings and
  every task report live there and are not in git. Nothing from phases 1–5 was
  written there.
* Commit messages use gitmoji `:shortcode:` prefixes.
* **Do not push, open a PR, or file an upstream issue without being asked.**
  Phase 5 pushed because it was asked to; that is not standing permission.
* No site-specific path belongs in this repo. Two were removed from
  `tools/verify_clinarmonize.sh` in phase 5; do not reintroduce the pattern.
