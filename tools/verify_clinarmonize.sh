#!/bin/bash
#SBATCH --job-name=clinarmonize_verify
#SBATCH --output=logs/clinarmonize_verify_%j.out
#SBATCH --error=logs/clinarmonize_verify_%j.err
#SBATCH --time=12:00:00
#SBATCH --cpus-per-task=8
#SBATCH --mem=64G
#SBATCH --partition=medium        # 'medium' is what the 20260817 run actually landed on
# Mail is off by default: the template's placeholder address made SLURM
# bounce every notification. Uncomment and set your own to re-enable.
# #SBATCH --mail-type=END,FAIL
# #SBATCH --mail-user=you@example.org

# ============================================================================
# clinarmonize — container verification harness
# ============================================================================
# Runs the nf-test suite under a real container engine and writes ONE
# pasteable report plus a tarball of evidence.
#
#   sbatch tools/verify_clinarmonize.sh                  # everything (~2h)
#   CHUNK=fast      sbatch tools/verify_clinarmonize.sh  # 38 quick tests (~15m)
#   CHUNK=invariant sbatch tools/verify_clinarmonize.sh  # the 5 slow ones
#
# Also runs directly on an interactive node:
#   srun --cpus-per-task=8 --mem=64G --time=2:00:00 --pty bash
#   bash tools/verify_clinarmonize.sh
#
# Knobs (env vars, all optional):
#   CHUNK        all | fast | invariant                     (default: all)
#   ENGINE       singularity | apptainer | docker | podman  (default: auto)
#   CONDA_ENV    conda env providing nextflow/java/python   (default: nf-env)
#   SITE_CONFIG  extra nextflow config to layer in          (default: none)
#   NXF_VER      Nextflow version                           (default: 26.04.6)
#
# NOTE ON cpus-per-task: unlike the mirage pipeline, this suite runs its tasks
# through Nextflow's LOCAL executor inside this one allocation. Nextflow sizes
# its parallelism from the CPUs it can see, so --cpus-per-task=2 serialises the
# 453-task invariant graph. 8 is the useful minimum here.
# ============================================================================

# No `set -uo pipefail` here, deliberately.
#
# It was removed after it broke the run on the cluster. `set -u` in particular
# is hostile to this script's own preamble: `source ~/.bashrc` and
# `eval "$(conda shell.bash hook)"` both dereference variables the site's
# profile leaves unset, and nounset turns that into an abort before a single
# test runs. Every variable read below already uses ${VAR:-default}, so
# nothing here depends on nounset to be correct.
#
# `set -e` was never here either, for a separate reason that still holds:
# run_chunk() captures `rc=$?` from a failing nf-test and the script must keep
# going to write the report and the evidence tarball. Under -e a single failed
# test would abort the job and destroy the evidence you submitted it to collect.

mkdir -p logs

# ---------------------------------------------------------------------------
# banner
# ---------------------------------------------------------------------------
echo "=================================================="
echo "Job ID:     ${SLURM_JOB_ID:-none (interactive)}"
echo "Job name:   ${SLURM_JOB_NAME:-clinarmonize_verify}"
echo "Node:       ${SLURM_NODELIST:-$(hostname)}"
echo "Start time: $(date)"
echo "Purpose:    verify the nf-test suite under containers"
echo "=================================================="

NXF_VER="${NXF_VER:-26.04.6}"
CHUNK="${CHUNK:-all}"
CONDA_ENV="${CONDA_ENV:-nf-env}"
SITE_CONFIG="${SITE_CONFIG:-}"
ENV_LABEL="ambient environment (no conda env active)"
REPO="$(pwd)"
STAMP="$(date +%Y%m%d-%H%M%S)${SLURM_JOB_ID:+-$SLURM_JOB_ID}"
OUT="$REPO/verification-$STAMP"
REPORT="$OUT/report.txt"
mkdir -p "$OUT"

say() { printf '%s\n' "$*" | tee -a "$REPORT"; }
hdr() { printf '\n=== %s ===\n' "$*" | tee -a "$REPORT"; }
die() { say "FATAL: $*"; say ""; say "(report so far: $REPORT)"; exit 1; }

say "clinarmonize verification  |  $STAMP"

