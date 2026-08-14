#!/usr/bin/env python3
"""Generate the edge-case fixtures §1.1/§1.2's tests need.

These are test *inputs* (samplesheets, locked_model.json files, and the tiny
placeholder tables they point at), not cohort data (§10.2 forbids committing
real fixture data -- there is none here, every value is synthetic). One
function per fixture; main() writes them all to a target directory. Later
tasks extend this generator with their own fixtures.

Usage:
    python3 tests/fixtures/make_fixtures.py [target_dir]

Defaults to <repo_root>/test_data/fixtures (gitignored, like all of test_data/).
"""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
HOLDOUT_TIMESTAMP = "2026-08-13T00:00:00Z"

# The dataset id -profile test's fixture (test_data/samplesheet.csv, built by
# fetch_eunomia.py) marks holdout: true. Fixtures that need to hash-match a
# real `-profile test` run use this id.
DEFAULT_TEST_HELD_DATASET = "observation_period"


def _current_git_sha() -> str:
    return subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=REPO_ROOT, capture_output=True, text=True, check=True
    ).stdout.strip()


def _params_hash(input_path: str, concept_pack_path: str, allow_single_dataset: bool = False) -> str:
    """Mirrors computeParamsHash() in workflows/harmonize.nf exactly: same
    three fields, same key names, same '\\n'-joined canonical form, same
    lowercase boolean stringification. Any drift here is a drift there too --
    if this stops matching a real pipeline run's params_hash.txt, the
    algorithms have diverged and both sides need to change together."""
    canonical = "\n".join(
        [
            f"input={input_path}",
            f"concept_pack={concept_pack_path}",
            f"allow_single_dataset={str(allow_single_dataset).lower()}",
        ]
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


def _write_table(target_dir: Path, name: str) -> Path:
    """A tiny, content-free placeholder table. Ingest never reads its bytes;
    it only needs to exist for nf-schema's path `exists` check."""
    path = target_dir / "tables" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("id,value\n1,0\n")
    return path


def valid_samplesheet(target_dir: Path) -> Path:
    """Two cohorts, three datasets, one holdout row: the baseline sheet
    every other fixture is a mutation of."""
    a1 = _write_table(target_dir, "cohort_a_clinical.csv")
    a2 = _write_table(target_dir, "cohort_a_treatment.csv")
    b1 = _write_table(target_dir, "cohort_b_clinical.csv")
    rows = [
        "cohort_id,dataset_id,role,path,holdout",
        f"COHORT_A,clinical,clinical,{a1},false",
        f"COHORT_A,treatment,treatment,{a2},false",
        f"COHORT_B,clinical,clinical,{b1},true",
    ]
    path = target_dir / "valid_samplesheet.csv"
    path.write_text("\n".join(rows) + "\n")
    return path


def duplicate_samplesheet(target_dir: Path) -> Path:
    """A sheet with two rows sharing (cohort_id, dataset_id) -- the §1.1
    Trap: cohort_id alone is not the primary key."""
    a1 = _write_table(target_dir, "dup_a_clinical_1.csv")
    a2 = _write_table(target_dir, "dup_a_clinical_2.csv")
    b1 = _write_table(target_dir, "dup_b_clinical.csv")
    rows = [
        "cohort_id,dataset_id,role,path,holdout",
        f"COHORT_A,clinical,clinical,{a1},false",
        f"COHORT_A,clinical,treatment,{a2},false",  # duplicate (COHORT_A, clinical)
        f"COHORT_B,clinical,clinical,{b1},true",
    ]
    path = target_dir / "duplicate_samplesheet.csv"
    path.write_text("\n".join(rows) + "\n")
    return path


def missing_holdout_column_samplesheet(target_dir: Path) -> Path:
    """A sheet that never mentions holdout at all: absent is not false."""
    a1 = _write_table(target_dir, "no_holdout_a_clinical.csv")
    rows = [
        "cohort_id,dataset_id,role,path",
        f"COHORT_A,clinical,clinical,{a1}",
    ]
    path = target_dir / "missing_holdout_column_samplesheet.csv"
    path.write_text("\n".join(rows) + "\n")
    return path


def single_dataset_cohort_samplesheet(target_dir: Path) -> Path:
    """A cohort with exactly one dataset, for --allow_single_dataset."""
    a1 = _write_table(target_dir, "single_a_clinical.csv")
    rows = [
        "cohort_id,dataset_id,role,path,holdout",
        f"COHORT_A,clinical,clinical,{a1},false",
    ]
    path = target_dir / "single_dataset_cohort_samplesheet.csv"
    path.write_text("\n".join(rows) + "\n")
    return path


def covering_locked_model(target_dir: Path, covers: list[str] | None = None) -> Path:
    """A locked_model.json that covers the given dataset id(s) but carries a
    deliberately fake params_hash/analysis_git_sha -- covers the F4 nogo:
    "do not let --unseal proceed without a hash-matching lock". A lock that
    exists, is valid JSON, and covers the right id must still refuse if it
    does not hash-match the run about to use it. Defaults to covering
    DEFAULT_TEST_HELD_DATASET so it's directly usable against -profile
    test's fixture without an extra covers= argument."""
    covers = covers or [DEFAULT_TEST_HELD_DATASET]
    lock = {
        "analysis_git_sha": "0" * 40,
        "params_hash": "0" * 64,
        "locked_at": HOLDOUT_TIMESTAMP,
        "covers": covers,
    }
    path = target_dir / "covering_locked_model.json"
    path.write_text(json.dumps(lock, indent=2) + "\n")
    return path


def noncovering_locked_model(target_dir: Path) -> Path:
    """A locked_model.json that exists but does not cover the dataset id
    that will be requested with --unseal -- must still refuse."""
    lock = {
        "analysis_git_sha": "0" * 40,
        "params_hash": "0" * 64,
        "locked_at": HOLDOUT_TIMESTAMP,
        "covers": ["some_other_dataset"],
    }
    path = target_dir / "noncovering_locked_model.json"
    path.write_text(json.dumps(lock, indent=2) + "\n")
    return path


def profiling_fixture_table(target_dir: Path) -> Path:
    """Synthetic table for §2.1/§2.2/§2.3's profiling tests. Every column
    exercises a specific card requirement:

      PATIENT_ID        -- plain int, one value per row (baseline "one
                            record per column" sanity check).
      OS_MONTHS          -- float, 0.0..60.0: the §2.2 Done-when fixture.
                            The header says months; the range ALSO fits a
                            plausible (if wrong) days reading, so a
                            low-confidence day candidate must appear
                            alongside the header's month candidate.
      RESPONSE           -- a small, fixed vocabulary {CR,PR,SD,PD,NE}
                            repeated across rows: the §2.1 Trap. Cardinality
                            alone ("5 values") is not what identifies a
                            RECIST column against a concept's value set --
                            the SET {CR,PR,SD,PD,NE} is.
      MIXED_COL          -- deliberately mixed numeric/text (4 of 20 values
                            are "not_a_number"): the §2.3 Done-when fixture,
                            a column that must appear in _failed.json with
                            its raw sample while the run still succeeds.
      ALL_MISSING_COL    -- every value is "" (a default na_string): the
                            §2.3 Trap. This must be a SUCCESSFUL profile of
                            an empty column (inferred_type "empty"), never a
                            failure -- a later stage needs it
                            present-but-empty to tell "never measured" from
                            "no such column".
    """
    n = 20
    responses = ["CR", "PR", "SD", "PD", "NE"]
    header = ["PATIENT_ID", "OS_MONTHS", "RESPONSE", "MIXED_COL", "ALL_MISSING_COL"]
    rows = [header]
    for i in range(n):
        os_months = round(i * 60 / (n - 1), 1)  # 0.0 .. 60.0
        response = responses[i % len(responses)]
        mixed = "not_a_number" if i % 5 == 0 else str(i)
        rows.append([str(i + 1), str(os_months), response, mixed, ""])
    path = target_dir / "tables" / "profiling_fixture.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(",".join(r) for r in rows) + "\n")
    return path


def profiling_samplesheet(target_dir: Path) -> Path:
    """Points --input at profiling_fixture_table()'s single table."""
    table = profiling_fixture_table(target_dir)
    rows = [
        "cohort_id,dataset_id,role,path,holdout",
        f"COHORT_PROFILE,profile_test,clinical,{table},false",
    ]
    path = target_dir / "profiling_samplesheet.csv"
    path.write_text("\n".join(rows) + "\n")
    return path


def encoding_fixture_table(target_dir: Path) -> Path:
    """A table with one column (BAD_ENCODING) containing an invalid UTF-8
    byte sequence in one cell -- §2.3's "encoding" failure reason. Written in
    binary mode; the rest of the table is plain ASCII so only that column's
    values are affected."""
    path = target_dir / "tables" / "encoding_fixture.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = [
        b"LABEL,BAD_ENCODING",
        b"row1,ok",
        b"row2,bad_\xff_byte",  # 0xFF is never valid UTF-8 on its own
        b"row3,ok",
    ]
    path.write_bytes(b"\n".join(rows) + b"\n")
    return path


