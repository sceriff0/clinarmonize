# Phase 4 handoff — §6.2 units, and the publishing switch it forced

Covers the item the phase-3 handoff named under "What is not done": §6.2's
wiring, which was blocked on two things that no longer exist
(`assets/schema_pack.json` had no `plausible_range`; `assets/ucum_factors.yaml`
did not exist). **§6.3 is still not started**, and its blocker is a §5
contract change rather than a §6 one — see "What is not done" below.

**The container run happened, and it did not cover this phase.** SLURM
6505317 passed under singularity against `bb35174` — 15 module
`environment.yml` copies matching, the pinned image resolving, the parquet
round-trip clean, `git dirty: no`. It ran **48 of 61 tests**, and
`tests/units.nf.test` was not among them.

`tools/verify_clinarmonize.sh` selected tests from two hand-maintained file
lists, and `link_score.nf.test`, `map_concepts.nf.test` and `units.nf.test`
were in neither. The report said `ALL REQUESTED TESTS PASSED`; the word
carrying it was *REQUESTED*. Fixed in `5e0c6fa` — the fast chunk is now every
`tests/*.nf.test` on disk minus the two slow ones, so a new file is IN by
default, and the verdict names its own scope.

**So Global Constraint 6 is NOT claimable for §6.2, §6.1 or §3.** Not because
anything failed, but because nothing ran. That is also retroactive: phases 2
and 3 recorded F2 as clean on runs that silently declined to execute the code
those phases added. **Re-run `sbatch tools/verify_clinarmonize.sh` against
`5e0c6fa` or later**; the first full-coverage run in this repo's history will
be the one that either discharges the constraint or finds three phases' worth
of container-only defects at once. `bin/convert_units.py` is the first script
here importing both `duckdb` and `pyyaml` in one interpreter, and it has never
been inside the image.

**What that run already fixed on the way.** Jobs 6501142 and 6502426 died
`FAILED 1:0` after 9s with an empty stderr and a `report.txt` ending at
`=== conda ===`. A site `~/.bashrc` that runs `set -u` leaves it active for
the rest of the script, and the next line — `eval "$(conda shell.bash hook)"`
— dereferences variables conda's init leaves unset. `|| true` catches a
non-zero *return*; `set -u` makes the shell *exit*, which `||` cannot catch,
and `2>/dev/null` swallowed the diagnostic. `bb35174` neutralises inherited
options (`set +e +u +o pipefail`) immediately after the source. Note that
`bash -c 'source ~/.bashrc; echo SURVIVED'` prints SURVIVED either way — that
test only ever proved `.bashrc` does not `exit`, and treating it as a
refutation of `set -u` cost three hypotheses.

## State

* Branch `main`, HEAD `5e0c6fa`, **pushed** to
  `github.com/sceriff0/clinarmonize`. This phase is the first to push:
  `origin/main` had been sitting at `37e7c2b` (mid-phase-0), so the push
  carried phases 1-4 across in one go. **No PR has ever been opened**, and
  opening one is what first fires `linting.yml` and `nf-test.yml` (both are
  `pull_request`/`release` only) -- including the known-red nf-core/tools
  4.1.0 crash below. Pushing to `main` fires no CI at all.
* §6.2 itself is `290b3dd`; `bb35174` and `5e0c6fa` are the two verification
  harness fixes the cluster run produced.
* **Suite: 61/61 green** under `-profile test` on the host, 1075s (56 at the
  end of phase 3). The five new tests are all in `tests/units.nf.test`.
* Local runs need **`export NXF_VER=26.04.6`**. Unchanged from phase 3, and
  still true: a bare `nextflow` resolves 25.04.7 and fails every test on
  `nf-schema@2.8.0 requires Nextflow version >=26.04.0`.
  `tools/verify_clinarmonize.sh` already exports it.
* `test_data/` is gitignored. Run `python3 tests/fixtures/make_fixtures.py`
  before the suite; this phase added four fixtures to it.

