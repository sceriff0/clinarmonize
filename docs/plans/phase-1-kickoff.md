# Phase 1 kickoff — §10.2 fixtures, then §3 link

Paste-ready brief for a fresh session. It assumes no memory of any previous
conversation; everything it needs is either here or named by path.

## Read first, in this order

1. `docs/plans/phase-0.md` — the spec, the Global Constraints, the build protocol
2. `docs/plans/phase-0-handoff.md` — what is built, what is proven, and the exact
   limits of that proof. Read "Carried forward" and "Session 2026-08-17b" in
   full; they contain the traps.
3. `docs/steps/s10-2.md`, then `docs/steps/s3-1.md`, `s3-2.md`, `s3-3.md` — the
   cards you are implementing. Each card's `contract` names the paths; each
   card's `done` is the test you write first.
4. `containers/duckdb-pyyaml/README.md` — only if you touch the image.

## State at the time of writing

* Branch `main`, HEAD `591553e`. Phase 0 (build-order items 1–6) is complete:
  43/43 nf-test tests green under `-profile test,singularity` on the cluster at
  commit `37e7c2b`, image pinned at
  `docker.io/bolt3x/clinarmonize-duckdb@sha256:056f3260…`.
* `main` is ahead of `origin/main` and **not pushed**. No PR has ever been opened.
* Stage graph: §1 ingest, §2 profile, §4 propose, §5 confirm and the §10.1
  invariant harness are built. **§3 link and §6–§9 are not.**

## What to build, in this order

### 1. §10.2 first — and this ordering is not negotiable

`-profile test` (Eunomia GiBleed + `assets/packs/minimal.yaml`) yields 35
candidate rows but **zero distinct variables** and an empty
`ledger.proposed.yaml`. That is correct behaviour — §4.1's SIDE clause requires
a column with no candidates to be emitted with a null concept_id — but it means
the default acceptance run proves the pipeline *runs*, never that it *maps*.
The generic pack's names have no overlap with Eunomia's OMOP columns.

Build §6 or §7 on top of that and they will be built, and will "pass", against
zero rows. Fix the fixture gap first: `docs/steps/s10-2.md`.

§10.1's own fixture is separate and does produce real proposals, so the
invariant proof is unaffected by this gap.

### 2. §3 link — `s3-1` blocking, `s3-2` Fellegi–Sunter scoring, `s3-3` thresholding

§0.7 puts link at item 7, *after* propose and confirm, so the invariant work is
not blocked behind entity resolution. §0.9 confirms the dependency direction:
§2 feeds §4, while §3 feeds §6.

**When you add link, do not "fix" `implementedStages()` in
`workflows/harmonize.nf` back into a prefix walk.** It is deliberately
non-contiguous with `stageGraph()` — it holds `ingest, profile, propose,
confirm` but not `link`, and the stage gate checks the *requested* stage's own
membership rather than walking the graph for the first gap. Add `link` to the
set; leave the mechanism alone.

### 3. Then §6–§9

`s6-*` map, `s7-*` derive, `s8-*` coverage, `s9-*` emit — in card order, only
after §10.2 and §3 are green. Do not start these in the same session as §3
unless §3 is fully green and committed first.

## Build protocol — §0.2, binding

Write the failing test from the card's `done` slot **first**, and confirm it
fails, before any implementation. Implement to `contract`, at the paths
`contract` names. Nothing more. Honour `nogo` — absent instruction is not
licence.

All eight Global Constraints in `docs/plans/phase-0.md` are binding on every
task; violations are defects whether or not the card repeats them. The two
easiest to breach silently:

* **§0 the invariant.** No harmonization decision may be a function of the
  outcome variable. The pack's `outcome: true` flag is what "the outcome
  variable" means — never a hard-coded column name.
* **§11.1 no magic numbers.** Every `params` row in a card becomes a
  `nextflow_schema.json` property with that exact default; `domain` becomes
  `minimum`/`maximum` or `enum`; `effect` becomes `help_text`. A schema default
  differing from a process default is a defect.