def encoding_samplesheet(target_dir: Path) -> Path:
    """Points --input at encoding_fixture_table()'s single table."""
    table = encoding_fixture_table(target_dir)
    rows = [
        "cohort_id,dataset_id,role,path,holdout",
        f"COHORT_ENCODING,encoding_test,clinical,{table},false",
    ]
    path = target_dir / "encoding_samplesheet.csv"
    path.write_text("\n".join(rows) + "\n")
    return path


def matching_locked_model(target_dir: Path) -> Path:
    """A locked_model.json that hash-matches a real `-profile test` run
    exactly: same input (test_data/samplesheet.csv), same concept_pack
    (assets/packs/minimal.yaml), same allow_single_dataset (false), and the
    actual current git HEAD. Covers DEFAULT_TEST_HELD_DATASET, the dataset
    -profile test's fixture marks holdout: true. This is the fixture that
    proves the successful --unseal path actually admits a held-out row
    (F2) -- covering_locked_model()'s fake hash cannot do that once F4's
    hash check is enforced."""
    input_path = str(REPO_ROOT / "test_data" / "samplesheet.csv")
    concept_pack_path = str(REPO_ROOT / "assets" / "packs" / "minimal.yaml")
    lock = {
        "analysis_git_sha": _current_git_sha(),
        "params_hash": _params_hash(input_path, concept_pack_path),
        "locked_at": HOLDOUT_TIMESTAMP,
        "covers": [DEFAULT_TEST_HELD_DATASET],
    }
    path = target_dir / "matching_locked_model.json"
    path.write_text(json.dumps(lock, indent=2) + "\n")
    return path


