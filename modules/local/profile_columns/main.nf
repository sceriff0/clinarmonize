//
// §2.1 / §2.2 / §2.3 — profile every column of one admitted dataset into a
// typed evidence record, with ranked candidate units and a manifest of
// unprofilable columns.
//
// Contract (docs/steps/s2-1.md):
//   IN   [ meta(cohort_id, dataset_id), path(table) ]
//   OUT  profiles/<cohort_id>.<dataset_id>.json   one record per COLUMN
//   SIDE none — a pure function of the table bytes and the params below
//
// The run-level failure-rate gate (§2.3's SIDE: "exit non-zero when the
// failure RATE exceeds --max_failed_frac") is enforced by the CALLER
// (workflows/harmonize.nf), once every dataset in the run has been counted
// — a single dataset's failure rate is not the run's failure rate.
//
// This is the first process in the pipeline to ever read a table's bytes
// (everything upstream deliberately does not).
//
// Not here (nogo, all three cards): never normalises/lowercases/strips a
// column name (§4.1 needs it verbatim); never drops a column that fails to
// parse (§2.3 manifests it instead); never converts a value or resolves a
// unit ambiguity; never reads the outcome column differently from any other
// (§0 the invariant) — the concept pack is not even an input here.
//
process PROFILE_COLUMNS {
    tag "${meta.cohort_id}:${meta.dataset_id}"
    label 'process_single'

    // The runtime for the whole profiling/ledger phase: python3 with duckdb
    // and PyYAML baked in, not installed at task time, so the digest actually
    // covers what runs rather than just what the container started as (the
    // §11.2 Trap this stood in for prior to fix round 1 -- see
    // task-3-report.md). It also ships bash and procps (ps), which Nextflow's
    // own task wrapper requires for resource-metrics collection
    // (trace.enabled=true) before this process's script ever runs.
    //
    // Built by containers/duckdb-pyyaml/Dockerfile from the SAME
    // environment.yml the conda directive below points at, so the two
    // delivery paths cannot drift.
    //
    // This replaces a Wave ephemeral build
    // (wave.seqera.io/wt/211e562aa32e/...) that stopped resolving within four
    // days of being pinned: nextflow.config set wave.freeze = true with no
    // wave.build.repository, so the frozen image lived only in Wave's own
    // store and its token expired. A digest is only as durable as the
    // cheapest place someone can still fetch it from.
    conda "${moduleDir}/environment.yml"
    container "docker.io/bolt3x/clinarmonize-duckdb:1.5.5_pyyaml6.0.2"

    input:
    tuple val(meta), path(table)
    path  unit_patterns
    val   params_json

    output:
    tuple val(meta), path("${prefix}.json"),        emit: profile
    tuple val(meta), path("${prefix}.failed.json"), emit: failed
    path "versions.yml",                             emit: versions

    script:
    prefix = "${meta.cohort_id}.${meta.dataset_id}"
    """
    profile_columns.py \\
        --table '${table}' \\
        --cohort-id '${meta.cohort_id}' \\
        --dataset-id '${meta.dataset_id}' \\
        --params '${params_json}' \\
        --unit-patterns '${unit_patterns}' \\
        --out-profile '${prefix}.json' \\
        --out-failed '${prefix}.failed.json'

    cat <<-END_VERSIONS > versions.yml
    "${task.process}":
        python: \$(python3 --version | sed 's/Python //')
        duckdb: \$(python3 -c "import duckdb; print(duckdb.__version__)")
        pyyaml: \$(python3 -c "import yaml; print(yaml.__version__)")
    END_VERSIONS
    """
}
