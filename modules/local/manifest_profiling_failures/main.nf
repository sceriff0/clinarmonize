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

    // Same image as PROFILE_COLUMNS (fix round 1): this process
    // only needs python3's stdlib (json/glob), but reusing one digest-pinned
    // image across the whole profiling stage means one less container to
    // pull and trust, rather than pinning a second, unrelated base image.
    conda "${moduleDir}/environment.yml"
    container "docker.io/bolt3x/clinarmonize-duckdb@sha256:056f3260afbddaf99bfbd881b25f318b24ead3103bc626e4401e4f5afa03a7e0"

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
