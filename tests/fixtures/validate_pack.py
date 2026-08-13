#!/usr/bin/env python3
"""Validate a concept pack YAML file against assets/schema_pack.json.

Usage:
    python3 tests/fixtures/validate_pack.py [path/to/pack.yaml]

Defaults to assets/packs/minimal.yaml. Exits non-zero with the jsonschema
validation error(s) on failure.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import yaml
from jsonschema import Draft202012Validator

REPO_ROOT = Path(__file__).resolve().parents[2]
SCHEMA_PATH = REPO_ROOT / "assets" / "schema_pack.json"
DEFAULT_PACK_PATH = REPO_ROOT / "assets" / "packs" / "minimal.yaml"


def validate(pack_path: Path) -> list[str]:
    schema = json.loads(SCHEMA_PATH.read_text())
    pack = yaml.safe_load(pack_path.read_text())
    validator = Draft202012Validator(schema)
    return [f"{'.'.join(str(p) for p in err.path) or '<root>'}: {err.message}" for err in validator.iter_errors(pack)]


def main() -> int:
    pack_path = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_PACK_PATH
    errors = validate(pack_path)
    if errors:
        print(f"INVALID: {pack_path} does not conform to {SCHEMA_PATH}")
        for e in errors:
            print(f"  - {e}")
        return 1
    print(f"OK: {pack_path} conforms to {SCHEMA_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