| §0.9 stage | state | entry point |
|---|---|---|
| §1 ingest | built (phase 0) | `subworkflows/local/ingest/` |
| §2 profile | built (phase 0) | `modules/local/profile_columns/` |
| §3 link | built (phase 1) | `link_blocking/`, `link_score/`, `link_resolve/` |
| §4 propose | built (phase 0) | `propose_candidates/`, `propose_channels/`, `propose_ledger/` |
| §5 confirm | built (phase 0) | `confirm_ledger/`, `compile_rules/` |
| §6 map | §6.1 (phase 2), **§6.2 built this phase**; §6.3 not | `map_concepts/`, **`convert_units/`** |
| §7–§9 derive/coverage/emit | not built — §0.7 items 9–12 | — |
| §10.1 invariant harness | scope `map` (phase 3); **now digests the converted artefact** | `permute_outcome/`, `artefact_digest/`, `invariant_report/` |
| §10.2 fixtures | built (phase 1) | `conf/test.config`, `conf/test_full.config` |
| §10.3 generality | not built — §0.7 item 14 | — |

`implementedStages()` is unchanged and still checks the requested stage's own
membership rather than walking the graph. Do not "fix" it into a prefix walk.
(This is the fifth handoff to say so.)

`stageGraph()` is also unchanged, and §6.2 did **not** get a `--stop_after`
entry. §6.1/§6.2/§6.3 are parts of the `map` stage exactly as §3.1–§3.3 are
parts of `link`, and there is deliberately no way to stop between them: a
mapped table whose units were never converted is not a smaller result, it is
a result in unknown units.

## What §6.2 is

`bin/convert_units.py` + `modules/local/convert_units/`. The seam is
`UnitConverter — convert(value, from, to, analyte) -> value`, and `analyte`
is the **pack variable's name**, never the source column's header: two
cohorts' columns mapping to one variable must convert identically or the
harmonization did nothing.

### Where the two units come from

The card's IN slot names "rules (unit_in, unit_out)" and only one of the two
is on the rule.

* `unit_in` — the rule's `params.unit_in`, which `bin/compile_rules.py`
  already recorded verbatim from the confirmed ledger. Its docstring said in
  as many words that this was for §6: *"§6 map ... is where an actual unit
  conversion, with the pack's canonical unit in hand, belongs."* Phase 0 left
  this on purpose and it was there waiting.
* `unit_out` — the **pack** variable's `unit`. It can be nowhere else: the
  nogo is "Never convert to a unit the pack does not declare", which only
  means anything if the pack is what declares it.

§2.2's ranked `candidate_units` are never re-read here. A ranking is
candidates; a conversion needs a decision, and the decision was made at the
human gate.

### The four cases, and why one is an exit code

```
unit_out is None                  -> nothing to convert TO. Passed through,
                                     status `no-target-unit`, recorded.
unit_in == unit_out               -> factor 1.0, status `identity`, NO lookup.
unit_in is None, unit_out is not  -> REFUSED.
both present and different        -> looked up by (analyte, from, to).
                                     A miss is an exit code, never 1.0.
```

The third is a judgment call and is flagged as one. The pack asserts the
variable is expressed in a specific unit and the source unit was never
resolved, so emitting the number anyway claims a unit on no evidence. The
card's nogo ("Do not guess a unit when candidate_units was empty") and its
Why ("Refusing an ambiguous conversion is cheaper than detecting one") point
the same way, so it fails, naming the rule and pointing at §5.1's `unit_in`.
**No shipped pack trips this today.** `assets/packs/clinical_core.yaml`
declares `unit` on `age` and `length_of_stay` and would, but `-profile
test_full` has no `--confirmed_ledger` and so cannot reach `map` at all. That
is a live trap for whoever writes one.

### The factor table

`assets/ucum_factors.yaml`, ten analytes, every row keyed on
`(analyte, from, to)`. Three things it deliberately does not have:

