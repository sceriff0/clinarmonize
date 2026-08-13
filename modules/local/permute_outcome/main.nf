//
// §10.1 — the InvariantProbe seam: perturb(inputs, seed) -> inputs.
//
// Contract (docs/steps/s10-1.md):
//   IN   [ meta(cohort_id, dataset_id), path(table), val(seed) ]
//   OUT  one permuted copy of the table + a manifest of what was permuted
//   SIDE none — a pure function of the table bytes, the pack, and the seed
//
// Runs once per DATASET and emits every replicate from that one task. The
// card's loop body is `permute outcome column WITHIN each cohort`, and a
// dataset belongs to exactly one cohort, so a per-dataset shuffle can never
// move a value across a cohort boundary. That is the §10.1 Trap ("permuting
// across cohorts instead of within") closed structurally rather than by an
// assertion — there is no channel shape here through which a value could
// reach another cohort.
//
// The replicate axis is batched INSIDE the task rather than fanned out
// across 100 of them. Shuffling one column 100 times is microseconds of
// work; 100 container starts to do it is roughly a thousand times its own
// cost, and §14.2 requires this harness on every push. A test whose price
// is dominated by scheduling overhead is a test that gets moved to a
// nightly and then to nobody — which is the switched-off gate §10.1's
// nogo forbids, arrived at through the build system instead of the
// assertion. Profiling downstream is NOT batched: that one has to travel
// the real per-dataset code path (see the alias in workflows/harmonize.nf).
//
// This process reads the concept pack, which PROFILE_COLUMNS deliberately
// does not. The asymmetry is the invariant, not an inconsistency: profiling
// must treat the outcome column exactly like every other column (§0), while
// the harness's entire job is to find the outcome column and disturb it.
//
// Not here (nogo): never converts, rounds, retypes or reformats a value —
// every non-outcome byte must survive untouched, or the ledger could move
// for a reason that has nothing to do with the outcome and the test would
// report a leak that is really a formatting change.
//
process PERMUTE_OUTCOME {
    tag "${meta.cohort_id}:${meta.dataset_id}"
    label 'process_single'

    // Same digest-pinned Wave image as the profiling stage: this script
    // needs python3 + PyYAML (to read the pack's outcome flag) and nothing
    // else, so reusing one image across the phase keeps the number of
    // containers this pipeline asks you to trust at one.
    container "wave.seqera.io/wt/211e562aa32e/wave/build:duckdb-1.5.5_pyyaml-6.0.2--d70265250861aaf1@sha256:af953fd9ecb445cb0e62ecb3ca0427c2abb805feb0e7f3e9fd41982e9e03c756"

    input:
    tuple val(meta), path(table)
    path  pack
    val   seeds

    output:
    // One entry per replicate. The seed is encoded in each filename
    // (<cohort>.<dataset>.p<seed>.csv) because that is what carries the
    // replicate id back across the process boundary — the workflow reads it
    // off the name to re-key the channel, so every downstream stage stays
    // attributed to the permutation it came from.
    tuple val(meta), path("${prefix}.p*.csv"),             emit: tables
    path "${prefix}.p*.permutation.json",                  emit: manifest
    path "versions.yml",                                   emit: versions

    script:
    prefix = "${meta.cohort_id}.${meta.dataset_id}"
    """
    permute_outcome.py \\
        --table '${table}' \\
        --pack '${pack}' \\
        --seeds '${seeds.join(',')}' \\
        --cohort-id '${meta.cohort_id}' \\
        --dataset-id '${meta.dataset_id}' \\
        --out-prefix '${prefix}'

    cat <<-END_VERSIONS > versions.yml
    "${task.process}":
        python: \$(python3 --version | sed 's/Python //')
        pyyaml: \$(python3 -c "import yaml; print(yaml.__version__)")
    END_VERSIONS
    """
}
