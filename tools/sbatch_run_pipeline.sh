#!/bin/bash
#SBATCH --job-name=clinarmonize_run
#SBATCH --output=logs/clinarmonize_run_%j.out
#SBATCH --error=logs/clinarmonize_run_%j.err
#SBATCH --time=08:00:00
#SBATCH --cpus-per-task=8
#SBATCH --mem=32G
# Partition is deliberately NOT set here -- it is the one SLURM setting that is
# purely a property of your site. Pass it on the sbatch line:
#     sbatch --partition=medium tools/sbatch_run_pipeline.sh
# Mail is off by default: a placeholder address makes SLURM bounce every
# notification. Uncomment and set your own to re-enable.
# #SBATCH --mail-type=END,FAIL
# #SBATCH --mail-user=you@example.org

# ============================================================================
# clinarmonize — batch wrapper around tools/run_pipeline.sh
# ============================================================================
# Submit from the repo root, with RUN_DIR pointing at scratch:
#
#   mkdir -p logs
#   sbatch --partition=<yours> tools/sbatch_run_pipeline.sh \
#       --run-dir=/path/to/scratch/run
#
# Pass --run-dir as an ARGUMENT rather than as `RUN_DIR=... sbatch ...`. The
# environment form works only where SLURM is configured to forward the
# submitting environment (--export=ALL); where it is not, the job starts with
# RUN_DIR unset and nothing in the submitting shell says so.
#
# With no trailing arguments it runs the §6.3 fixture demo end to end
# (generates fixtures, runs through `map`, writes the alluvial plots). With
# trailing arguments it passes them straight to `nextflow run` instead:
#
#   sbatch --partition=<yours> tools/sbatch_run_pipeline.sh \
#       --run-dir=/path/to/scratch/run \
#       --stop_after propose --input /path/to/your/samplesheet.csv
#
# Knobs (env vars, all optional except RUN_DIR):
#   RUN_DIR     where work/, results/ and fixtures/ go   (REQUIRED -- prefer
#               the --run-dir=PATH argument, see above)
#   SRC_DIR     the pipeline checkout                    (default: submit dir)
#   CONDA_ENV   conda env providing nextflow/java/python (default: nf-env;
#               set CONDA_ENV= empty to skip activation entirely)
#   PROFILE     nextflow -profile value                  (default: singularity)
#   CLINARMONIZE_CACHE  shared container image cache     (default: $SCRATCH or $HOME)
#   NXF_JVM_ARGS        JVM heap for the head process    (default: -Xms4g -Xmx16g)
#
# NOTE ON logs/: SLURM opens the --output file at job START, before a single
# line of this script runs. If logs/ does not exist the job dies with no
# output at all and no obvious cause. `mkdir -p logs` first.
#
# NOTE ON cpus-per-task: Nextflow runs its tasks through the LOCAL executor
# inside this one allocation and sizes its parallelism from the CPUs it can
# see, so a small --cpus-per-task serialises the whole graph.
# ============================================================================

# No `set -uo pipefail`, for the reason tools/verify_clinarmonize.sh documents
# at length: a site ~/.bashrc that runs `set -u` turns conda's own init into an
# abort, and `|| true` cannot catch it because `set -u` makes the shell EXIT
# rather than return non-zero. Every variable below uses ${VAR:-default}.
set +e +u +o pipefail

die() { echo "ERROR: $*" >&2; exit 1; }

# SLURM executes a SPOOLED COPY of this file, so ${BASH_SOURCE[0]} points into
# the spool directory and cannot locate the repo. The submission directory is
# what SLURM does preserve.
SRC_DIR="${SRC_DIR:-${SLURM_SUBMIT_DIR:-$PWD}}"
[[ -f "$SRC_DIR/main.nf" ]] || \
  die "no main.nf under SRC_DIR='$SRC_DIR'. Submit from the repo root, or set SRC_DIR."

# --run-dir=PATH, consumed here and never passed on to nextflow.
#
# This exists because `RUN_DIR=... sbatch script.sh` is NOT reliable: it sets
# the variable for sbatch's own process, and SLURM forwards it to the job only
# under --export=ALL. Plenty of sites default to NONE or set SBATCH_EXPORT in
# /etc/profile, and the job then starts with RUN_DIR unset for a reason that
# is invisible from the submitting shell. An argument travels in the command
# line, which SLURM always preserves.
case "${1:-}" in
  --run-dir=*) RUN_DIR="${1#--run-dir=}"; shift ;;
  --run-dir)   RUN_DIR="${2:-}"; shift 2 ;;