* **No wildcard row.** There is no universal mg/dL → mmol/L factor; it is
  molar-mass specific. A table keyed on the unit pair alone answers every
  question confidently and is wrong for every analyte but one, and the values
  are plausible, wrong, and indistinguishable from real biological variation.
  The two `g/dL -> g/L` rows *could* have been one wildcard (the factor really
  is 10 whatever is dissolved) and are written per analyte anyway, because a
  single wildcard would establish the shape the mass-to-molar rows must never
  take.
* **No derived inverses.** `mg/dL -> umol/L` does not imply the reverse. The
  reverse row gets written and reviewed like any other. Manufacturing a factor
  by arithmetic is one step from manufacturing one by assumption, and the SIDE
  clause wants a missing factor to be loud.
* **No UCUM grammar.** `from`/`to` are matched literally against the pack's
  `unit` and the rule's `unit_in`. The card's Alternatives table names a real
  UCUM library and names its limit in the same row: it still cannot supply the
  analyte-specific molar factors, which is the hard half.

Duplicate `(analyte, from, to)` keys are an error even when the two factors
agree — the SIDE clause names "ambiguous" first, and a table carrying two
answers to one question is that, whether or not they match.

### The post-condition

`--plausible_range_quantiles` (default `[0.01, 0.99]`) of the **converted**
values, per **variable** across every rule that writes it, against the pack's
new `plausible_range`. Outside it is `implausible_after_conversion`, fatal
under `--fail_on_implausible_range` (default true).

Three readings that are decisions, not obvious:

* **Per variable, not per rule.** Two cohorts' columns mapping to one variable
  are one distribution; checking them separately lets a half-sized cohort's
  wrong factor hide inside a right one. The verdict is then repeated onto each
  of that variable's `qc/unit_conversions.json` entries, so an entry read on
  its own says whether its values were checked and what the check said.
* **On converted values only.** A `no-target-unit` variable gets status
  `not-converted` and no verdict. An unconverted column's implausibility is a
  §2 profiling concern, and firing on it here would make
  `--fail_on_implausible_range` a data-quality gate that stops runs for
  reasons §6.2 did not cause.
* **`--fail_on_implausible_range false` demotes, never suppresses.** The flag
  still lands in the published `qc/unit_conversions.json`. An escape hatch
  that also hid the finding would be a way to make it disappear, and
  `tests/units.nf.test` asserts exactly that split.

The qc file is written **before** the exit, so it exists in the failed task's
work directory — but Nextflow does not publish a failed task's output, so the
exit message carries the same numbers. An operator reading a red run should
not have to find a work directory to learn which analyte and which factor.

## MAP_CONCEPTS stopped publishing, and that is the load-bearing change

`CONVERT_UNITS` rewrites the same five CDM tables plus `_unmapped.parquet`
under the same names. Two processes with a `publishDir` into
`${params.outdir}` would be resolved by whichever task finished last — so
`results/mapped/` would carry converted or unconverted values depending on
task scheduling, which is the worst possible way for a unit error to enter a
dataset. `MAP_CONCEPTS` is now `publishDir = [enabled: false]` and §6.1's
output is an intermediate.

§6.1's own done-when still holds against `results/mapped/`:
`_unmapped.parquet` is copied through verbatim and the domain tables are
rewritten with `SELECT * REPLACE`, so every column §6.1 wrote is still there.

`SELECT * REPLACE`, and not `* EXCLUDE` plus a re-added column, for two
reasons. The five tables do not share a schema (each carries its own
`<domain>_concept_id` / `<domain>_source_value` / `<domain>_datetime`), so an
explicit column list would be five lists to keep in step with a file this
script does not own. And `REPLACE` keeps `value_as_number` **in its own
position** — `bin/artefact_digest.py` hashes columns in declared order, so a
positional shift would move every §10.1 digest for a reason that is not data.

Row order is untouched. §6.1 wrote `ORDER BY person_id, rule_id` and owns
that decision.

## ADR-004: same artefact list, one more process

