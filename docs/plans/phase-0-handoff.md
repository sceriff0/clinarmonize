# Phase 0 handoff — what is built, what is proven, and what the proof does not cover

Phase 0 covers build-order items 1–6 of §0.7. It is complete: 43 nf-test tests, all passing.
Its stated end condition — "§0.1's falsification test runs and prints one hash for N
permutations" — is met, and met in both directions: the harness goes green on a clean
pipeline and red on a deliberately leaky one.

**One blocking caveat, stated up front: the entire phase is verified only under
`-profile test`.** See "The one thing that is not done" below before trusting any of it.

## Stage graph as built

| §0.9 stage | state | entry point |
|---|---|---|
| §1 ingest | built | `subworkflows/local/ingest/`, seal in `workflows/harmonize.nf` |
| §2 profile | built | `modules/local/profile_columns/`, `manifest_profiling_failures/` |
| §3 link | **not built** — §0.7 item 7 | — |
| §4 propose | built | `propose_candidates/`, `propose_channels/`, `propose_ledger/` |
| §5 confirm | built | `confirm_ledger/`, `compile_rules/` |
| §6–§9 map/derive/coverage/emit | **not built** — §0.7 items 8–12 | — |
| §10.1 invariant harness | built | `permute_outcome/`, `invariant_report/` |

`implementedStages()` in `workflows/harmonize.nf` is deliberately **non-contiguous** with
`stageGraph()`: it holds `ingest, profile, propose, confirm` but not `link`. §0.7 puts link at
item 7, after propose and confirm, "so the invariant work is not blocked behind entity
resolution", and §0.9 confirms the dependency — §2 feeds §4, while §3 feeds §6. The stage gate
therefore checks the *requested* stage's own membership rather than walking the graph for the
first gap. Do not "fix" it back into a prefix walk when you build link.

## What §10.1 actually proves

The headline result is `tests/invariant.nf.test`: 100 permutations of the outcome column,
one ledger sha256, `verdict: 'proof'`. It is a real measurement, not a tautology —

- the perturbation is applied to the **input table bytes**, not to a derived intermediate;
- every replicate re-derives through an **alias of the real profiler**, not a test-only copy;
- the **unpermuted baseline is inside the hashed set**, so one hash means the real-outcome
  ledger is byte-identical to 100 shuffled ones;
- the marginal guard compares two **independently derived** multisets (the input file against
  the re-read written file), so it can actually fail;
- `tests/invariant_leak_control.nf.test` sabotages a channel into reading the outcome and
  **observes the harness go red** (`verdict: 'leak'`, `n_distinct_hashes: 11`).

**The exact claim, with both qualifiers, is narrower than "no outcome leak":**

> No outcome leak large enough to move a rounded, top-k-truncated ledger, **through a
> candidate the outcome column does not itself supply.**

Two independent limits produce those qualifiers.

**1. A sensitivity floor.** `score_type_agreement` is binary 1.0/0.0; `score_cardinality` and
an exact name match clamp at 1.0; the ledger rounds to `ledger_float_precision` and truncates
to `ledger_top_k`. A leak landing on an already-saturated channel, moving a total by less than
5e-5, or reordering only below top-k yields a byte-identical ledger and still certifies
`proof`. This is a **spec consequence, not a defect**: §4.3 mandates the rounding precisely so
"a float's last bit cannot change the hash", and removing it would make every permutation hash
differ, report a leak on every run, and get the gate switched off — the card's Trap exactly.
Do not remove the rounding.

**2. Non-reachability.** The outcome column can never appear in the ledger at all, because
§4.1 forbids generating a candidate for an outcome-flagged variable. So §10.1's power comes
entirely from the possibility of **cross-column influence** — and today no channel does
cross-column work; each scores one column's profile against one pack variable. The test
currently confirms a property the architecture already guarantees.

**It becomes load-bearing the moment a cross-column signal exists.** Specifically:

- §4.2's own Alternatives table names "a seventh channel: schema context" and warns that it
  "couples columns, so the permutation test in §10.1 must widen its scope";
- §3 link computes joint statistics across datasets;
- the profiler is per-column today, so no joint statistic exists — **the moment §2 records
  one, that is the leak surface.**

## Contracts later phases consume

Each was specified in its task report (`.superpowers/sdd/phase-0/task-*-report.md`) and
verified by the consuming task's review. Producer and consumer agree, including on
null/absent/empty cases.