## Traps that will cost you a session each

* **Do not remove the ledger's rounding or top-k truncation.** §4.3 mandates
  them so "a float's last bit cannot change the hash". Removing them makes every
  §10.1 permutation hash differ, reports a leak on every run, and gets the gate
  switched off. `ledger.proposed.yaml` must stay byte-deterministic — every
  §10.1 result rests on it.
* **`effective_weight` is not decoration.** A candidate's total is
  `sum(score * effective_weight)` over rows whose `score` is non-null. A channel
  with no reference is excluded and its weight redistributed proportionally.
  Treating a null score as 0 drags every total down and reads as evidence
  *against* the candidate.
* **`rule_id` is a published content hash** — `sha256(kind, from, to, params)`.
  Reordering the confirmed ledger must leave every id unchanged. Anything
  derived from position or time makes an audit against an older output resolve
  to the wrong rule, which is worse than failing to resolve.
* **§10.1 becomes load-bearing the moment a cross-column signal exists.** Today
  no channel does cross-column work, so the permutation test confirms a property
  the architecture already guarantees. §3 link computes joint statistics across
  datasets, and §4.2's Alternatives table names a "schema context" channel that
  "couples columns, so the permutation test in §10.1 must widen its scope". The
  profiler is per-column today — **the moment §2 records a joint statistic, that
  is the leak surface.** Widen §10.1 in the same commit that introduces it.
* **Widen `tools/parquet_roundtrip_probe.py` when a stage first writes a nested
  type.** LIST / STRUCT / DECIMAL / TIMESTAMP is where the container's duckdb
  1.5.5 and the cluster host's 1.4.5 have a genuinely new encoding surface.
  VARCHAR / BIGINT / DOUBLE is already proven identical in both directions.
* **`.subscribe { error(...) }`.** §5.1's no-ledger gate uses one, which
  contradicts the caution at the top of
  `modules/local/manifest_profiling_failures/main.nf` — `error()` inside a
  subscribe callback runs on a dataflow thread and does not surface the way a
  failing process does. If the confirm gate gains downstream work, or you reuse
  the pattern, convert it to a real process.
* **`distribution_distance` goes live once §5 has produced confirmed
  references.** Re-run the §10.1 negative control at that point: the channel
  changes from "always excluded" to "scores", which is a change in the harness's
  own surface.

## Verifying

Local, fast:

```bash
export NXF_VER=26.04.6
python3 tests/fixtures/make_fixtures.py
nf-test test tests/<the-test-you-wrote>.nf.test --profile=+docker
```

Full, on the cluster:

```bash
sbatch tools/verify_clinarmonize.sh             # everything, ~2h
CHUNK=fast sbatch tools/verify_clinarmonize.sh  # the quick tests, ~15m
```

The harness asserts two preconditions before any test runs and dies on either:
conda-spec drift between `containers/duckdb-pyyaml/environment.yml` and the nine
module copies, and a container→host parquet round-trip. Both failures mean the
results below them are meaningless, not merely red.

## Known-red, already diagnosed — do not re-investigate

* **`.github/workflows/linting.yml` will fail on the first PR.** Not your bug:
  nf-core/tools 4.1.0 corrupts `array` and `object` schema defaults in two
  places. Full diagnosis, reproducer and patches are in
  `docs/upstream/nf-core-tools-array-defaults.md`. That issue is **drafted, not
  filed** — filing is the maintainer's call, so do not file it unprompted.
* **25 ordinary lint failures sit behind that crash**, mostly 11×
  `nf_test_content` "does not snapshot a 'versions.yml' file". If you are asked
  to make CI green, that list is the work — see the handoff for the breakdown.
  New tests you write add to it unless they snapshot `versions.yml`.

## Housekeeping

* `.superpowers/` is **gitignored** — the phase-0 ledger, all 31 rulings, and
  every task report/review live there and are not in git. Back it up before
  cleaning.
* Commit messages use gitmoji `:shortcode:` prefixes.
* Do not push, open a PR, or file an upstream issue without being asked.