def invariant_fixture_tables(target_dir: Path) -> tuple[Path, Path, Path]:
    """The §10.1 cohort set: two cohorts of DIFFERENT sizes, three datasets.

    The harness keys on the concept pack's `outcome: true` flag, never on a
    column name (§0), so this fixture's whole job is to carry a column that
    the pack's outcome-flagged variable actually names --
    `time_to_event` in assets/packs/minimal.yaml. Rename the pack variable
    and rename these headers with it; that is the coupling the invariant is
    supposed to have, and the only one.

    Cohort sizes differ (20 vs 12 rows) deliberately: a permutation that
    leaked ACROSS cohorts (the §10.1 Trap) would move values between two
    differently-sized value sets and change each cohort's outcome marginal,
    which the per-cohort marginal hash in tests/invariant/report.json then
    catches.

    COHORT_A/treatment carries NO outcome column on purpose. A cohort is
    covered when ANY one of its datasets carries the outcome, so the
    per-dataset probe must pass such a table through untouched rather than
    treating "no outcome column here" as an error -- only a whole COHORT
    with no outcome column anywhere is a defect, and that is a run-level
    judgement, not a per-table one.

    Every column is synthetic and domain-neutral (§10.3): no disease term,
    no gene symbol, no cohort name that means anything.
    """
    tables_dir = target_dir / "tables"
    tables_dir.mkdir(parents=True, exist_ok=True)

    def clinical_rows(n: int, id_column: str, first_id: int) -> list[list[str]]:
        header = [id_column, "AGE_AT_INDEX", "SEX", "VITAL_STATUS", "TIME_TO_EVENT"]
        sexes = ["male", "female", "other", "unknown"]
        statuses = ["alive", "dead", "unknown"]
        rows = [header]
        for i in range(n):
            rows.append(
                [
                    str(first_id + i),
                    str(30 + (i * 7) % 50),
                    sexes[i % len(sexes)],
                    statuses[i % len(statuses)],
                    # n DISTINCT values, so a shuffle almost surely reorders
                    # them: an outcome column of one repeated value would
                    # make the permutation a silent no-op.
                    str(round(10.0 + i * 3.5, 1)),
                ]
            )
        return rows

    a_clinical = tables_dir / "invariant_a_clinical.csv"
    a_clinical.write_text("\n".join(",".join(r) for r in clinical_rows(20, "PATIENT_ID", 1)) + "\n")

    b_clinical = tables_dir / "invariant_b_clinical.csv"
    b_clinical.write_text("\n".join(",".join(r) for r in clinical_rows(12, "SUBJECT_ID", 101)) + "\n")

    a_treatment = tables_dir / "invariant_a_treatment.csv"
    treatment = [["PATIENT_ID", "EXPOSURE_COUNT", "EXPOSURE_START_DAY"]]
    for i in range(20):
        treatment.append([str(i + 1), str(1 + i % 3), str(i * 11)])
    a_treatment.write_text("\n".join(",".join(r) for r in treatment) + "\n")

    return a_clinical, a_treatment, b_clinical