# ---------------------------------------------------------------------------
# 0. must be run from the repo root
# ---------------------------------------------------------------------------
[[ -f nf-test.config && -f main.nf ]] || \
  die "run this from the root of the clinarmonize clone (nf-test.config + main.nf not found in $REPO)."

# ---------------------------------------------------------------------------
# 1. conda environment (mirrors your usual mirage submission)
# ---------------------------------------------------------------------------
hdr "conda"
if [[ -f "$HOME/.bashrc" ]]; then
  # shellcheck disable=SC1091
  source "$HOME/.bashrc" 2>/dev/null || true
fi

# Whatever ~/.bashrc just did to this shell's options, undo it.
#
# This is not belt-and-braces; it is the fix for a real, reproduced failure
# (SLURM 6501142 and 6502426, both FAILED 1:0 after 9s with an empty stderr
# and no FATAL line in report.txt). A site profile that runs `set -u` leaves
# it active for the REST of this script, and the very next line --
# `eval "$(conda shell.bash hook)"` -- dereferences variables conda's own
# init leaves unset.
#
# The `|| true` on that line does NOT protect against it. `|| true` catches a
# non-zero RETURN; `set -u` makes the shell EXIT, and an exit cannot be
# caught by ||. With stderr redirected to /dev/null the bash diagnostic is
# swallowed too, so the job dies with no message anywhere -- which is exactly
# how this presented, and why it took four hypotheses to find.
#
# Hence: neutralise here, once, immediately after the only line that can
# import someone else's options. Removing `set -uo pipefail` from THIS file
# (commit e32151e) was necessary and was never sufficient, because the
# hostile `set` was never in this file.
set +e +u +o pipefail
if command -v conda >/dev/null 2>&1; then
  # 'conda activate' needs the shell hook under a non-interactive shell
  eval "$(conda shell.bash hook)" 2>/dev/null || true
  if conda activate "$CONDA_ENV" 2>/dev/null; then
    ENV_LABEL="$CONDA_ENV"
    say "conda env   : $CONDA_ENV ($(python3 --version 2>&1))"
  else
    say "WARNING     : could not activate conda env '$CONDA_ENV' -- continuing with the ambient environment"
  fi
else
  say "WARNING     : conda not found -- continuing with the ambient environment"
fi

# ---------------------------------------------------------------------------
# 2. environment report
# ---------------------------------------------------------------------------
hdr "environment"
say "host        : $(hostname)"
say "os          : $(uname -srm)"
say "cwd         : $REPO"
say "git commit  : $(git rev-parse --short HEAD 2>/dev/null || echo 'not a git repo')"
say "git branch  : $(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo '?')"
# Listed, not merely flagged. A bare "yes" tells you the commit above no longer
# identifies this tree, but not WHICH files to gitignore -- diagnosing that from
# a finished report costs a whole session. `-uall` names the files instead of
# collapsing an untracked directory to a single entry.
#
# The old `| grep -v 'verification-'` filter is gone on purpose: verification-*/
# and the tarball became gitignored in 184323f, so it was dead weight, and as a
# plain substring filter over the whole porcelain line it would also have hidden
# a genuine edit to any path containing that word.
DIRTY="$(git status --porcelain -uall 2>/dev/null)"
if [[ -z "$DIRTY" ]]; then
  say "git dirty   : no"
else
  say "git dirty   : YES -- the commit above does NOT identify this tree"
  printf '%s\n' "$DIRTY" | sed 's/^/              /' | tee -a "$REPORT"
fi
say "cpus alloc  : ${SLURM_CPUS_PER_TASK:-$(nproc 2>/dev/null || echo '?')}"
say "mem alloc   : ${SLURM_MEM_PER_NODE:-?} MB"
say "partition   : ${SLURM_JOB_PARTITION:-none}"
say "java        : $(java -version 2>&1 | head -1 || echo MISSING)"

command -v java >/dev/null 2>&1 || die "java not on PATH (need >= 17). Add it to the '$CONDA_ENV' env or 'module load java'."

if [[ "${SLURM_CPUS_PER_TASK:-8}" -lt 4 && "$CHUNK" != "fast" ]]; then
  say "WARNING     : only ${SLURM_CPUS_PER_TASK} CPUs allocated. The invariant chunk runs 453 tasks"
  say "              through the LOCAL executor and will crawl. Resubmit with --cpus-per-task=8."
