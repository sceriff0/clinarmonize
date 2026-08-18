# Phase 5 kickoff — §6.3 value vocabularies, and the §5 contract change it needs

Paste-ready brief for a fresh session. It assumes no memory of any previous
conversation; everything it needs is either here or named by path.

**The headline, so you do not discover it three hours in:** §6.3 cannot be
built as a §6 change. Its IN slot names "value-map rules" and no such rule can
exist today. You are reopening §5's contract — the human gate — which every
phase since phase 0 has treated as settled. Read "The blocker" below before
writing anything.

## Read first, in this order

1. `docs/plans/phase-0.md` — the spec, the eight Global Constraints, the build
   protocol. All eight are binding on every task.
2. `docs/plans/phase-4-handoff.md` — current state. Read the top block and
   "What is not done" in full; they carry the traps and the whole remaining
   ladder.
3. `docs/steps/s6-3.md` — the card you are implementing. Then `s5-1.md` and
   `s5-2.md`, because you are changing their contract, and `s7-3.md`, because
   its endpoint is §6.3's own Trap.
4. `docs/adr/0004-invariant-scope-widens-to-map.md`, including the §6.2
   amendment at the end. You will have to amend it again.

## State at the time of writing

* Branch `main`, HEAD `5e2f6af`, **pushed** to `github.com/sceriff0/clinarmonize`.
  No PR has ever been opened; opening one is what first fires `linting.yml` and
  `nf-test.yml` (both `pull_request`/`release` only), including the known-red
  crash below. Pushing to `main` fires no CI.
* **Container-verified: 61/61 across all 14 test files under singularity**
  (SLURM 6507381, apptainer 1.4.5, commit `575219d`, `git dirty: no`). Global
  Constraint 6 is discharged for that tree, and nothing in the pipeline has
  changed since — the only later commits are handoff text.
* **Host: 61/61 green** under `-profile test`, 1075s.
* Local runs need **`export NXF_VER=26.04.6`**. A bare `nextflow` resolves
  25.04.7 and fails every test on *"Plugin nf-schema@2.8.0 requires Nextflow
  version >=26.04.0"*. `tools/verify_clinarmonize.sh` already exports it.
* `test_data/` is **gitignored**. Run `python3 tests/fixtures/make_fixtures.py`
  before the suite, every time.

| §0.9 stage | state |
|---|---|
| §1 ingest, §2 profile, §4 propose, §5 confirm | built (phase 0) |
| §3 link | built (phase 1) |
| §6 map | §6.1 (phase 2), §6.2 (phase 4); **§6.3 is yours** |
| §7 derive, §8 coverage, §9 emit | not built — §0.7 items 9–12 |
| §10.1 invariant harness | scope `propose` and `map`; digests the post-§6.2 artefact |
| §10.2 fixtures | built (phase 1) |
| §10.3 generality | not built — §0.7 item 14 |

## The blocker: §6.3 has no rules to apply

`bin/compile_rules.py` only ever emits `kind: "column_map"`. Its own docstring
says why, and it is not an oversight:

> `value_map` needs a value-level mapping table nothing in
> `ledger.confirmed.yaml`'s Contract offers

§5.2's Contract *does* enumerate `column_map|value_map|unit_convert|derive` as
the enum, so the kind is anticipated. What is missing is the human decision
that produces one. §5.1's ledger Contract has `cohort_id, dataset_id, column,
decision, variable, concept_id, unit_in, confirmed_by, rationale,
proposed_hash` — and nowhere to say *"these four source grades become these
two"*.

**A value collapse is a harmonization decision, so it must come from the
gate.** Inferring it from `domain_values` would be §5.1's Why deleted in one
line: *"a pipeline that fills that gap with its own top-ranked guess has
quietly deleted the only review step in the design while still emitting a file
called a ledger."*

### The shape the card asks for

§6.3's Contract fixes the output shape, which fixes the input shape:

```
{ "rule_id": "R-0207", "variable": "ecog",
  "from": ["0","1"], "to": "0-1", "n_rows": 812,
  "fan_in": 2 }        # fan_in > 1 is a lost distinction, by definition