def invariant_samplesheet(target_dir: Path) -> Path:
    """Points --input at the §10.1 cohort set. No holdout row: the harness
    permutes what the proposer actually sees, and a held-out dataset is by
    construction not that (§1.2)."""
    a_clinical, a_treatment, b_clinical = invariant_fixture_tables(target_dir)
    rows = [
        "cohort_id,dataset_id,role,path,holdout",
        f"COHORT_A,clinical,clinical,{a_clinical},false",
        f"COHORT_A,treatment,treatment,{a_treatment},false",
        f"COHORT_B,clinical,clinical,{b_clinical},false",
    ]
    path = target_dir / "invariant_samplesheet.csv"
    path.write_text("\n".join(rows) + "\n")
    return path


def no_outcome_samplesheet(target_dir: Path) -> Path:
    """A cohort set carrying no column the pack's outcome variable names.

    The permutation is then a no-op on every table, so hash agreement across
    seeds proves nothing at all -- the switched-off gate §10.1's Trap
    describes, reached by accident instead of by edit. The harness has to
    refuse to call such a run a proof, and this fixture is what proves it
    does.
    """
    table = target_dir / "tables" / "no_outcome_clinical.csv"
    table.parent.mkdir(parents=True, exist_ok=True)
    rows = [["PATIENT_ID", "AGE_AT_INDEX", "SEX"]]
    for i in range(12):
        rows.append([str(i + 1), str(30 + i), ["male", "female"][i % 2]])
    table.write_text("\n".join(",".join(r) for r in rows) + "\n")

    sheet = [
        "cohort_id,dataset_id,role,path,holdout",
        f"COHORT_NO_OUTCOME,clinical,clinical,{table},false",
    ]
    path = target_dir / "no_outcome_samplesheet.csv"
    path.write_text("\n".join(sheet) + "\n")
    return path


def propose_pack(target_dir: Path) -> Path:
    """§4.1's own small, generic concept pack (Ruling R2/Task 5 note: T5
    "owns validating and extending" the pack format -- this is a dedicated
    TEST pack, not an edit to assets/packs/minimal.yaml, so Task 5a's own
    fixtures never touch a pack other tasks' green tests already depend on.

    Three variables, one per generator this card's default
    candidate_generators list names, plus the mandatory outcome flag:
      sex            -- nominal, domain_values -- exercises value_set.
      weight         -- continuous, unit: kg -- exercises unit (and, via
                        a header like WEIGHT_KG, name_ngram too).
      study_outcome  -- outcome: true -- the §4.1 nogo's own test subject:
                        a column can share this variable's exact name AND
                        a day-like value range and must still get ZERO
                        candidates.
    """
    pack = {
        "pack": "propose-fixture-generic",
        "version": "0.1.0",
        "vocabulary": "test-vocab-2026",
        "variables": [
            {
                "name": "sex",
                "domain": "person",
                "concept_id": 100,
                "type": "nominal",
                "domain_values": ["male", "female", "other", "unknown"],
            },
            {
                "name": "weight",
                "domain": "measurement",
                "concept_id": 200,
                "type": "continuous",
                "unit": "kg",
            },
            {
                "name": "study_outcome",
                "domain": "derived",
                "derivation": "survival_time",
                "unit": "day",
                "outcome": True,
            },
        ],
    }
    path = target_dir / "propose_pack.yaml"
    path.write_text(yaml.safe_dump(pack, sort_keys=False, default_flow_style=False))
    return path


