#!/usr/bin/env python3
"""Fetch the MIMIC-IV demo dataset for `-profile test_full` and write a
two-level samplesheet pointing at it.

§10.2's second fixture, and the one that carries the card's actual argument:

> Eunomia is native OMOP and tiny -- perfect for CI, and useless for proving
> the pipeline handles mess. MIMIC-IV demo is genuinely messy with many
> tables per subject, which is the real JOIN-then-UNION shape.

The mess is not decorative. Against `assets/packs/clinical_core.yaml` this
fixture carries, among others:

  * `gender` as F/M but `race` as free text with slashes and hyphens
    ("BLACK/CAPE VERDEAN"), so value_set matches one and not the other;
  * `language` using "?" as its missing marker rather than any of
    params.na_strings' defaults, so §2.1's missingness is visibly wrong
    until someone says so;
  * `anchor_age` / `anchor_year` / `anchor_year_group`, which are a
    de-identification artefact, not a birth date -- there is no
    `year_of_birth` column to match at all;
  * `los` in days and `valueuom` carrying per-row units, which is the only
    place in either fixture where §4.1's `unit` generator has anything to
    fire on.

None of that is reachable from Eunomia, whose columns are already OMOP and
already mapped -- §10.2's Trap. Both fixtures are needed; neither is
sufficient.

Licence and access: the MIMIC-IV *demo* (v2.2) is the open-access subset,
distributed under ODC-BY, and needs no PhysioNet credentialing -- which is
precisely why the card names the demo and rejects full MIMIC-IV
("credentialed access, so CI cannot run it and the fixture stops being
public"). Nothing fetched here is ever committed (§10.2 nogo: "Never commit
fixture data to the repo"); `test_data/` is gitignored.

Idempotent: re-running when test_data/mimic-iv-demo already holds the CSVs
is a no-op.

Usage:
    python3 tests/fixtures/fetch_mimic_demo.py [target_dir]

Defaults to <repo_root>/test_data.
"""
from __future__ import annotations

import gzip
import shutil
import sys
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
BASE_URL = "https://physionet.org/files/mimic-iv-demo/2.2"

# One module per (cohort, dataset) row below. Kept to the small tables: the
# demo's own labevents is the largest at a few hundred KB, and there is no
# reason to pull the rest -- ingest never reads a table's contents, and the
# profiler reads whatever it is given.
#
# The cohort split is the card's "multi-centre exercised as multi-cohort":
# MIMIC-IV demo is one centre, so two cohorts are carved out of it by CARE
# SETTING (hospital vs ICU) rather than invented. Each cohort holds more
# than one dataset, which is the JOIN-then-UNION shape §1.1 is about -- a
# cohort with one dataset would need --allow_single_dataset and would prove
# nothing about the join.
#
#   member path under BASE_URL, cohort_id, dataset_id, role, holdout
FIXTURE_ROWS = [
    ("hosp/patients.csv.gz", "COHORT_MIMIC_HOSP", "patients", "clinical", "false"),
    ("hosp/admissions.csv.gz", "COHORT_MIMIC_HOSP", "admissions", "clinical", "false"),
    ("hosp/omr.csv.gz", "COHORT_MIMIC_HOSP", "omr", "other", "false"),
    ("icu/icustays.csv.gz", "COHORT_MIMIC_ICU", "icustays", "clinical", "false"),
    ("hosp/labevents.csv.gz", "COHORT_MIMIC_ICU", "labevents", "other", "false"),
    # The held-out dataset (§1.2). Admitted only by a hash-matching
    # locked_model.json via --unseal; a plain `-profile test_full` run must
    # never stage it, and STAGE_OPEN_DATASET never being submitted for it is
    # how that is checked.
    ("hosp/diagnoses_icd.csv.gz", "COHORT_MIMIC_ICU", "diagnoses", "other", "true"),
]


def fetch_and_extract(target_dir: Path) -> Path:
    extract_dir = target_dir / "mimic-iv-demo"
    wanted = {Path(member).name[: -len(".gz")] for member, *_ in FIXTURE_ROWS}
    if extract_dir.is_dir() and all((extract_dir / name).exists() for name in wanted):
        print(f"test_data already populated at {extract_dir}, skipping download")
        return extract_dir

    extract_dir.mkdir(parents=True, exist_ok=True)
    for member, *_ in FIXTURE_ROWS:
        csv_name = Path(member).name[: -len(".gz")]
        out_path = extract_dir / csv_name
        if out_path.exists():
            continue
        url = f"{BASE_URL}/{member}"
        print(f"downloading {url}")
        gz_path = extract_dir / Path(member).name
        urllib.request.urlretrieve(url, gz_path)
        # Decompressed on the way in, not read as .gz by the pipeline: §1.1's
        # samplesheet takes a table path, and a fixture that only works
        # because duckdb happens to sniff gzip is a fixture testing duckdb.
        with gzip.open(gz_path, "rb") as src, open(out_path, "wb") as dst:
            shutil.copyfileobj(src, dst)
        gz_path.unlink()
    print(f"extracted {len(FIXTURE_ROWS)} tables to {extract_dir}")
    return extract_dir


def write_samplesheet(extract_dir: Path, target_dir: Path) -> Path:
    samplesheet_path = target_dir / "samplesheet_mimic.csv"
    lines = ["cohort_id,dataset_id,role,path,holdout"]
    for member, cohort_id, dataset_id, role, holdout in FIXTURE_ROWS:
        csv_name = Path(member).name[: -len(".gz")]
        lines.append(f"{cohort_id},{dataset_id},{role},{extract_dir / csv_name},{holdout}")
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
