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

import json
import sys
from pathlib import Path

HOLDOUT_TIMESTAMP = "2026-08-13T00:00:00Z"


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
    """A locked_model.json that covers the given dataset id(s) -- valid for --unseal."""
    covers = covers or ["clinical"]
    lock = {
        "analysis_git_sha": "0" * 40,
        "params_hash": "deadbeef",
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
        "params_hash": "deadbeef",
        "locked_at": HOLDOUT_TIMESTAMP,
        "covers": ["some_other_dataset"],
    }
    path = target_dir / "noncovering_locked_model.json"
    path.write_text(json.dumps(lock, indent=2) + "\n")
    return path


FIXTURES = [
    valid_samplesheet,
    duplicate_samplesheet,
    missing_holdout_column_samplesheet,
    single_dataset_cohort_samplesheet,
    covering_locked_model,
    noncovering_locked_model,
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