def propose_fixture_table(target_dir: Path) -> Path:
    """A table whose four columns each exercise one §4.1 behaviour against
    propose_pack():
      SEX_CODE                -- values are EXACTLY propose_pack()'s `sex`
                                  domain_values -- value_set's own test.
      WEIGHT_KG                -- a header the unit patterns read as kg,
                                  matching `weight`'s declared unit.
      STUDY_OUTCOME             -- header IDENTICAL to the outcome-flagged
                                  variable's name, with a plausible day-like
                                  range -- must still get zero candidates
                                  (§4.1 nogo).
      UNRELATED_NOISE_COLUMN   -- matches nothing -- the SIDE clause's own
                                  test: a null-concept_id placeholder row.
    """
    n = 12
    sexes = ["male", "female", "other", "unknown"]
    header = ["SEX_CODE", "WEIGHT_KG", "STUDY_OUTCOME", "UNRELATED_NOISE_COLUMN"]
    rows = [header]
    for i in range(n):
        rows.append(
            [
                sexes[i % len(sexes)],
                str(50 + i),
                str(round(5.0 + i * 2.5, 1)),
                f"noise_{i}",
            ]
        )
    path = target_dir / "tables" / "propose_fixture.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(",".join(r) for r in rows) + "\n")
    return path


def propose_samplesheet(target_dir: Path) -> Path:
    """Points --input at propose_fixture_table(); pair with --concept_pack
    propose_pack()."""
    table = propose_fixture_table(target_dir)
    rows = [
        "cohort_id,dataset_id,role,path,holdout",
        f"COHORT_PROPOSE,propose_test,clinical,{table},false",
    ]
    path = target_dir / "propose_samplesheet.csv"
    path.write_text("\n".join(rows) + "\n")
    return path


def propose_cap_pack(target_dir: Path) -> Path:
    """25 variables that all name_ngram-match one column (target_variable_00
    .. target_variable_24, all sharing the "target_variable" prefix a
    TARGET_VARIABLE column trigram-overlaps heavily with), for
    --max_candidates_per_column's own test: the recall ceiling has to bound
    DISTINCT variables, not just cap generator hits."""
    variables = [
        {
            "name": f"target_variable_{i:02d}",
            "domain": "observation",
            "concept_id": 400 + i,
            "type": "nominal",
            "domain_values": ["x"],
        }
        for i in range(25)
    ]
    variables.append(
        {
            "name": "cap_outcome",
            "domain": "derived",
            "derivation": "survival_time",
            "unit": "day",
            "outcome": True,
        }
    )
    pack = {
        "pack": "propose-cap-fixture",
        "version": "0.1.0",
        "vocabulary": "test-vocab-2026",
        "variables": variables,
    }
    path = target_dir / "propose_cap_pack.yaml"
    path.write_text(yaml.safe_dump(pack, sort_keys=False, default_flow_style=False))
    return path


def propose_cap_table(target_dir: Path) -> Path:
    header = ["TARGET_VARIABLE", "FILLER_COLUMN"]
    rows = [header]
    for i in range(10):
        rows.append([str(i), str(i)])
    path = target_dir / "tables" / "propose_cap_fixture.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(",".join(r) for r in rows) + "\n")
    return path


def propose_cap_samplesheet(target_dir: Path) -> Path:
    """Points --input at propose_cap_table(); pair with --concept_pack
    propose_cap_pack() and a small --max_candidates_per_column."""
    table = propose_cap_table(target_dir)
    rows = [
        "cohort_id,dataset_id,role,path,holdout",
        f"COHORT_PROPOSE_CAP,cap_test,clinical,{table},false",
    ]
    path = target_dir / "propose_cap_samplesheet.csv"
    path.write_text("\n".join(rows) + "\n")
    return path


def confirm_ledger_valid(target_dir: Path) -> Path:
    """§5.1's own fixture (tests/confirm_ledger.nf.test): a valid
    ledger.confirmed.yaml row against profiling_samplesheet()'s fixture pair
    (profiling_fixture_table() + assets/packs/propose_channels_fixture.yaml),
    whose one real proposal is COHORT_PROFILE/profile_test/OS_MONTHS ->
    os_days, concept_id 500.

    proposed_hash is the sha256 of the ledger.proposed.yaml THAT fixture
    pair + `--max_failed_frac 0.5` (default params otherwise) produces --
    verified directly by running the pipeline once and hashing the result
    (see tests/confirm_ledger.nf.test's own header comment). If that fixture
    pair or its params ever change, this hash goes stale on purpose -- that
    is the staleness guard doing its job (§5.1's own Contract), not a bug
    here.
    """
    rows = [
        {
            "cohort_id": "COHORT_PROFILE",
            "dataset_id": "profile_test",
            "column": "OS_MONTHS",
            "decision": "accept",
            "variable": "os_days",
            "concept_id": 500,
            "unit_in": "month",  # the header/name reads as months; os_days' declared unit is days (d) -- a genuine mismatch for §6/map to reconcile later
            "confirmed_by": "a.reviewer",
            "rationale": "os_days evidence dominates the ranked proposals; OS_MONTHS' header and observed range both read as months",
            "proposed_hash": "sha256:c5e6c12fd9a8a0d6979e67e6957793fe0e88b47de58d34aaf6916d11a329381c",
        }
    ]
    path = target_dir / "confirm_ledger_valid.yaml"
    path.write_text(yaml.safe_dump(rows, sort_keys=False, default_flow_style=False))
    return path


