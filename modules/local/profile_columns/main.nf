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

    // A Wave-built image resolved from a conda spec ("duckdb=1.5.5
    // pyyaml=6.0.2" against conda-forge+bioconda), not a base image plus a
    // runtime install: duckdb/PyYAML are baked in, so the digest below
    // actually covers what runs, not just what the container started as
    // (the §11.2 Trap this stood in for prior to fix round 1 -- see
    // task-3-report.md). It also ships bash, python3 and procps (ps),
    // which Nextflow's own task wrapper requires for resource-metrics
    // collection (trace.enabled=true) before this process's script ever
    // runs.
    container "wave.seqera.io/wt/211e562aa32e/wave/build:duckdb-1.5.5_pyyaml-6.0.2--d70265250861aaf1@sha256:af953fd9ecb445cb0e62ecb3ca0427c2abb805feb0e7f3e9fd41982e9e03c756"

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