```

`from` is a **list**, so the ledger's unit is a collapse group, not a
value-to-value pair. Something like a `value_map:` key on the confirmed row
carrying a list of `{from: [str], to: str}`. `fan_in` is `len(from)` and is
never optional — the nogo is *"Do not collapse a value set without writing its
fan_in."*

### What already anticipates you, and what does not

Good news, verified:

* `bin/map_concepts.py`'s `ConceptMapper_map` already skips non-`column_map`
  rules and prints a NOTE naming the kind. `bin/convert_units.py`'s
  `plan_conversions` does the same. Neither needs changing to tolerate a new
  kind.
* `rule_id` is `sha256(kind, from, to, params)`, and `kind` is inside the hash
  — so a `value_map` rule for the same column gets its own stable id, distinct
  from its `column_map` sibling, for free.
* §5.2's collision detector keys on `(cohort_id, dataset_id, to.variable)` →
  set of `from.column`. A `value_map` and a `column_map` on the *same* column
  add the same column to that set, so they do not falsely collide. **Verify
  this still holds after your change rather than trusting this paragraph.**

Not anticipated: `value_as_concept_id` does not exist anywhere in the repo
yet. §6.3 introduces it.

## Three things §6.3 must not break

These are all §6.2's lessons, and each one is a whole session if you find it
the hard way.

**1. Exactly one process may publish `mapped/`.** `MAP_CONCEPTS` is
`publishDir = [enabled: false]` and `CONVERT_UNITS` publishes. If §6.3 adds a
third process writing the same five tables plus `_unmapped.parquet` under the
same names, two `publishDir`s into `${params.outdir}` are resolved by whichever
task finishes last — so `results/mapped/` would carry value-mapped or
unmapped rows depending on task scheduling. Move the publish to the last
writer and disable it on `CONVERT_UNITS`.

**2. ADR-004's digest follows the last writer of `mapped/`.** `ch_mapped` is
rebound at each stage and `ARTEFACT_DIGEST` consumes it. If you add
`VALUE_MAP`, you must also add a `VALUE_MAP_PERMUTED` alias in the
`invariant_scope == 'map'` block and rebind `ch_mapped` — otherwise the harness
digests a stale artefact and reports `[SUCCESS]` over bytes the run does not
publish. The ADR's amendment section explains why "it makes no difference
because the transform is outcome-blind" is not an acceptable answer here.

**3. `implementedScopes` and `SCOPE_ARTEFACTS` must agree.**
`implementedScopes` in `workflows/harmonize.nf` decides which stages RUN per
replicate; `SCOPE_ARTEFACTS` in `bin/invariant_report.py` decides which
artefacts are HASHED. A scope wired in one but not the other is a `[SUCCESS]`
over a claim nothing measured. You should not need to touch either for §6.3 —
`map` still means `[ledger.proposed.yaml, mapped/]` — but check.

## Build protocol — §0.2, binding

Write the failing test from the card's `done` slot **first**, and confirm it
fails, before any implementation. Implement to `contract`, at the paths
`contract` names. Nothing more. Honour `nogo` — absent instruction is not
licence.

§6.3's done-when is two commands, not one:

```
nextflow run . -profile test,docker --stop_after map
jq -e '[.[] | select(.fan_in > 1)] | length >= 0' results/qc/value_collapse.json
```

Note that the `jq` is `>= 0`, which is vacuously true. **That is not the test.**
The card's prose is: *"Every collapse in the fixture appears with its fan-in
and an alluvial plot exists."* Build a fixture that actually collapses
something, and assert the collapse, or the done-when passes against a stage
that recorded nothing. §6.2's own five tests exist for exactly this reason —
a done-when that only exercises one path passes against a stage that does one
thing.

§11.1: §6.3's three params (`max_fan_in_warn` 3 / 1..20, `emit_alluvial` true,
`unmapped_value_policy` `manifest|fail`) each become a `nextflow_schema.json`
property with that exact default; `domain` becomes `minimum`/`maximum` or
`enum`; `effect` becomes `help_text`. A schema default differing from a process
default is a defect.

## Traps that will cost you a session each

* **The pack trap.** `nf-test.config` sets `profile = "test"` and
  `conf/test.config` sets `concept_pack` to `assets/packs/omop_cdm53.yaml`. So
  **every test in `tests/` that does not override `concept_pack` proposes
  against that pack**, whatever the pipeline default is. A `proposed_hash`
  derived against the wrong pack makes `CONFIRM_LEDGER` reject the whole file —
  correctly. Every test in `tests/units.nf.test` overrides it explicitly; copy
  that. Derive the hash empirically by running once and hashing the published
  `ledger.proposed.yaml`.
* **§6.3 and §7.3 constrain each other.** §6.3's Trap is that *best response*
  must not be treated as a value to map — it is an endpoint derived from a scan
  sequence under a criteria version, which is §7.3's. Mapping the source's
  stated string silently imports whichever criteria that cohort used,
  unrecorded. Read both cards before either.
* **The alluvial plot is not blocked, and do not reach for a plotting library.**
  `bin/link_resolve.py` hand-writes a PNG with a zlib/CRC32 encoder and a 3×5
  bitmap font, and publishes the pixel coordinates it drew at into
  `link_report.json` so a test asserts on the image rather than eyeballing it.
  Copy that shape — "the plot exists" is not an assertion anyone re-checks.
  §0.8 pins no plotting dependency and adding one needs saying so in the report.
* **`record_id` is `'<cohort_id>#<dataset_id>#<row_number>'`**, built in four
  places (`link_blocking.py`, `link_score.py`, `link_resolve.py`,
  `map_concepts.py`). Phase 3 found the two-field version colliding across
  cohorts and scoring 8 of 20 true pairs. If you add a fifth site, it must
  match, and `map_concepts.py`'s cardinality guard is what catches drift.
* **Do not "fix" `implementedStages()` into a prefix walk.** It deliberately
  checks the requested stage's own membership rather than walking the graph for
  the first gap, because §0.7 builds out of graph order. This is the sixth
  handoff to say so.
* **There is no `--stop_after` between §6.1, §6.2 and §6.3.** They are parts of
  the `map` stage as §3.1–§3.3 are parts of `link`. Do not add a tenth entry to
  `stageGraph()`; every `graph.indexOf(...)` comparison in the workflow depends
  on that list being §0.9's nine.
* **Do not remove §4.3's rounding or top-k truncation.** They exist so "a
  float's last bit cannot change the hash". Removing them makes every §10.1
  permutation hash differ and reports a leak on every run.
* **Widen `tools/parquet_roundtrip_probe.py` when a stage first writes a type
  it does not cover.** LIST/STRUCT and TIMESTAMP are already covered (added for
  §3.3 and §6.1). **DECIMAL is not.** The container's duckdb is 1.5.5 and the
  cluster host's is 1.4.5; the probe is what proves that skew is not silently
  lossy, and it dies before any test runs if it is.
* **`.subscribe { error(...) }`** — the confirm gate uses one
  (`workflows/harmonize.nf:1089`). `error()` inside a subscribe callback runs on
  a dataflow thread and does not surface the way a failing process does. If you
  reuse the pattern, convert it to a real process.

## Verifying

Local:

```bash
export NXF_VER=26.04.6
python3 tests/fixtures/make_fixtures.py
nf-test test tests/<the-test-you-wrote>.nf.test
nf-test test                                    # full suite, ~18 min
```

Cluster, for Global Constraint 6:

```bash
git pull
sbatch tools/verify_clinarmonize.sh              # everything, ~35 min
CHUNK=fast sbatch tools/verify_clinarmonize.sh   # the quick chunk only
```

**The chunk lists are now derived from disk** (`5e0c6fa`), so a new
`tests/*.nf.test` file is picked up automatically and the verdict names its own
scope. Before that fix they were two hand-maintained lists, and three test
files had been in neither — §3's, §6.1's and §6.2's had never run inside the
image while three handoffs recorded F2 as clean. If you find yourself editing
`SLOW_TESTS`, you are moving a file between chunks, never deciding whether it
runs.

The harness dies before any test on two preconditions: conda-spec drift between
`containers/duckdb-pyyaml/environment.yml` and the **fifteen** module copies,
and a container→host parquet round-trip. Either failure makes everything below
it meaningless, not merely red.

If a cluster job dies with an empty stderr and a `report.txt` that stops
mid-section, the cause is almost certainly a shell option imported from the
site profile. `bb35174` neutralises them (`set +e +u +o pipefail` after
sourcing `~/.bashrc`); `|| true` does not, because `set -u` makes the shell
*exit* and `||` cannot catch an exit.

## Known-red, already diagnosed — do not re-investigate

* **`.github/workflows/linting.yml` will fail on the first PR.** Not your bug:
  nf-core/tools 4.1.0 corrupts `array` and `object` schema defaults. Full
  diagnosis, reproducer and patches are in
  `docs/upstream/nf-core-tools-array-defaults.md`. That issue is **drafted, not
  filed** — filing is the maintainer's call, so do not file it unprompted.
  §6.2's `plausible_range_quantiles` added one more `array` default to the blast
  radius; `unmapped_value_policy` is an enum and will not.
* **25-plus ordinary lint failures sit behind that crash**, mostly
  `nf_test_content` "does not snapshot a 'versions.yml' file". New tests add to
  it unless they snapshot `versions.yml`; none written since phase 1 have.
* **`nextflow_schema.json` is hand-formatted** with inline arrays and literal
  UTF-8 `§`. Edit it as text. Round-tripping through `json.dump` reformats 227
  lines and buries your three.
* **`docs/output.md` is still nf-core template boilerplate** and does not
  document `mapped/`, `link/`, `rules/` or `qc/`. **`CHANGELOG.md` is still the
  untouched template.** No phase has maintained either.
* **§9.3's Quarto pin is contradicted in the repo**: §0.8 of the spec pins
  1.10.18, `docs/plans/phase-0.md` says 1.7.33. Nothing has needed Quarto yet.
  The spec is authoritative.

## Housekeeping

* `.superpowers/` is **gitignored** — phase 0's ledger, all 31 rulings and every
  task report live there and are not in git. Nothing from phases 1–4 was written
  there.
* Commit messages use gitmoji `:shortcode:` prefixes.
* **Do not push, open a PR, or file an upstream issue without being asked.**
  Phase 4 pushed because it was asked to; that is not standing permission.