def confirm_ledger_stale(target_dir: Path) -> Path:
    """Same row as confirm_ledger_valid(), but a deliberately WRONG
    proposed_hash (as if made against an older ledger.proposed.yaml, e.g.
    before a pack bump). Exercises the staleness SIDE clause and
    --allow_stale_ledger."""
    rows = [
        {
            "cohort_id": "COHORT_PROFILE",
            "dataset_id": "profile_test",
            "column": "OS_MONTHS",
            "decision": "accept",
            "variable": "os_days",
            "concept_id": 500,
            "unit_in": "month",
            "confirmed_by": "a.reviewer",
            "rationale": "os_days evidence dominates the ranked proposals; OS_MONTHS' header and observed range both read as months",
            "proposed_hash": "sha256:0000000000000000000000000000000000000000000000000000000000000000",
        }
    ]
    path = target_dir / "confirm_ledger_stale.yaml"
    path.write_text(yaml.safe_dump(rows, sort_keys=False, default_flow_style=False))
    return path


def confirm_ledger_no_rationale(target_dir: Path) -> Path:
    """A valid key + hash, but an `accept` decision with no `rationale` at
    all. Exercises --require_rationale."""
    rows = [
        {
            "cohort_id": "COHORT_PROFILE",
            "dataset_id": "profile_test",
            "column": "OS_MONTHS",
            "decision": "accept",
            "variable": "os_days",
            "concept_id": 500,
            "unit_in": "month",
            "confirmed_by": "a.reviewer",
            "proposed_hash": "sha256:c5e6c12fd9a8a0d6979e67e6957793fe0e88b47de58d34aaf6916d11a329381c",
        }
    ]
    path = target_dir / "confirm_ledger_no_rationale.yaml"
    path.write_text(yaml.safe_dump(rows, sort_keys=False, default_flow_style=False))
    return path


def confirm_ledger_unmatched(target_dir: Path) -> Path:
    """A row whose (cohort_id, dataset_id, column) key names a column that
    does NOT exist anywhere in ledger.proposed.yaml for this same fixture
    pair (a typo / stale reference, distinct from a whole-file hash
    mismatch). Exercises the card's own Trap guard: matched by key, never by
    row position, and never waved through by --allow_stale_ledger (that
    escape hatch is scoped to hash drift only)."""
    rows = [
        {
            "cohort_id": "COHORT_PROFILE",
            "dataset_id": "profile_test",
            "column": "NOT_A_REAL_COLUMN",
            "decision": "accept",
            "variable": "os_days",
            "concept_id": 500,
            "unit_in": "month",
            "confirmed_by": "a.reviewer",
            "rationale": "this key does not exist in ledger.proposed.yaml",
            "proposed_hash": "sha256:c5e6c12fd9a8a0d6979e67e6957793fe0e88b47de58d34aaf6916d11a329381c",
        }
    ]
    path = target_dir / "confirm_ledger_unmatched.yaml"
    path.write_text(yaml.safe_dump(rows, sort_keys=False, default_flow_style=False))
    return path


# §5.2's own fixture pair (tests/rules_stable.nf.test): two accepted
# decisions against propose_samplesheet()/propose_pack() -- SEX_CODE ->
# sex (concept_id 100), WEIGHT_KG -> weight (concept_id 200, unit_in "kg").
# proposed_hash is the sha256 of THAT fixture pair's ledger.proposed.yaml
# under default params (verified directly, same method as
# confirm_ledger_valid()'s own docstring).
_RULES_FIXTURE_HASH = "sha256:41149e226537c6915e37d22699b710abece2d209917a383c34748378113706b0"

