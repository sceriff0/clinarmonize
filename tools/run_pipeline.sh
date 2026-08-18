#!/usr/bin/env bash
# ============================================================================
# Run the pipeline with the checkout in one place and the run in another.
#
# The case this exists for: the repo lives on a home/NFS filesystem that is
# small and quota-bound, while the run has to happen on fast scratch. Nextflow
# is happy to do that, but four paths have to be pointed somewhere explicitly
# or they land next to the checkout -- and one of them (--params_hash_file)
# does NOT follow --outdir, which is the one people miss.
#
# No site path is baked into this file. Everything below is either derived
# from where the script lives or read from an environment variable you set.
#
# Usage:
#   bash tools/run_pipeline.sh [extra nextflow args ...]
#
# Knobs (env vars, all optional):
#   RUN_DIR     where work/, results/ and fixtures/ go   (default: $PWD)
#   SRC_DIR     the pipeline checkout                    (default: this script's repo)
#   PROFILE     nextflow -profile value                  (default: singularity)
#   CONDA_ENV   conda env providing nextflow/java/python (default: none -- assumes
#                                                         your env is already active)
#   NXF_VER     Nextflow version                         (default: 26.04.6)
#   CLINARMONIZE_CACHE  shared container image cache     (default: $SCRATCH or $HOME)
#   FIXTURES    1 = regenerate test fixtures into $RUN_DIR/fixtures first
#
# Everything after the script name is passed straight through to `nextflow run`,
# so this composes rather than wraps:
#
#   RUN_DIR=/scratch/$USER/clin_test FIXTURES=1 bash tools/run_pipeline.sh \
#       --stop_after map \
#       --input            "$RUN_DIR/fixtures/values_samplesheet.csv" \
#       --concept_pack     "$RUN_DIR/fixtures/values_pack.yaml" \
#       --confirmed_ledger "$RUN_DIR/fixtures/confirm_ledger_values.yaml" \
#       --max_unmapped_frac 0.6
# ============================================================================

# No `set -u`, for the reason tools/verify_clinarmonize.sh spells out at
# length: a site ~/.bashrc that runs `set -u` turns conda's own init into an
# abort, and `|| true` cannot catch it because `set -u` makes the shell EXIT.
# Every variable below uses ${VAR:-default}, so nothing here needs nounset to
# be correct. `set -e` is likewise absent -- the failure paths are explicit.
set +e +u +o pipefail

die() { echo "ERROR: $*" >&2; exit 1; }
say() { printf '%-14s: %s\n' "$1" "$2"; }

# ---------------------------------------------------------------------------
# Where things are
# ---------------------------------------------------------------------------
SELF="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SRC_DIR="${SRC_DIR:-$(cd "$SELF/.." && pwd)}"
RUN_DIR="${RUN_DIR:-$PWD}"
PROFILE="${PROFILE:-singularity}"

[[ -f "$SRC_DIR/main.nf" ]] || die "no main.nf under SRC_DIR='$SRC_DIR'."
mkdir -p "$RUN_DIR" || die "cannot create RUN_DIR='$RUN_DIR'."
RUN_DIR="$(cd "$RUN_DIR" && pwd)"

# ---------------------------------------------------------------------------
# Conda, only if asked. An interactive user who already activated their env
# does not want this script re-activating it underneath them.
# ---------------------------------------------------------------------------
if [[ -n "${CONDA_ENV:-}" ]]; then
  if command -v conda >/dev/null 2>&1; then
    eval "$(conda shell.bash hook)" 2>/dev/null
    conda activate "$CONDA_ENV" 2>/dev/null || die "cannot activate conda env '$CONDA_ENV'."
    set +e +u +o pipefail   # conda's init can re-enable them
  else
    die "CONDA_ENV='$CONDA_ENV' was set but no conda is on PATH."
  fi
fi

command -v nextflow >/dev/null 2>&1 || die "nextflow is not on PATH (set CONDA_ENV, or module load it)."