fi

# ---------------------------------------------------------------------------
# 3. container engine
# ---------------------------------------------------------------------------
hdr "container engine"
detect_engine() {
  for e in singularity apptainer docker podman; do
    command -v "$e" >/dev/null 2>&1 && { echo "$e"; return; }
  done
  echo ""
}
ENGINE="${ENGINE:-$(detect_engine)}"
[[ -n "$ENGINE" ]] || die "no container engine found (singularity, apptainer, docker, podman). Try 'module load singularity'."
say "engine      : $ENGINE ($(command -v "$ENGINE"))"
say "version     : $($ENGINE --version 2>&1 | head -1)"

# Shared image cache — same location you already use for mirage, so the wave
# images are pulled once and reused across pipelines.
if [[ "$ENGINE" == "singularity" || "$ENGINE" == "apptainer" ]]; then
  DEFAULT_CACHE="/hpcnfs/scratch/P_DIMA_ATTEND/users/vfassi/docker_images"
  export SINGULARITY_CACHEDIR="${SINGULARITY_CACHEDIR:-$DEFAULT_CACHE}"
  export NXF_SINGULARITY_CACHEDIR="${NXF_SINGULARITY_CACHEDIR:-$DEFAULT_CACHE}"
  export APPTAINER_CACHEDIR="${APPTAINER_CACHEDIR:-$DEFAULT_CACHE}"
  export NXF_APPTAINER_CACHEDIR="${NXF_APPTAINER_CACHEDIR:-$DEFAULT_CACHE}"
  mkdir -p "$NXF_SINGULARITY_CACHEDIR" 2>/dev/null || \
    die "cannot create image cache at $NXF_SINGULARITY_CACHEDIR — set NXF_SINGULARITY_CACHEDIR to a writable path."
  say "image cache : $NXF_SINGULARITY_CACHEDIR"
  say "cache usage : $(du -sh "$NXF_SINGULARITY_CACHEDIR" 2>/dev/null | awk '{print $1}' || echo '?')"
fi

# ---------------------------------------------------------------------------
# 4. Nextflow / JVM
# ---------------------------------------------------------------------------
hdr "toolchain"
export NXF_VER
export NXF_JVM_ARGS="${NXF_JVM_ARGS:--Xms4g -Xmx16g}"
export NXF_ANSI_LOG=false
say "NXF_VER     : $NXF_VER"
say "NXF_JVM_ARGS: $NXF_JVM_ARGS"

# This pipeline pins '!>=26.04.6' in nextflow.config. The leading '!' makes it a
# HARD failure, not a warning. 25.04.7 (your mirage pin) will be refused.
REQUIRED_PIN="$(grep -oE "nextflowVersion\s*=\s*'[^']+'" nextflow.config 2>/dev/null | head -1)"
say "pipeline pin: ${REQUIRED_PIN:-unknown}"
case "$NXF_VER" in
  25.*) die "NXF_VER=$NXF_VER is below this pipeline's hard pin (${REQUIRED_PIN}). Unset NXF_VER or set it to 26.04.6." ;;
esac

