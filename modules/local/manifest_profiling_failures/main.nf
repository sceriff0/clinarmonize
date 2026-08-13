//
// §2.3 — the run-level half of the FailurePolicy seam. Collates every
// dataset's per-column failure manifest (PROFILE_COLUMNS.out.failed) into
// the single profiles/_failed.json the contract names, and enforces the
// SIDE clause: exit non-zero once the failure RATE (failed columns / all
// columns seen across the WHOLE run) exceeds --max_failed_frac.
//
// This has to be a real process (not a plain Groovy channel operator) so a
// pipeline-level exit-non-zero here shows up the same way any other process
// failure does — a channel operator's error() from inside a subscribe
// callback runs on a background dataflow thread and does not surface
// through the normal per-process stdout/stderr Nextflow (and nf-test) show
// the user; a failing process does.
//
process MANIFEST_PROFILING_FAILURES {
    label 'process_single'

    // Same Wave-built image as PROFILE_COLUMNS (fix round 1): this process
    // only needs python3's stdlib (json/glob), but reusing one digest-pinned
    // image across the whole profiling stage means one less container to
    // pull and trust, rather than pinning a second, unrelated base image.
    container "wave.seqera.io/wt/211e562aa32e/wave/build:duckdb-1.5.5_pyyaml-6.0.2--d70265250861aaf1@sha256:af953fd9ecb445cb0e62ecb3ca0427c2abb805feb0e7f3e9fd41982e9e03c756"

    input:
    path profile_jsons, stageAs: 'profiles_in/*'
    path failed_jsons,  stageAs: 'failed_in/*'
    val  max_failed_frac

    output:
    path "_failed.json", emit: manifest
    path "versions.yml", emit: versions

    script:
    """
    manifest_profiling_failures.py \\
        --profile-glob 'profiles_in/*.json' \\
        --failed-glob 'failed_in/*.json' \\
        --max-failed-frac ${max_failed_frac} \\
        --out-failed _failed.json

    cat <<-END_VERSIONS > versions.yml
    "${task.process}":
        python: \$(python3 --version | sed 's/Python //')
    END_VERSIONS
    """
}