```
profiles/<cohort>.<dataset>.json        one typed evidence record per COLUMN (§2.1)
  -> propose/candidates.parquet         + excluded_candidates / excluded_variables (§4.1)
  -> propose/evidence.parquet           + status / effective_weight per channel (§4.2)
  -> ledger.proposed.yaml               sorted, rounded, byte-deterministic (§4.3)
  -> ledger.confirmed.yaml              human-written; matched on (cohort,dataset,column)
                                        and guarded by proposed_hash (§5.1)
  -> rules/ruleset.json                 rule_id = sha256(kind, from, to, params) (§5.2)
```

Three of these are easy to break by accident:

- **`effective_weight` is not decoration.** A candidate's total is
  `sum(score * effective_weight)` over rows whose `score` is non-null. A channel with no
  reference (today: `distribution_distance`, which needs confirmed columns that only exist
  once §5 has run at least once) is **excluded** and its weight redistributed proportionally.
  Treating a null score as 0 would drag every total down and read as evidence against the
  candidate.
- **`rule_id` is a published content hash.** Reordering the confirmed ledger must leave every
  id unchanged. Anything derived from position or time makes an audit against an older output
  resolve to the wrong rule — worse than failing to resolve.
- **`ledger.proposed.yaml` must stay byte-deterministic.** Every §10.1 result rests on it.

## The one thing that is not done

**F2 — the phase is unverified under Docker.** Docker Desktop wedged partway through this
session and would not restart. Every test result quoted anywhere in phase 0 comes from
`-profile test` with a scratch venv pinned to the container's own versions (duckdb 1.5.5,
pyyaml 6.0.2) — the same scripts through the same processes, but **without container
isolation**. Global Constraint 6 ("green under `-profile test,docker` with no sibling pipeline
installed") is therefore **not demonstrated**.

Eight local processes are affected. Two specific risks are Docker-dependent and would not have
shown up in any test run here:

1. `bin/propose_ledger.py` **sibling-imports** `bin/propose_channels.py` (for
   `CHANNEL_WEIGHT_KEY` and the filename sanitiser). Nextflow stages `bin/` onto `PATH`, not
   necessarily onto `PYTHONPATH`. This is a new pattern in this repo. It works under
   `-profile test`; it is unproven under container staging.
2. The digest-pinned Wave image is reused by every new process but was only ever *exercised*
   under Docker by Tasks 2–3.

**First action for the next session:** start Docker and run

```
export NXF_VER=26.04.6
nf-test test --profile=+docker
```

Until that passes, treat phase 0 as verified-in-principle rather than verified.

## Carried forward

- **The default test profile proposes nothing.** `-profile test` (Eunomia GiBleed +
  `assets/packs/minimal.yaml`) yields 35 candidate rows but **zero distinct variables** and an
  empty `ledger.proposed.yaml`. That is correct behaviour — §4.1's SIDE clause requires a
  column with no candidates to be emitted with a null concept_id — but it means the default
  acceptance run proves the pipeline *runs*, never that it *maps*. The generic pack's names
  have no overlap with Eunomia's OMOP columns. §10.1's own fixture is separate and does
  produce real proposals, so the invariant proof is unaffected. **Fix this with §10.2 (item 7+)
  before trusting any later stage's CI**, or §6/§7 will be built and "pass" against zero rows.
- **A `.subscribe { error(...) }` gate.** §5.1's no-ledger gate uses one, which contradicts
  the caution written at the top of `modules/local/manifest_profiling_failures/main.nf` — an
  `error()` inside a subscribe callback runs on a dataflow thread and does not surface the way
  a failing process does. It fails visibly in the path the tests exercise. If the confirm gate
  ever gains downstream work, or the pattern is reused, convert it to a real process.
- **Deferred minors** are listed in `.superpowers/sdd/phase-0/progress.md` (search
  `minor (deferred)`). The final review triaged them all as carry-forward.
- **`distribution_distance` goes live once §5 has produced confirmed references.** Re-run the
  negative control at that point — the channel changes from "always excluded" to "scores", and
  that is a change in the harness's own surface.

## Where the record lives

`.superpowers/` is **gitignored**, so nothing below is in git — back it up before cleaning:

- `.superpowers/sdd/phase-0/progress.md` — the ledger, including all 31 rulings with their
  reasoning and cost-if-wrong
- `task-N-report.md` / `task-N-review.md` — per task
- `final-review.md`, `final-fix-report.md` — the whole-branch review and its fix wave