command -v nextflow >/dev/null 2>&1 || {
  say "nextflow not found -- installing into $OUT"
  ( cd "$OUT" && curl -fsSL https://get.nextflow.io | bash >/dev/null 2>&1 )
  export PATH="$OUT:$PATH"
}
command -v nf-test >/dev/null 2>&1 || {
  say "nf-test not found -- installing into $OUT"
  ( cd "$OUT" && curl -fsSL https://get.nf-test.com | bash >/dev/null 2>&1 )
  export PATH="$OUT:$PATH"
}
command -v nextflow >/dev/null 2>&1 || die "nextflow unavailable and the install failed (no outbound network on this node?)."
command -v nf-test  >/dev/null 2>&1 || die "nf-test unavailable and the install failed (no outbound network on this node?)."
say "nextflow    : $(nextflow -v 2>&1 | head -1)"
say "nf-test     : $(nf-test version 2>&1 | grep -oE 'nf-test [0-9.]+' | head -1)"

# ---------------------------------------------------------------------------
# 5. host-side python  (NOT containerised — see comment)
# ---------------------------------------------------------------------------
# nf-test's parquet assertions shell out to the HOST python3 and need duckdb.
# The container profile does not cover them: the pipeline can complete happily
# inside containers while every assertion fails for want of a host duckdb.
hdr "host python (required by nf-test assertions)"
python3 -c 'import sys; sys.exit(0 if sys.version_info>=(3,9) else 1)' 2>/dev/null || \
  die "python3 >= 3.9 not on PATH. Add it to the '$CONDA_ENV' env or 'module load python'."
say "python3     : $(python3 --version 2>&1) ($(command -v python3))"

if python3 -c 'import duckdb,yaml,jsonschema' 2>/dev/null; then
  say "deps        : duckdb $(python3 -c 'import duckdb;print(duckdb.__version__)') already present in '$ENV_LABEL'"
else
  # Layer a venv on top of the conda python rather than mutating '$CONDA_ENV'.
  say "deps        : missing -- building an overlay venv (your '$ENV_LABEL' is left untouched)"
  VENV="$OUT/venv"
  python3 -m venv --system-site-packages "$VENV" >/dev/null 2>&1 || die "python3 -m venv failed (need python3-venv)."
  "$VENV/bin/pip" install -q --disable-pip-version-check duckdb pyyaml jsonschema 2>&1 | tail -3 | tee -a "$REPORT"
  export PATH="$VENV/bin:$PATH"
  python3 -c 'import duckdb,yaml,jsonschema' 2>/dev/null || \
    die "could not install duckdb/pyyaml/jsonschema. If PyPI is blocked here, 'conda install -n $CONDA_ENV python-duckdb pyyaml jsonschema' on a login node and resubmit."
  say "deps ok     : duckdb $(python3 -c 'import duckdb;print(duckdb.__version__)'), pyyaml, jsonschema"
fi

# ---------------------------------------------------------------------------
# 6. test workdir + optional site config
# ---------------------------------------------------------------------------
hdr "workdir"
# Keep the nf-test workdir off $HOME: hundreds of short-lived task dirs on NFS
# is both slow and quota-hostile.
DEFAULT_WORK="${SCRATCH:-/hpcnfs/scratch/P_DIMA_ATTEND/users/vfassi}/nf-test-clinarmonize"
export NFT_WORKDIR="${NFT_WORKDIR:-$DEFAULT_WORK}"
mkdir -p "$NFT_WORKDIR" 2>/dev/null || {
  say "WARNING     : cannot write $NFT_WORKDIR -- falling back to $REPO/.nf-test"
  export NFT_WORKDIR="$REPO/.nf-test"; mkdir -p "$NFT_WORKDIR"
}
say "NFT_WORKDIR : $NFT_WORKDIR"

# nf-test has no '-c' passthrough, so a site config must be layered into the
# configFile that nf-test.config already points at. Backed up and restored.
TESTCFG="tests/nextflow.config"
RESTORE_CFG=""
if [[ -n "$SITE_CONFIG" ]]; then
  [[ -f "$SITE_CONFIG" ]] || die "SITE_CONFIG='$SITE_CONFIG' does not exist."
  RESTORE_CFG="$OUT/nextflow.config.orig"
  cp "$TESTCFG" "$RESTORE_CFG"
  printf '\n// injected by verify_clinarmonize.sh (%s)\nincludeConfig %s\n' \
    "$STAMP" "'$(cd "$(dirname "$SITE_CONFIG")" && pwd)/$(basename "$SITE_CONFIG")'" >> "$TESTCFG"
  say "site config : $SITE_CONFIG (layered into $TESTCFG; original saved to $RESTORE_CFG)"
fi
cleanup() {
  if [[ -n "$RESTORE_CFG" && -f "$RESTORE_CFG" ]]; then
    cp "$RESTORE_CFG" "$TESTCFG" && echo "restored $TESTCFG"
  fi
}
trap cleanup EXIT INT TERM

# ---------------------------------------------------------------------------
# 6b. git provenance  (REQUIRED -- not cosmetic)
# ---------------------------------------------------------------------------
# Both tests/fixtures/make_fixtures.py:34 and workflows/harmonize.nf:127 shell
# out to `git rev-parse HEAD`. On an rsync'd tree with no .git:
#   * make_fixtures.py dies outright (CalledProcessError, exit 128), and
#   * currentGitSha() returns null, so harmonize.nf:175 SKIPS the
#     analysis_git_sha half of the --unseal hash check. The F4 test still
#     passes -- via the params_hash check -- but the git-sha branch it is
#     partly there to exercise never runs. The suite would look green while
#     proving strictly less.
# A repo with a single empty commit is enough: both sides read HEAD at run
# time, so any self-consistent SHA exercises the real code path.
hdr "git provenance"
if git rev-parse HEAD >/dev/null 2>&1; then
  say "git HEAD    : $(git rev-parse --short HEAD) (real history present)"
elif [[ -n "${NO_AUTO_GIT:-}" ]]; then
  die "no git HEAD here and NO_AUTO_GIT is set. Either rsync the .git directory across, or unset NO_AUTO_GIT to let this script create a throwaway repo."
else
  say "NOTICE      : this tree has no git history (rsync'd copy)."
  say "              Creating a throwaway repo with one empty commit so that"
  say "              make_fixtures.py and harmonize.nf's --unseal check both work."
  say "              Remove it afterwards with: rm -rf '$REPO/.git'"
  say "              For accurate provenance instead, rsync the .git directory across."
  git init -q 2>/dev/null || die "git init failed in $REPO"
  git -c user.email="verify@localhost" -c user.name="verify" \
      commit -q --allow-empty -m "rsync snapshot for verification $STAMP" 2>/dev/null \
      || die "could not create the empty commit (is git configured?)"
  say "git HEAD    : $(git rev-parse --short HEAD) (synthetic -- self-consistent, not the real commit)"
  say "WARNING     : provenance in this report is SYNTHETIC. Confirm the code matches d5b1cd1 yourself."
fi

# ---------------------------------------------------------------------------
# 6c. container spec drift
# ---------------------------------------------------------------------------
# Each modules/local/*/environment.yml is a verbatim copy of the canonical
# containers/duckdb-pyyaml/environment.yml (nf-core wants one per module; the
# image is built from the canonical one). Copies drift. If they do, `-profile
# conda` and `-profile singularity` silently run DIFFERENT software and the
# suite cannot tell you which one it proved anything about.
hdr "container spec"
CANON="containers/duckdb-pyyaml/environment.yml"
if [[ -f "$CANON" ]]; then
  drift=0
  for e in modules/local/*/environment.yml; do
    cmp -s "$CANON" "$e" || { say "DRIFT       : $e differs from $CANON"; drift=1; }
  done
  [[ "$drift" == 0 ]] \
    && say "conda spec  : all $(ls -1 modules/local/*/environment.yml | wc -l | tr -d ' ') module copies match $CANON" \
    || die "conda spec drift (above). Re-copy $CANON over the module copies."
  PINNED_IMAGE="$(grep -ho 'container "[^"]*"' modules/local/*/main.nf | sed 's/container //' | tr -d '\"' | sort -u | head -1)"
  say "pinned image: $PINNED_IMAGE"
  # Flag any container reference that is not digest-pinned.
  #
  # Done with shell `case`, deliberately, not grep. Two separate traps were hit
  # getting this right: a pattern for "looks like a tag" matches a digest too,
  # because in repo@sha256:<hex> a greedy [^"]* eats "@sha256" and the next
  # colon then reads as a tag separator. And the replacement that tested
  # `grep -qv` returned different exit statuses under ugrep (a Homebrew grep
  # replacement) than under the GNU grep on the cluster -- so the check passed
  # on the machine it was written on and would have been unreliable on the
  # machine that runs it. Pattern-matching a string in the shell has none of
  # that variance.
  unpinned=0
  for m in modules/local/*/main.nf; do
    line=$(sed -n 's/.*container "\([^"]*\)".*/\1/p' "$m" | head -1)
    [ -n "$line" ] || continue
    case "$line" in
      *@sha256:*) ;;
      *) [ "$unpinned" = 0 ] && say "WARNING     : not digest-pinned -- run tools/pin_container.sh"
         say "              $m -> $line"
         unpinned=$((unpinned+1)) ;;
    esac
  done