# Java, checked here rather than met as a JVM stack trace three screens into a
# batch log. tools/verify_clinarmonize.sh already does this; the omission here
# was a gap, not a decision.
command -v java >/dev/null 2>&1 || \
  die "java is not on PATH (Nextflow needs >= 17). Add it to your conda env or 'module load java'."

# ---------------------------------------------------------------------------
# The version pin, checked here rather than discovered as a stack trace.
# nextflow.config declares `nextflowVersion = '!>=26.04.6'`; the leading '!'
# makes a mismatch a hard abort, and nf-schema@2.8.0 refuses anything older
# regardless. A bare `nextflow` on many sites resolves 25.x.
# ---------------------------------------------------------------------------
export NXF_VER="${NXF_VER:-26.04.6}"
case "$NXF_VER" in
  25.*|24.*|23.*)
    die "NXF_VER=$NXF_VER is below this pipeline's pin (>=26.04.6). Unset NXF_VER or set it to 26.04.6." ;;
esac
export NXF_JVM_ARGS="${NXF_JVM_ARGS:--Xms4g -Xmx16g}"

# ---------------------------------------------------------------------------
# Container image cache. Only meaningful for the singularity/apptainer
# profiles; harmless otherwise. Anything you already exported wins.
# ---------------------------------------------------------------------------
case "$PROFILE" in
  *singularity*|*apptainer*)
    DEFAULT_CACHE="${CLINARMONIZE_CACHE:-${SCRATCH:-$HOME}/.clinarmonize/container-cache}"
    export SINGULARITY_CACHEDIR="${SINGULARITY_CACHEDIR:-$DEFAULT_CACHE}"
    export NXF_SINGULARITY_CACHEDIR="${NXF_SINGULARITY_CACHEDIR:-$DEFAULT_CACHE}"
    export APPTAINER_CACHEDIR="${APPTAINER_CACHEDIR:-$DEFAULT_CACHE}"
    export NXF_APPTAINER_CACHEDIR="${NXF_APPTAINER_CACHEDIR:-$DEFAULT_CACHE}"
    mkdir -p "$NXF_SINGULARITY_CACHEDIR" 2>/dev/null || \
      die "cannot create image cache at $NXF_SINGULARITY_CACHEDIR -- set CLINARMONIZE_CACHE to a writable path."
    ;;
esac

# ---------------------------------------------------------------------------
# Fixtures. test_data/ is gitignored, so these do not survive a clone and have
# to be regenerated. Written into RUN_DIR, not into the checkout: the
# samplesheets they emit carry ABSOLUTE paths to their own tables, so
# generating them beside the run keeps the checkout read-only-clean.
# ---------------------------------------------------------------------------
if [[ "${FIXTURES:-0}" == "1" ]]; then
  command -v python3 >/dev/null 2>&1 || die "FIXTURES=1 needs python3 on PATH."
  python3 "$SRC_DIR/tests/fixtures/make_fixtures.py" "$RUN_DIR/fixtures" >/dev/null \
    || die "fixture generation failed."
  say "fixtures" "$RUN_DIR/fixtures"
fi

# ---------------------------------------------------------------------------
# Go
# ---------------------------------------------------------------------------
say "SRC_DIR"  "$SRC_DIR"
say "RUN_DIR"  "$RUN_DIR"
say "profile"  "$PROFILE"
say "NXF_VER"  "$NXF_VER"
[[ -n "${NXF_SINGULARITY_CACHEDIR:-}" ]] && say "image cache" "$NXF_SINGULARITY_CACHEDIR"
echo

cd "$RUN_DIR" || die "cannot cd to '$RUN_DIR'."

# --params_hash_file is passed explicitly and is NOT redundant with --outdir.
# Its default is the RELATIVE 'results/params_hash.txt', which Nextflow
# resolves against the launch directory and which does not move when --outdir
# does -- so without this line it lands somewhere the run does not own.
exec nextflow \
  -log "$RUN_DIR/nextflow.log" \
  run "$SRC_DIR/main.nf" \
  -profile "$PROFILE" \
  -work-dir "$RUN_DIR/work" \
  --outdir "$RUN_DIR/results" \
  --params_hash_file "$RUN_DIR/results/params_hash.txt" \
  "$@"
