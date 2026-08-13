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

    container "docker.io/library/python@sha256:dd4fe98ab39f91e936f8e7e7a65a3ce59ecfb11e32f9a125b3132779920ba7f7"

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