else
  say "NOTICE      : $CANON absent; skipping spec-drift check"
fi

# ---------------------------------------------------------------------------
# 6d. reader/writer compatibility  (REQUIRED -- not cosmetic)
# ---------------------------------------------------------------------------
# The pipeline writes parquet from INSIDE the container (duckdb 1.5.5). nf-test's
# assertions read it back with the HOST python3's duckdb. Those are different
# duckdbs on this cluster and cannot be made the same one:
#
#   duckdb >= 1.5.0 declares requires_python >= 3.10 and ships no cp39 wheel,
#   and the host interpreter here is python 3.9 -- so `pip install duckdb`
#   resolves to 1.4.5 and CANNOT resolve to 1.5.5.
#
# Pinning both sides equal would also be worse than the skew, not better: a
# reader and a writer of the same build share their bugs, so a parquet
# round-trip defect would round-trip cleanly and the suite would never see it.
#
# So the skew stays and gets asserted. This runs before any test, because a
# host that misreads the container's output produces assertion failures that
# look exactly like pipeline bugs -- and, worse, can produce assertion
# *successes* on values that were silently altered in transit.
hdr "parquet round-trip (container writes, host reads)"
PROBE="tools/parquet_roundtrip_probe.py"
PROBE_DIR="$OUT/roundtrip"
mkdir -p "$PROBE_DIR"
if [[ ! -f "$PROBE" ]]; then
  say "NOTICE      : $PROBE absent; skipping the round-trip check"
