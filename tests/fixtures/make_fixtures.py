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


FIXTURES = [
    valid_samplesheet,
    duplicate_samplesheet,
    missing_holdout_column_samplesheet,
    single_dataset_cohort_samplesheet,
    covering_locked_model,
    noncovering_locked_model,
    matching_locked_model,
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
