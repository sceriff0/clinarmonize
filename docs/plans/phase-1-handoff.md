# Phase 1 handoff — §10.2 fixtures and §3 link

Covers build-order item 7 of §0.7 (§10.2's fixture set) and §3 in full
(s3-1, s3-2, s3-3). Written to the same standard as
`docs/plans/phase-0-handoff.md`: what is built, what is proven, and the
exact limits of that proof.

**One blocking caveat, stated up front: nothing here is verified under a
container.** See "What is not done" before trusting any of it.

## Stage graph as built

| §0.9 stage | state | entry point |
|---|---|---|
| §1 ingest | built (phase 0) | `subworkflows/local/ingest/` |
| §2 profile | built (phase 0) | `modules/local/profile_columns/` |
| §3 link | **built this phase** | `link_blocking/`, `link_score/`, `link_resolve/` |
| §4 propose | built (phase 0) | `propose_candidates/`, `propose_channels/`, `propose_ledger/` |
| §5 confirm | built (phase 0) | `confirm_ledger/`, `compile_rules/` |
| §6–§9 map/derive/coverage/emit | **not built** — §0.7 items 8–12 | — |
| §10.1 invariant harness | built (phase 0) | `permute_outcome/`, `invariant_report/` |
| §10.2 fixtures | **built this phase** | `conf/test.config`, `conf/test_full.config` |

`implementedStages()` now holds `ingest, profile, link, propose, confirm` and
so happens to be a contiguous prefix of `stageGraph()` again. **That is a
coincidence, not a repair.** The gate still checks the *requested* stage's own
membership rather than walking the graph for the first gap, because §6–§9 are
unbuilt and the identical situation recurs the moment any later stage is built
out of graph order. Do not "fix" it into a prefix walk.

**Suite: 49/49 green** under `-profile test` (43 at the end of phase 0).

## §10.2 — what the fixture set now proves

**The gap that was closed.** `-profile test` paired Eunomia GiBleed with
`assets/packs/minimal.yaml`, whose variable names have no trigram or
value-set overlap with OMOP CDM 5.3's headers. It produced 35 candidate
rows, **zero distinct variables**, and an empty `ledger.proposed.yaml` — correct
under §4.1's SIDE clause, and an acceptance run that proved the pipeline
*ran* and never that it *mapped*.

| | `-profile test` | `-profile test_full` |
|---|---|---|
| fixture | Eunomia GiBleed | MIMIC-IV demo v2.2 |
| pack | `assets/packs/omop_cdm53.yaml` | `assets/packs/clinical_core.yaml` |
| fetched by | `tests/fixtures/fetch_eunomia.py` | `tests/fixtures/fetch_mimic_demo.py` |
| candidate rows | 36 | 50 |
| distinct variables | 4 | 5 |
| generators exercised | `name_ngram`, `value_set` | all three |
| ledger entries with proposals | 11 | 7 |

**Neither fixture is sufficient alone, and this is the point.** Eunomia is
already OMOP, so matching OMOP names to OMOP columns is §10.2's Trap
verbatim: the matcher can be badly broken and it will still score well. It
buys a fast, non-zero CI signal and nothing else. MIMIC-IV demo is where the
matcher is actually tested — `sex` is reachable from the column `gender` by
`value_set` **alone** (there is no trigram overlap at all), which is §4.1's
own Trap made real, and it is the only fixture where the `unit` generator
fires.

**Asserted, not merely fixed.** `tests/default.nf.test` gained a property
test: at least one column resolves to at least one distinct variable, and the
outcome-flagged variable resolves to none. Confirmed failing first against
the old pack (`ledger = []`) and passing against the new one, with the
pre-existing ingest snapshot green either way. The gap was silent; a fixture
change alone would let it reopen silently.

**MIMIC-IV demo is open access** (ODC-BY, no PhysioNet credentialing) — which
is why §10.2's Alternatives table names the demo and rejects full MIMIC-IV.
Nothing fetched is committed; `test_data/` is gitignored.

## §3 — what link proves, and what it does not

All three cards' done-when clauses pass:

| clause | result |
|---|---|
| s3-1: `--stop_after block`, `jq -e '.[] \| .n_records_unblocked'` | every rule reports its pair count and the unblocked count, including at zero |
| s3-2: `nf-test test tests/link_score.nf.test` | a pair with a **missing** field scores **14.65**; the otherwise-identical pair **disagreeing** on it scores **11.34** |
| s3-3: `test -s match_histogram.png`, `jq -e '.collapse_ratio > 0.5'` | histogram written unconditionally, both thresholds drawn, ratio 0.9986 on MIMIC / 0.667 on the fixture |

**The three-way handling is observable in the weight, which is the whole
step.** `missing` contributes **+0.30** to the match weight and `disagree`
contributes **−3.02** — near-zero evidence either way versus evidence
against. The two fixture pairs differ in exactly one comparison level by
construction, and the test asserts that too, so the comparison stays about
missingness if the fixture ever drifts. Folding missing into disagreement
(§3.2's Trap) makes the two weights equal *by construction* and the test goes
red. Verified by inverting the assertion and observing it fail.

**The invariant is enforced inside the stage, by the flag.**
`bin/link_blocking.py` refuses a blocking rule keyed on a column named by an
outcome-flagged pack variable, and `bin/link_score.py` refuses a comparison
field on one. Both refuse by the pack's `outcome: true` flag, never by a
hard-coded column name, and both **kill the run** rather than dropping the
offending rule — a silently dropped blocking rule lowers the recall ceiling
with nothing in any report to say so, and §3.1's Why is that no later stage
can tell a pair was excluded.

### §10.1 does not widen in this change, and here is exactly when it must

The harness measures `ledger.proposed.yaml` (`invariant_scope == 'propose'`,
ADR-003), and **no value computed by §3 reaches the proposer** —
`PROPOSE_CANDIDATES` consumes profiles, not links. §0.9's dependency
direction is that §2 feeds §4 while §3 feeds §6, and §6 is unbuilt, so
link's outputs are terminal today.

§3 *does* compute joint statistics across datasets — m, u and match weights
are precisely that. **The moment §6 consumes `links.parquet`, the invariant's
scope has to widen with it, in that change and not after it.** The phase-0
handoff's standing warning is unchanged and now has a second trigger
alongside the profiler recording a joint statistic.

### Decisions worth knowing about

- **`--stop_after block` is a sub-stage, not a tenth stage.** s3-1's
  done-when stops after blocking while s3-3's stops after all of link.
  `stageGraph()` is §0.9's nine-stage list and is untouched; `subStages()`
  maps `block -> link`, so `block` answers every ordering question with
  link's position and additionally halts the link block after §3.1.
  `graph.indexOf('block')` is `-1`, which would silently read as "before
  ingest" and skip every stage — hence `graphStage`, resolved once.

- **EM is only identified up to a label swap.** A two-class mixture
  likelihood is identical if you call the match class the non-match class,
  and EM converges to the mirror solution readily — on MIMIC's first run it
  produced a model in which agreeing on every field scored **−27 bits**, a
  linkage exactly backwards that still reported convergence. The identifying
  constraint comes from outside the likelihood (*matches agree more often
  than non-matches*) and is applied once after convergence.
  `model.json.provenance.em_relabelled_to_identify_match_class` records
  whether it fired; a run where it fires repeatedly has blocking rules worth
  a second look.

- **The histogram is drawn by hand** (`bin/link_resolve.py`, ~150 lines of
  zlib and struct), not by matplotlib. §0.8 pins the toolchain and the image
  is duckdb + PyYAML + procps; adding a plotting library to draw one bar
  chart means rebuilding and re-pinning across twelve module copies. The
  hand-written PNG is also byte-deterministic, which a rasterised figure is
  not. **No dependency outside §0.8's pins was introduced.**

- **"Both thresholds are drawn on it" is read back off the image.**
  `link_report.json` publishes each threshold's pixel column and RGB, and
  the test reads those pixels. An assertion no one can make is an eyeball
  check that quietly stops happening.

- **`person_id` is a content hash of the cluster's sorted membership**, not a
  counter — same reasoning as §5.2's `rule_id`. Re-running on the same
  records with the same links yields the same id, and adding an unrelated
  cohort renumbers nobody.

- **u is enumerated, not sampled, when the pair population is smaller than
  `link_random_pair_n`.** Drawing 1,000,000 times with replacement from the
  276 distinct pairs of a 24-record fixture converges to exactly the
  enumerated answer, only slower and with sampling noise. Enumeration makes
  u exact for small inputs and removes the seed from the result;
  `model.json` reports `random_pairs_drawn` either way.

- **EM runs over collapsed comparison vectors.** With F fields at three
  levels there are at most 3^F distinct vectors (243 for the five-field
  default) however many pairs there are. The estimates are *identical*, not
  approximate — the weights are the multiplicities.

## Traps found while building (each cost real time)

- **`on:` is boolean `true` in YAML 1.1.** PyYAML resolves the bare token the
  way it resolves `yes`/`no`, so a rule file matching §3.1's published
  contract arrives with the key `True`, not `"on"`. Both spellings are
  accepted in code so the shipped asset can keep matching the contract.

- **A YAML flow sequence splits on every comma.** §3.1's contract writes
  `on: [substr(last_name,1,3), birth_year]`, which YAML reads as **four**
  items — `substr(last_name`, `1`, `3)`, `birth_year`. Transcribed verbatim,
  the shipped rule file cannot parse at all. The expression must be quoted.
  Caught by `tests/link_score.nf.test`, not by review.

- **Blocking must never materialise pairs in Python.** The first
  implementation `fetchall()`-ed the join and died with an
  `OutOfMemoryException` on MIMIC-IV demo — in the step whose entire job is
  keeping a quadratic problem tractable. Everything now happens in SQL and
  goes from the join straight to parquet.

- **`max_block_size` guards block SIZE, not pair COUNT**, exactly as §3.1's
  Params table specifies. A hundred blocks of 452 rows each are all under the
  10000 default and still produce tens of millions of pairs.
  `conf/test_full.config` turns it down to 200 so the guard actually fires on
  MIMIC's EAV `labevents` table; the report then names what that cost —
  88 blocks skipped covering 107,242 records.

- **A nested parquet type arrived, so the probe widened.**
  `bin/link_score.py` writes `per_field_agreement` as
  `LIST(STRUCT(field, level, weight))` and `bin/link_resolve.py` writes
  `source_row_id` as `VARCHAR[]`. `tools/parquet_roundtrip_probe.py` gained
  both, with an empty list, a NULL list, a one-element list and a NULL inside
  a struct — three of those look alike in a printout and are different
  encodings. Sabotage-checked: swapping a NULL list for an empty one turns
  the probe red. The nested fixtures are inserted from **SQL literals**, not
  parameter binding, because how a Python list binds to a LIST column is
  exactly the kind of detail that can differ between two duckdb versions, and
  a probe whose write path depends on the thing it probes cannot fail
  honestly.

- **`matching_locked_model` was pinned to a literal pack path.**
  `computeParamsHash()` folds `concept_pack` into the hash, so changing
  `-profile test`'s pack detached the fixture from the profile and the
  successful `--unseal` test went red. It now READS `conf/test.config`.

## What is not done

**F2 remains open, and could not be closed on this machine.** Docker is
running, but `linux/amd64` emulation hangs — `docker run --platform
linux/amd64 alpine uname -m` never returns, which is the exact failure
`containers/duckdb-pyyaml/README.md` documents for Apple Silicon without
Rosetta. The pinned image is amd64-only. Every result quoted above therefore
comes from `-profile test` against a host environment pinned to the
container's own versions (duckdb 1.5.5, pyyaml 6.0.2) — the same scripts
through the same processes, **without container isolation**.

Global Constraint 6 is still not demonstrated, and the container round-trip
the widened probe exists to measure has only been run 1.5.5 → 1.5.5, not
1.5.5 → 1.4.5. **First action for the next session, on the cluster:**

```
sbatch tools/verify_clinarmonize.sh
```

The harness globs `modules/local/*/environment.yml`, so it picks up the three
new modules' conda specs automatically (twelve copies now, not nine), and
section 6d runs the widened probe before any test.

**Also not done, and deliberately:**

- **§6–§9 are untouched.** The kickoff is explicit: do not start them in the
  same session as §3.
- **`tests/ingest.nf.test`'s gate test had to move.** It named `link` as its
  example of an unimplemented stage, so building §3 made it pass and the test
  went red for the right reason. It now names `map` (§6), and says in its own
  comment that it has to move again when §6 lands — a gate test naming an
  implemented stage asserts nothing. A second case was added asserting the
  other half: `--stop_after link` is now reachable and `--stop_after block`
  stops partway through it.
- **`test_full` is not wired into CI.** §10.2 calls it nightly, its done-when
  is a bare `nextflow run`, and CI has no fetch step for it. Adding it to
  `tests/` would run a 107k-row fixture on every push.
- **The `unit` generator is unexercised by `-profile test`.** Eunomia has no
  unit-bearing headers. `-profile test_full` covers it.
- **`n_clerical` is 0 on `-profile test_full`.** The comparison fields exist
  only on `admissions`, so most pairs cannot agree and nothing lands in the
  band. The purpose-built fixture exercises it (2 pairs).
- **`minimum`/`maximum` bounds are live, but not from the CLI.** nf-schema
  types a CLI-supplied numeric as a string and rejects on TYPE before it
  reaches the domain check, so `--max_pairs_warn_frac 5.0` fails with "Value
  is [string] but should be [number]" rather than "5 is greater than 1". Via
  `-params-file` the domain check fires properly and reports the bound. Both
  paths reject, so §11.1's done-when ("an out-of-domain param value is
  rejected") holds — but the bound itself is only *exercised* from config.
  This is pre-existing repo-wide behaviour affecting phase 0's numeric params
  equally, not something §3 introduced.

- **The `.subscribe { error(...) }` gate** carried forward from phase 0 is
  unchanged. Link gained no such pattern — all three processes fail through
  a real process exit.

## The MIMIC histogram is itself a finding

`link/match_histogram.png` under `-profile test_full` shows a single mass at
−14.4 and a thin tail near +42, with **no overlap region** between the
thresholds. §3.1's Trap names that shape exactly: "a match-weight histogram
with no overlap region is that mistake showing up as an apparently clean
result." It is what blocking on the source system's own person key produces,
and `assets/blocking_rules_fixture.yaml` says so in its own header. The
figure is doing its job; the linkage it depicts is the easy case.

## Where the record lives

`.superpowers/` is gitignored and holds phase 0's ledger and rulings. Nothing
from this phase was written there.