elif [[ -z "${PINNED_IMAGE:-}" ]]; then
  say "NOTICE      : no pinned image resolved above; skipping the round-trip check"
else
  case "$ENGINE" in
    singularity|apptainer)
      "$ENGINE" exec -B "$REPO:$REPO" --pwd "$REPO" \
        "docker://${PINNED_IMAGE}" python3 "$PROBE" write "$PROBE_DIR" 2>&1 | tee -a "$REPORT" ;;
    docker|podman)
      "$ENGINE" run --rm -v "$REPO:$REPO" -w "$REPO" \
        "$PINNED_IMAGE" python3 "$PROBE" write "$PROBE_DIR" 2>&1 | tee -a "$REPORT" ;;
  esac
  if [[ ! -f "$PROBE_DIR/candidates.parquet" ]]; then
    die "the container never wrote the round-trip fixtures -- the image is unusable, so every containerised test below would fail for that reason and not for a code reason. See the excerpt above."
  fi
  # The host half runs with whatever python3 section 5 left on PATH -- i.e.
  # exactly the interpreter nf-test's assertions will use.
  # PIPESTATUS[0], not $?: $? is tee's status, which is 0 even when the probe
  # fails. Read it on the very next line -- any intervening command clobbers it.
  python3 "$PROBE" read "$PROBE_DIR" 2>&1 | tee -a "$REPORT"
  if [[ "${PIPESTATUS[0]}" -ne 0 ]]; then
    die "host duckdb cannot faithfully read the container's parquet (above). Every parquet assertion in this suite is now untrustworthy in BOTH directions. Do not interpret the results below."
  fi
fi

# ---------------------------------------------------------------------------
# 7. fixtures
# ---------------------------------------------------------------------------
hdr "fixtures"
if python3 tests/fixtures/make_fixtures.py > "$OUT/make_fixtures.log" 2>&1; then
  say "make_fixtures.py: OK ($(grep -c . "$OUT/make_fixtures.log") files written)"
else
  say "make_fixtures.py: FAILED"
  tail -20 "$OUT/make_fixtures.log" | tee -a "$REPORT"
  exit 1
fi

# The Eunomia fixture is the one piece of test state that encodes the machine
# that built it: conf/test.config:32 points --input at test_data/samplesheet.csv,
# and fetch_eunomia.py:69 writes ABSOLUTE paths derived from its own repo root.
# test_data/ is gitignored, so on an rsync'd tree that file arrives holding the
# SOURCE machine's paths. nf-schema validates them synchronously at pipeline
# start, so every test that reaches parameter validation dies before a single
# process is scheduled -- 10 of them on the 20260817 run. Never trust the
# rsync'd copy: prove the paths resolve here, repair them if the data is
# present under a different root, and only re-fetch if it genuinely is absent.
SHEET="$REPO/test_data/samplesheet.csv"
fetch_eunomia() {
  say "eunomia     : fetching (needs outbound network)"
  python3 tests/fixtures/fetch_eunomia.py > "$OUT/fetch_eunomia.log" 2>&1 \
    || { tail -20 "$OUT/fetch_eunomia.log" | tee -a "$REPORT"
         die "fetch_eunomia.py failed -- no outbound network on this node? Copy test_data/ across and re-run."; }
}
if [[ ! -f "$SHEET" ]]; then
  say "eunomia     : no samplesheet.csv here"
  fetch_eunomia