`map` still names `[ledger.proposed.yaml, mapped/]` and `SCOPE_ARTEFACTS` in
`bin/invariant_report.py` is unchanged. What changed is what `mapped/` **is**,
so `CONVERT_UNITS_PERMUTED` runs on every replicate and `ARTEFACT_DIGEST`
consumes the converted output.

The argument that this was unnecessary is written into the ADR amendment
rather than left implicit, because it is the argument the ADR exists to
distrust: a unit conversion is a fixed function of the mapped rows (factor
table, ruleset and pack are all held at the baseline), so
`n_distinct(converted) <= n_distinct(mapped)` and digesting §6.1's output
would be no less sensitive. True — and structurally identical to ADR-003's
soundness argument for scoping the harness at the proposer, which ADR-004
itself had to widen when it stopped holding. Measuring the bytes the run
publishes needs no such argument and cannot go stale when §6.3 lands.

Cost: the map-scoped invariant test went **345s → 362s** (~100 extra tasks).

The factor table and the convert params are the **baseline's** on every
replicate, exactly as the ruleset is. A per-replicate conversion table would
add a second moving part and a red verdict would stop saying which one moved.

## The fixture, and why it is 200 rows

`tests/fixtures/make_fixtures.py`: `units_pack`, `units_samplesheet`,
`confirm_ledger_units`, `units_analyte_blind_factors`,
`units_missing_factor_factors`.

Two analytes whose canonical units differ by three orders of magnitude once
each is in its own SI unit — creatinine at 88.4 µmol/L per mg/dL, glucose at
0.05551 mmol/L per mg/dL. **That pairing is the fixture's whole point.** If
the pack declared mmol/L for both, an analyte-blind table would be off by a
factor of 1.6 and land creatinine *inside* its own plausible range: the card's
Trap says the values are "plausible, wrong, and indistinguishable", and a
fixture that could not detect its own sabotage would prove nothing.

**200 rows, and one deliberate outlier** (creatinine 5000 mg/dL — 5.000 with
the point lost). `--plausible_range_quantiles` exists so "outliers alone
cannot fail it", and that claim is untestable on a short table: p1 and p99 of
twelve values are the min and the max, so every quantile setting behaves
identically and a test could not tell a live param from a decorative one. At
200 rows the 0.99 quantile genuinely excludes the top row, so the same fixture
passes under the default and fails under `[0.0, 1.0]`.

The pack trap the phase-3 handoff named applies in reverse and bit once
already: `nf-test.config` sets `profile = "test"` and `conf/test.config` sets
`concept_pack` to the OMOP pack, so **every test in `tests/units.nf.test`
overrides `concept_pack` explicitly**. `_UNITS_FIXTURE_HASH` was derived
empirically against that override by running the pipeline once and hashing the
published `ledger.proposed.yaml`.

## The tests

Written and confirmed failing first (§0.2 step 2 — the pre-implementation run
succeeded through `map` and produced no `qc/unit_conversions.json` at all).
Five, of which the card names one:

| # | what it holds |
|---|---|
| 1 | each analyte converted with its OWN factor; the two entries carry different factors despite sharing a `from`. **Uses the shipped `assets/ucum_factors.yaml`**, so a table that shipped empty or analyte-blind fails here |
| 2 | **the card's done-when**: creatinine converted with the glucose factor trips `implausible_after_conversion` and fails, naming the analyte and the range |
| 3 | `--fail_on_implausible_range false` records the flag and finishes |
| 4 | `--plausible_range_quantiles [0.0, 1.0]` fails on the outlier the default tolerates |
| 5 | a missing factor is refused, never defaulted to 1.0 (SIDE) |

Tests 1 and 3–5 are not padding: a done-when that only exercised the failure
path would pass against a stage that failed every run.

## What is not done — the whole remaining ladder

§0.7's build order, for the next session's planning. Cards live in
`docs/steps/`.

