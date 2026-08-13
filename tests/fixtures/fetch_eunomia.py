#!/usr/bin/env python3
"""Fetch the Eunomia GiBleed test dataset for `-profile test` and write a
two-level samplesheet pointing at it.

Ruling R3: `-profile test` uses Eunomia, fetched at runtime into a gitignored
`test_data/` (never committed, see §10.2). This script is that runtime fetch:
run it once before `nextflow run . -profile test,docker ...` (nf-schema
validates params.input's paths synchronously at pipeline start, before any
process runs, so the tables have to exist on disk before Nextflow starts --
they cannot be fetched by a Nextflow process).

Idempotent: re-running it when test_data/GiBleed already has the CSVs it
needs is a no-op.

Usage:
    python3 tests/fixtures/fetch_eunomia.py [target_dir]

Defaults to <repo_root>/test_data.
"""
from __future__ import annotations

import sys
import urllib.request
import zipfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
ZIP_URL = "https://raw.githubusercontent.com/OHDSI/EunomiaDatasets/main/datasets/GiBleed/GiBleed_5.3.zip"
ZIP_MEMBER_PREFIX = "GiBleed_5.3/"

# The handful of tables our test samplesheet references. Kept small on
# purpose -- ingest never reads table contents, so any real OMOP CDM 5.3
# table would do, but there is no reason to pull the multi-MB tables too.
NEEDED_TABLES = ["PERSON.csv", "VISIT_OCCURRENCE.csv", "OBSERVATION_PERIOD.csv"]

SAMPLESHEET_ROWS = [
    # cohort_id,             dataset_id,           role,      filename,                holdout
    ("COHORT_GIBLEED", "person", "clinical", "PERSON.csv", "false"),
    ("COHORT_GIBLEED", "visit", "treatment", "VISIT_OCCURRENCE.csv", "false"),
    ("COHORT_GIBLEED_HELD", "observation_period", "clinical", "OBSERVATION_PERIOD.csv", "true"),
]


def fetch_and_extract(target_dir: Path) -> Path:
    extract_dir = target_dir / "GiBleed"
    have_all = extract_dir.is_dir() and all((extract_dir / t).exists() for t in NEEDED_TABLES)
    if have_all:
        print(f"test_data already populated at {extract_dir}, skipping download")
        return extract_dir

    extract_dir.mkdir(parents=True, exist_ok=True)
    zip_path = target_dir / "GiBleed_5.3.zip"
    print(f"downloading {ZIP_URL}")
    urllib.request.urlretrieve(ZIP_URL, zip_path)

    with zipfile.ZipFile(zip_path) as zf:
        for table in NEEDED_TABLES:
            member = f"{ZIP_MEMBER_PREFIX}{table}"
            with zf.open(member) as src, open(extract_dir / table, "wb") as dst:
                dst.write(src.read())
    zip_path.unlink()
    print(f"extracted {len(NEEDED_TABLES)} tables to {extract_dir}")
    return extract_dir


def write_samplesheet(extract_dir: Path, target_dir: Path) -> Path:
    samplesheet_path = target_dir / "samplesheet.csv"
    lines = ["cohort_id,dataset_id,role,path,holdout"]
    for cohort_id, dataset_id, role, filename, holdout in SAMPLESHEET_ROWS:
        lines.append(f"{cohort_id},{dataset_id},{role},{extract_dir / filename},{holdout}")
    samplesheet_path.write_text("\n".join(lines) + "\n")
    print(f"wrote {samplesheet_path}")
    return samplesheet_path


def main() -> int:
    target_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else REPO_ROOT / "test_data"
    target_dir.mkdir(parents=True, exist_ok=True)
    extract_dir = fetch_and_extract(target_dir)
    write_samplesheet(extract_dir, target_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
