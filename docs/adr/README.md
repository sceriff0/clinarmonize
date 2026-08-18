# Architecture decision records

Decisions the code cites by number. A comment reading "requiring a
superseding ADR" has to point at something a reader can open; until
2026-08-18 these numbers resolved to nothing in git, in `docs/`, or in the
gitignored `.superpowers/` ledger. See ADR-003's own preamble.

| ADR | Decision | Status |
|---|---|---|
| [0003](0003-invariant-scope-is-the-proposer.md) | §10.1's harness holds its claim over `ledger.proposed.yaml`, not the whole pipeline | accepted; superseded in part by 0004 |
| [0004](0004-invariant-scope-widens-to-map.md) | The trigger has fired — §6.1 consumes `link/links.parquet`, so the scope widens to `map` | accepted and implemented |
| [0005](0005-the-confirmed-ledger-gains-value-map.md) | §6.3 cannot be a §6 change: the confirmed ledger gains an optional `value_map`, because a value collapse is a decision only the gate can make | accepted and implemented |

ADR-001 and ADR-002 are not recorded. They were not found in git or in
`.superpowers/`, and no code comment cites either, so there is nothing to
reconstruct them from. Numbering starts at 0003 rather than renumbering,
because the three code sites that cite ADR-003 by number are the reason this
directory exists and renumbering would break exactly the reference it is
meant to fix.

Rulings (R1..R31) are a separate, finer-grained record from phase 0 and live
in the gitignored `.superpowers/` ledger. Where one is load-bearing for code
in git — Ruling R14, the pinned vocabulary — it is restated in full in the
file that depends on it (`bin/propose_candidates.py`, `bin/map_concepts.py`)
rather than cited by number.