| §0.7 item | ladder | cards | state |
|---|---|---|---|
| 1–7 | §11.1, §1, §2, §10.1, §4, §5, §3 | — | done (phases 0–1) |
| 8 map | §6 | `s6-1` ✅ `s6-2` ✅ **`s6-3` ❌** | 2 of 3 |
| 9 derive endpoints | §7 | `s7-1`, `s7-2`, `s7-3` | not started |
| 10 coverage | §8 | `s8-1`, `s8-2`, `s8-3` | not started |
| 11 emit + manifest + provenance | §9 | `s9-1`, `s9-2` | not started |
| 12 report | §9.3 | `s9-3` | not started |
| 13 container pins + versions | §11.2 | `s11-2` | pins done as modules land; **the floating-tag audit has never run** |
| 14 generality run + grep | §10.3 | `s10-3` | not started; params seeded in phase 0 |

Known blockers, per item:

**§6.3 needs a §5 contract change, not §6 work.** Its IN slot is "mapped rows
with categorical values + **value-map rules**", and no such rule can exist
today: `bin/compile_rules.py` only ever emits `kind: "column_map"`, and
§5.1's confirmed-ledger Contract has no field carrying a value-level mapping.
Its own docstring says why — `value_map` "needs a value-level mapping table
nothing in ledger.confirmed.yaml's Contract offers". So §6.3 is: extend the
human gate with a `value_map:` field (§5.1), compile it (§5.2), then apply it
and draw the collapse. **Every prior phase has treated the §5 contract as
settled**; this is the first that cannot. The alluvial PNG is *not* a blocker
— `bin/link_resolve.py` hand-writes one with a zlib/CRC32 encoder and a 3×5
bitmap font, and publishes pixel coordinates into `link_report.json` so a test
asserts on the image rather than eyeballing it. Copy that shape.

**§6.3 and §7.3 constrain each other.** §6.3's Trap is that *best response*
must not be treated as a value to map — it is an endpoint derived from a scan
sequence under a criteria version, which is §7.3's. Read both cards before
either.

**§8.3 will need an ADR.** The standardized-difference forest legitimately
reads outcome variables *for reporting*. That is exactly why
`--invariant_scope all` is refused today and why the refusal message names
the reason rather than the phase. Expect to have to say, in writing, where
the harness's claim stops and why that is not a relaxation.

**§9.3's Quarto pin is contradicted in the repo.** §0.8 of the spec pins
**1.10.18**; `docs/plans/phase-0.md`'s Global Constraint 8 says **1.7.33**.
Nothing has needed Quarto yet so nothing has caught it. The spec is
authoritative; phase-0.md is a historical document and was not edited here.

Carried, unchanged, and not widened by this phase:

* **`docs/output.md` is still nf-core template boilerplate**, now also not
  documenting `qc/` — on top of `mapped/`, `link/` and `rules/`.
* **`CHANGELOG.md` is still the untouched nf-core template.** No phase has
  maintained it; this one did not start.
* **The new tests do not snapshot `versions.yml`**, adding to the
  `nf_test_content` lint failures already sitting behind the nf-core/tools
  crash.
* **The permuted tables are passed by absolute path, not staged.** Pre-existing
  §3/§6 property. `CONVERT_UNITS` does **not** extend it — its `mapped_in/`
  and its factor table are real staged `path` inputs.

## Known-red, already diagnosed — do not re-investigate

Unchanged: `.github/workflows/linting.yml` fails on the first PR because
nf-core/tools 4.1.0 corrupts `array` and `object` schema defaults
(`docs/upstream/nf-core-tools-array-defaults.md`, drafted and **not filed** —
filing is the maintainer's call). 25-plus ordinary lint failures sit behind
that crash. Note that `plausible_range_quantiles` is a new `array` default, so
this phase adds one more property to that blast radius.

`nextflow_schema.json` is hand-formatted with inline arrays and literal UTF-8
`§`. This phase added 29 lines and deleted none; nothing was round-tripped
through `json.dump`.

## Housekeeping

* `.superpowers/` is gitignored and holds phase 0's ledger and rulings.
  Nothing from phases 1–4 was written there.
* Commit messages use gitmoji `:shortcode:` prefixes.
* Do not push, open a PR, or file an upstream issue without being asked.