fi
# --repair rewrites any path whose '.../test_data/...' tail resolves under $REPO
# and reports what it could not account for. Exit 0 = every path resolves.
if REPO="$REPO" python3 - "$SHEET" <<'EOF' >> "$REPORT" 2>&1
import csv, os, sys
sheet, repo = sys.argv[1], os.environ["REPO"]
rows = list(csv.DictReader(open(sheet)))
changed, bad = 0, []
for r in rows:
    p = r["path"]
    if os.path.exists(p):
        continue
    i = p.find("test_data/")
    cand = os.path.join(repo, p[i:]) if i != -1 else None
    if cand and os.path.exists(cand):
        r["path"], changed = cand, changed + 1
    else:
        bad.append(p)
if bad:
    print("eunomia     : %d path(s) unresolvable, re-fetch needed" % len(bad))
    for p in bad[:3]:
        print("              %s" % p)
    sys.exit(1)
if changed:
    with open(sheet, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=rows[0].keys())
        w.writeheader()
        w.writerows(rows)
    print("eunomia     : repaired %d rsync'd path(s) to this repo root" % changed)
else:
    print("eunomia     : samplesheet paths all resolve")
EOF
then :; else
  fetch_eunomia
  REPO="$REPO" python3 -c '
import csv,os,sys
rows=list(csv.DictReader(open(sys.argv[1])))
bad=[r["path"] for r in rows if not os.path.exists(r["path"])]
sys.exit(1 if bad else 0)' "$SHEET" \
    || die "samplesheet paths still do not resolve after a fresh fetch -- inspect $SHEET"
  say "eunomia     : re-fetched, paths now resolve"
fi

# ---------------------------------------------------------------------------
# 8. run
# ---------------------------------------------------------------------------
# The chunk split is about RUNTIME, and nothing else. SLOW_TESTS is the only
# hand-maintained list; FAST_TESTS is everything else found on disk.
#
# This was two hand-maintained lists until 20260818, and the omission that
# caused is the reason it is not any more. tests/link_score.nf.test,
# tests/map_concepts.nf.test and tests/units.nf.test were in NEITHER list, so
# 13 of the suite's 61 tests had never executed under a container -- while the
# report said "ALL REQUESTED TESTS PASSED" and named no shortfall. Every
# phase since §3 landed a test file that this harness silently declined to
# run, and each of those phases recorded F2 as clean on the strength of it.
#
# A hand-maintained list is a list that goes stale on the next phase that
# adds a file. Deriving from the filesystem means a new test file is IN by
# default and has to be deliberately moved to be slow -- the opposite failure
# direction, and the survivable one.
SLOW_TESTS="tests/invariant.nf.test tests/invariant_leak_control.nf.test"

