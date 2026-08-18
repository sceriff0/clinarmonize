# ADR-003 — The invariant harness is scoped to the proposer

* Status: **accepted** (phase 0), **superseded in part by [ADR-004](0004-invariant-scope-widens-to-map.md)**
* Date: recorded retrospectively, 2026-08-18
* Section: §10.1, §0

## Why this file exists at all

ADR-003 was cited in three places in the codebase before it was ever
written down:

* `workflows/harmonize.nf` — `invariant_scope`'s `take:` comment, the
  `error()` that refuses any other scope, and the comment on the §10.1
  report block
* `nextflow_schema.json` — `invariant_scope`'s `help_text`

and nowhere else. It was not in `docs/`, and it was not in the gitignored
`.superpowers/` ledger either. A code comment that says "widening it is a
design change requiring a superseding ADR" points at nothing a reader can
open, which is the same failure §9.2 names for a vocabulary release recorded
without its hash: the reference resolves for the person who wrote it and for
nobody else. This file is that decision written down, from what the code
already enforces. It records no new decision.

## Context

§0 is a claim about a function's inputs: no harmonization decision — column
match, value mapping, unit conversion, endpoint derivation, or linkage
threshold — may be a function of the outcome variable. §10.1 is the only
test of that claim, and it works by varying the input that must not matter
(permuting the outcome column within each cohort, which preserves marginals)
and asserting that a downstream artefact does not change.

Which artefact is the whole question. Two things constrain it:

* §8.3's standardized-difference forest **legitimately reads outcome
  variables**, because reporting an outcome is not a mapping decision. An
  end-to-end scope would therefore fail honestly, on a stage doing exactly
  what it is supposed to do — and a test that fails for a legitimate reason
  gets relaxed until it proves nothing. That is the failure mode §10.1's own
  nogo names ("Do not relax the assertion to 'hashes mostly agree'").
* The proposer is where the decisions actually are. §4.1 generates
  candidates, §4.2 scores them, §4.3 writes the ledger every later stage
  reads. A leak that never reaches `ledger.proposed.yaml` cannot become a
  mapping decision.

## Decision

The harness holds its claim over **`ledger.proposed.yaml`** —
`invariant_scope = 'propose'`. `permute_outcome_seed 1..N` runs
`--stop_after propose` for each seed and asserts one distinct hash across
all N.

`invariant_scope` is declared with the enum `propose|map|all` because the
scope is expected to widen as the stage graph grows, but every value other
than `propose` is **refused at run time**, with a message saying that
widening requires a superseding ADR rather than a config edit. The enum
without the refusal would let `--invariant_scope all` report `[SUCCESS]`
having measured nothing.

## Consequences

* The claim proven is narrower than §0's claim. §10.1's report says
  `invariant_scope` explicitly so that no reader mistakes one for the other.
* Every stage after `propose` is **outside the measurement**. As long as
  those stages only consume the confirmed ledger, that is sound: the ledger
  is inside the scope, so anything derived purely from it inherits the
  property.
* **The soundness argument fails the moment a post-propose stage consumes
  something that is not derived from the ledger.** That is the trigger
  condition, and it is why the phase-0 and phase-1 handoffs both carry a
  standing warning about it. See ADR-004.

## Alternatives considered

* **Static analysis of the call graph** — catches the dependency without
  running anything, and misses leakage through a derived or cached
  intermediate. §10.1's own Alternatives table.
* **Drop the outcome column entirely** — strictly stronger, since nothing
  can read what is not there; breaks §8.3's reporting, so it cannot run as
  the same test.
