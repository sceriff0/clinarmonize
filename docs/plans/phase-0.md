# Phase 0 — through the invariant

Spec: `docs/schematics/p-harmonize.html` (step cards extracted to `docs/steps/*.md`).
Scope: build-order items 1–6 of §0.7. Ends when §0.1's falsification test runs and
prints one hash for N permutations.

## Global Constraints

Binding on every task. Copied from the spec; violations are defects regardless of
whether the task text repeats them.

1. **§0 the invariant.** No harmonization decision — column match, value mapping,
   unit conversion, endpoint derivation, or linkage threshold — may be a function of
   the outcome variable. The pack's `outcome: true` flag is what "the outcome
   variable" means; never a hard-coded column name.
2. **§0.2 build protocol.** Write the failing test from the card's `done` slot
   FIRST and confirm it fails. Implement to `contract`, at the paths `contract`
   names. Nothing more. Honour `nogo` — absent instruction is not licence.
3. **§11.1 no magic numbers.** Every `params` row in a card becomes a
   `nextflow_schema.json` property with that exact default. The `domain` column
   becomes `minimum`/`maximum` or `enum`; the `effect` column becomes `help_text`.
   Neither is optional. A schema default differing from a process default is a
   defect (§12).
4. **§0.5 principle 10 / §10.3 generality.** No disease term, gene symbol, or
   cohort name in `modules/` or `workflows/` — and not as a hard-coded range, unit,
   or ordering either.
5. **§0.5 principle 7 / §11.2.** Every process emits `versions.yml`. Every
   container reference is digest-pinned; never a floating tag.
6. **§0.5 principle 3.** Standalone execution: green under `-profile test,docker`
   with no sibling pipeline installed.
7. **Build behind the `alts` seam** even when only one alternative exists today.
8. **Toolchain pins** (§0.8, as built): Nextflow 26.04.6 · nf-core/tools 4.1.0 ·
   nf-test 0.9.5 · DuckDB 1.5.5 · Quarto 1.7.33. Do not introduce a dependency
   outside these without saying so in the report.

## Tasks

### Task 1 — Repo skeleton and the params schema (§11.1, §0.6)
Cards: `docs/steps/s11-1.md`.
Create the nf-core template layout of §0.6 and a `nextflow_schema.json` that
validates. Seed it with the §11.1 params rows only (`validate_params`,
`schema_ignore_params`, `params_hash_file`) plus `input`, `outdir`,
`concept_pack`, `stop_after`. Later tasks add their own rows to a file that must
already exist and validate. `nextflow run . --help` must render.
Done: `nextflow run . --help` renders every seeded param with its stated default;
an out-of-domain param value is rejected.

### Task 2 — Ingest and the holdout seal (§1.1, §1.2)
Cards: `docs/steps/s1-1.md`, `docs/steps/s1-2.md`.
The two-level samplesheet, its `schema_input.json`, and the seal applied at channel
construction in `workflows/harmonize.nf` — never inside a reading process.
Done: a duplicate `(cohort_id, dataset_id)` exits non-zero naming both rows;
`--unseal` with no matching `locked_model.json` exits 1 and stages no held-out path.

### Task 3 — Profile (§2.1, §2.2, §2.3)
Cards: `docs/steps/s2-1.md`, `docs/steps/s2-2.md`, `docs/steps/s2-3.md`.
The typed evidence record, ranked UCUM candidate units, and the failure manifest.
Done: one record per column per fixture table; `OS_MONTHS` over a 0–60 range yields
both a month and a low-confidence day candidate; a mixed-type column appears in
`_failed.json` with its raw sample and the run still succeeds.

### Task 4 — The invariant harness (§10.1)
Card: `docs/steps/s10-1.md`.
**This task is written against a proposer that does not exist yet, and its test
MUST fail at the end of this task.** That failure is the deliverable. Implement
`--permute_outcome_seed`, the within-cohort permutation, the hash collection, and
`tests/invariant/report.json`. Do not implement any part of §4 to make it pass.
Done: `nf-test test tests/invariant.nf.test` fails because `ledger.proposed.yaml`
is never produced — not because of a harness error. Report must quote the failure.

### Task 5 — Propose (§4.1, §4.2, §4.3)
Cards: `docs/steps/s4-1.md`, `docs/steps/s4-2.md`, `docs/steps/s4-3.md`.
Candidate generation from the pack, the six evidence channels, and the
deterministic ledger. Turns Task 4's failing harness green.
Done: no outcome-flagged variable ever receives a candidate; a months column
against a day-unit concept is ranked down by `unit_plausibility` alone; three
permutation seeds yield one `sha256`.

### Task 6 — Confirm and the rule compiler (§5.1, §5.2)
Cards: `docs/steps/s5-1.md`, `docs/steps/s5-2.md`.
The human gate as a file, the staleness guard, and content-hashed rule ids.
Done: with no ledger the run stops cleanly and prints the path to write; with one
it proceeds; reordering the confirmed ledger leaves every `rule_id` unchanged.