_RULES_FIXTURE_SEX_ROW = {
    "cohort_id": "COHORT_PROPOSE",
    "dataset_id": "propose_test",
    "column": "SEX_CODE",
    "decision": "accept",
    "variable": "sex",
    "concept_id": 100,
    "confirmed_by": "a.reviewer",
    "rationale": "value set matches sex's declared domain_values exactly",
    "proposed_hash": _RULES_FIXTURE_HASH,
}

_RULES_FIXTURE_WEIGHT_ROW = {
    "cohort_id": "COHORT_PROPOSE",
    "dataset_id": "propose_test",
    "column": "WEIGHT_KG",
    "decision": "accept",
    "variable": "weight",
    "concept_id": 200,
    "unit_in": "kg",
    "confirmed_by": "a.reviewer",
    "rationale": "name and unit both match weight's declaration",
    "proposed_hash": _RULES_FIXTURE_HASH,
}


def confirm_ledger_rules_order_a(target_dir: Path) -> Path:
    """The card's own done-when fixture, order A: SEX_CODE listed first."""
    path = target_dir / "confirm_ledger_rules_order_a.yaml"
    rows = [_RULES_FIXTURE_SEX_ROW, _RULES_FIXTURE_WEIGHT_ROW]
    path.write_text(yaml.safe_dump(rows, sort_keys=False, default_flow_style=False))
    return path


def confirm_ledger_rules_order_b(target_dir: Path) -> Path:
    """The IDENTICAL two decisions as confirm_ledger_rules_order_a(), with
    WEIGHT_KG listed FIRST instead -- proves rule_id is a pure content hash
    of (kind, from, to, params), never a function of row order (the card's
    own Trap)."""
    path = target_dir / "confirm_ledger_rules_order_b.yaml"
    rows = [_RULES_FIXTURE_WEIGHT_ROW, _RULES_FIXTURE_SEX_ROW]
    path.write_text(yaml.safe_dump(rows, sort_keys=False, default_flow_style=False))
    return path


def confirm_ledger_collision(target_dir: Path) -> Path:
    """Two DIFFERENT source columns deliberately confirmed onto the SAME
    target variable ("sex"), to exercise §5.2's collision policy (nogo:
    "Never merge two rules that write the same target cell; fail and name
    both"). WEIGHT_KG's own top-ranked proposal is actually "weight" (see
    confirm_ledger_rules_order_a()) -- `remap` overrides that here on
    purpose, so this fixture stays honest about what a `remap` decision
    actually is: a human choosing something OTHER than the ledger's own top
    proposal, still for a column the ledger genuinely offers (the Trap's own
    key-matching guard still applies)."""
    rows = [
        _RULES_FIXTURE_SEX_ROW,
        {
            "cohort_id": "COHORT_PROPOSE",
            "dataset_id": "propose_test",
            "column": "WEIGHT_KG",
            "decision": "remap",
            "variable": "sex",
            "concept_id": 100,
            "confirmed_by": "a.reviewer",
            "rationale": "deliberate collision fixture: WEIGHT_KG remapped onto sex, the same target SEX_CODE already claims",
            "proposed_hash": _RULES_FIXTURE_HASH,
        },
    ]
    path = target_dir / "confirm_ledger_collision.yaml"
    path.write_text(yaml.safe_dump(rows, sort_keys=False, default_flow_style=False))
    return path


FIXTURES = [
    valid_samplesheet,
    duplicate_samplesheet,
    missing_holdout_column_samplesheet,
    single_dataset_cohort_samplesheet,
    covering_locked_model,
    noncovering_locked_model,
    matching_locked_model,
    profiling_samplesheet,
    encoding_samplesheet,
    invariant_samplesheet,
    no_outcome_samplesheet,
    propose_pack,
    propose_samplesheet,
    propose_cap_pack,
    propose_cap_samplesheet,
    confirm_ledger_valid,
    confirm_ledger_stale,
    confirm_ledger_no_rationale,
    confirm_ledger_unmatched,
    confirm_ledger_rules_order_a,
    confirm_ledger_rules_order_b,
    confirm_ledger_collision,
]


def main() -> int:
    repo_root = Path(__file__).resolve().parents[2]
    target_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else repo_root / "test_data" / "fixtures"
    target_dir.mkdir(parents=True, exist_ok=True)
    for fixture in FIXTURES:
        path = fixture(target_dir)
        print(f"wrote {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