ALL_TESTS=""
for _t in tests/*.nf.test; do
  [[ -e "$_t" ]] && ALL_TESTS="$ALL_TESTS $_t"
done
[[ -n "$ALL_TESTS" ]] || die "no tests/*.nf.test found in $REPO -- refusing to report a verdict over an empty suite."

FAST_TESTS=""
for _t in $ALL_TESTS; do
  case " $SLOW_TESTS " in
    *" $_t "*) ;;
    *) FAST_TESTS="$FAST_TESTS $_t" ;;
  esac
done

# A SLOW_TESTS entry that no longer exists means a rename moved a slow test
# out of every chunk. Refuse: a verdict is worth exactly what it measured.
for _t in $SLOW_TESTS; do
  [[ -f "$_t" ]] || die "SLOW_TESTS names '$_t', which does not exist. It was renamed or deleted and is now in NO chunk; fix the list rather than reporting a verdict that skips it."
done

hdr "suite coverage"
say "test files  : $(echo $ALL_TESTS | wc -w | tr -d ' ') on disk"
say "fast chunk  : $(echo $FAST_TESTS | wc -w | tr -d ' ') file(s)"
say "slow chunk  : $(echo $SLOW_TESTS | wc -w | tr -d ' ') file(s)"
if [[ "$CHUNK" != "all" ]]; then
  say "WARNING     : CHUNK=$CHUNK -- this run measures ONE chunk. The verdict below covers only it."
fi

run_chunk() {
  local name="$1"; shift
  local tests="$*"
  hdr "chunk: $name  (profile=test,+$ENGINE)"
  local log="$OUT/nf-test-$name.log"
  local t0=$SECONDS rc dt passed failed
  # shellcheck disable=SC2086
  nf-test test $tests --profile="+$ENGINE" > "$log" 2>&1
  rc=$?
  dt=$((SECONDS - t0))
  passed=$(grep -ac 'PASSED' "$log" 2>/dev/null); passed=${passed:-0}
  failed=$(grep -ac 'FAILED' "$log" 2>/dev/null); failed=${failed:-0}
  say "$name: exit=$rc passed=$passed failed=$failed elapsed=${dt}s"
  if [[ $rc -ne 0 || $failed -gt 0 ]]; then
    say "--- failure excerpt ($name) ---"
    grep -aE 'FAILED|Assertion|Caused by|Command error|error:|No such file' "$log" | head -40 | tee -a "$REPORT"
    say "--- end excerpt ---"
  fi
  return $rc
}

RC=0
case "$CHUNK" in
  fast)      run_chunk fast      $FAST_TESTS || RC=1 ;;
  invariant) run_chunk invariant $SLOW_TESTS || RC=1 ;;
  all)       run_chunk fast      $FAST_TESTS || RC=1
             run_chunk invariant $SLOW_TESTS || RC=1 ;;
  *)         die "CHUNK must be all|fast|invariant (got '$CHUNK')" ;;
esac

# ---------------------------------------------------------------------------
# 9. evidence
# ---------------------------------------------------------------------------
hdr "evidence"
# nextflow.log and trace.csv are the real progress record: nf-test's stdout
# stays silent from the start of a test until it finishes, so a healthy
# hour-long run and a stalled one look identical there.
find "$NFT_WORKDIR" -maxdepth 3 -name 'nextflow.log' 2>/dev/null | head -20 | while read -r f; do
  d="$OUT/logs/$(basename "$(dirname "$(dirname "$f")")")"
  mkdir -p "$d" && cp "$f" "$d/" 2>/dev/null
  cp "$(dirname "$f")/trace.csv" "$d/" 2>/dev/null
done
TARBALL="$REPO/clinarmonize-verification-$STAMP.tar.gz"
tar czf "$TARBALL" -C "$REPO" "$(basename "$OUT")" 2>/dev/null
say "report      : $REPORT"
say "tarball     : $TARBALL"

hdr "VERDICT"
# The verdict names its own SCOPE. "ALL REQUESTED TESTS PASSED" was true of
# the 20260818 run that skipped 13 tests, and that is precisely the problem:
# a reader has to be able to tell a full pass from a partial one without
# doing arithmetic on two numbers printed 300 lines apart.
case "$CHUNK" in
  all)  VERDICT_SCOPE="all $(echo $ALL_TESTS | wc -w | tr -d ' ') test file(s)" ;;
  fast) VERDICT_SCOPE="the fast chunk ONLY ($(echo $FAST_TESTS | wc -w | tr -d ' ') of $(echo $ALL_TESTS | wc -w | tr -d ' ') file(s)) -- NOT a full-suite result" ;;
  *)    VERDICT_SCOPE="the $CHUNK chunk ONLY ($(echo $SLOW_TESTS | wc -w | tr -d ' ') of $(echo $ALL_TESTS | wc -w | tr -d ' ') file(s)) -- NOT a full-suite result" ;;
esac
if [[ $RC -eq 0 ]]; then
  say "PASSED under $ENGINE: $VERDICT_SCOPE"
else
  say "FAILURES PRESENT under $ENGINE ($VERDICT_SCOPE) -- see excerpts above and the tarball"
fi

echo "=================================================="
echo "End time:    $(date)"
echo "Exit status: $RC"
echo "=================================================="

printf '\n----- paste everything below this line -----\n\n'
cat "$REPORT"
exit $RC