esac

[[ -n "${RUN_DIR:-}" ]] || die "RUN_DIR is not set. Either pass it as an argument (always works):

    sbatch tools/sbatch_run_pipeline.sh --run-dir=/path/to/scratch/run

or export it through SLURM explicitly (needed because this site does not
forward the submitting environment by default):

    sbatch --export=ALL,RUN_DIR=/path/to/scratch/run tools/sbatch_run_pipeline.sh"

echo "=== clinarmonize batch run ==="
echo "job         : ${SLURM_JOB_ID:-<interactive>} on $(hostname)"
echo "started     : $(date -u '+%Y-%m-%dT%H:%M:%SZ')"
echo "SRC_DIR     : $SRC_DIR"
echo "RUN_DIR     : $RUN_DIR"
echo "commit      : $(git -C "$SRC_DIR" rev-parse --short HEAD 2>/dev/null || echo '?')"
echo "git dirty   : $(git -C "$SRC_DIR" diff --quiet 2>/dev/null && echo no || echo YES)"
echo

# The site profile, then conda. Shell options are neutralised AFTER each,
# because both can turn them back on underneath us.
source ~/.bashrc 2>/dev/null
set +e +u +o pipefail
# ${CONDA_ENV-nf-env} is a SINGLE dash on purpose: it substitutes only when
# CONDA_ENV is UNSET, so an explicit `CONDA_ENV=` means "my environment is
# already active, do not touch it" rather than silently falling back to the
# default. The two-dash form cannot express that difference.
WANT_ENV="${CONDA_ENV-nf-env}"
if [[ -n "$WANT_ENV" ]]; then
  command -v conda >/dev/null 2>&1 || die "CONDA_ENV='$WANT_ENV' but no conda is on PATH."
  eval "$(conda shell.bash hook)" 2>/dev/null
  set +e +u +o pipefail
  conda activate "$WANT_ENV" 2>/dev/null || \
    die "cannot activate conda env '$WANT_ENV'. Set CONDA_ENV to one that exists, or CONDA_ENV= to skip."
  set +e +u +o pipefail
fi
command -v nextflow >/dev/null 2>&1 || die "nextflow is not on PATH after activating the env."

export NXF_JVM_ARGS="${NXF_JVM_ARGS:--Xms4g -Xmx16g}"
export RUN_DIR SRC_DIR

if [[ $# -gt 0 ]]; then
  echo "mode        : pass-through ($*)"
  echo
  bash "$SRC_DIR/tools/run_pipeline.sh" "$@"
else
  # The §6.3 fixture demo. Self-contained: FIXTURES=1 regenerates the inputs,
  # which is required because test_data/ is gitignored and does not survive a
  # clone. --max_unmapped_frac is raised because the fixture's outcome-flagged
  # column carries no rule by construction (§4.1 refuses to propose one), so a
  # third of its values reach mapped/_unmapped.parquet on every run.
  echo "mode        : §6.3 fixture demo (no arguments given)"
  echo

  # max_unmapped_frac goes through a PARAMS FILE, not the CLI. nf-schema types
  # a bare `--max_unmapped_frac 0.6` as a STRING and rejects it against a
  # `"type": "number"` property: "Value is [string] but should be [number]".
  # A -params-file carries real JSON types, so the same value validates. This
  # applies to every numeric and boolean param, not just this one.
  mkdir -p "$RUN_DIR"
  cat > "$RUN_DIR/demo-params.json" <<JSON
{ "max_unmapped_frac": 0.6 }
JSON

  FIXTURES=1 bash "$SRC_DIR/tools/run_pipeline.sh" \
      -params-file "$RUN_DIR/demo-params.json" \
      --stop_after map \
      --input            "$RUN_DIR/fixtures/values_samplesheet.csv" \
      --concept_pack     "$RUN_DIR/fixtures/values_pack.yaml" \
      --confirmed_ledger "$RUN_DIR/fixtures/confirm_ledger_values.yaml"
fi

rc=$?
echo
echo "=== finished $(date -u '+%Y-%m-%dT%H:%M:%SZ') with exit $rc ==="
if [[ $rc -eq 0 ]]; then
  echo "results     : $RUN_DIR/results"
  ls -1 "$RUN_DIR/results/qc" 2>/dev/null | sed 's/^/  qc\//'
fi
exit $rc
