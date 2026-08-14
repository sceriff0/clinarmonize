//
// §4.1 — generate candidate concepts from the pack's declared variable set.
//
// Contract (docs/steps/s4-1.md):
//   IN   profiles/*.json + the concept pack + the pinned ATHENA release
//   OUT  propose/candidates.parquet (cohort_id, dataset_id, column,
//                                    variable, concept_id, generator_id)
//   SIDE none; a column with zero candidates is emitted with a null concept_id
//
// Ruling R14 -- there is no ATHENA release in this repo. Candidates are
// generated against the PACK's declared variables and their concept_ids
// (bin/propose_candidates.py); the pinned release id is read from the
// pack's `vocabulary` key and recorded here, in versions.yml, alongside the
// tools that ran -- never resolved, never fetched. A change to it already
// changes the pack's bytes, which is what INGEST's pack_hash (§1.1) covers;
// this process additionally records the raw value for direct inspection.
//
// One task per REPLICATE, not per (dataset, replicate): `replicate` is the
// baseline profiles' key when it is null and the harness's seed otherwise
// (workflows/harmonize.nf groups PROFILE_COLUMNS.out.profile and
// PROFILE_COLUMNS_PERMUTED.out.profile by meta.replicate before calling
// this process), and profile_jsons is every dataset's profile for that one
// replicate, collected. A ledger (§4.3, Task 5c) spans every dataset in the
// run, so candidate generation has to see them all at once, not one at a
// time.
//
process PROPOSE_CANDIDATES {
    tag "replicate:${replicate ?: 'baseline'}"
    label 'process_single'

    // Same digest-pinned Wave image as the profiling stage (python3, duckdb
    // 1.5.5, pyyaml 6.0.2 baked in, not installed at runtime) -- this
    // process only needs python3 + duckdb, and reusing the one image this
    // pipeline already asks you to trust keeps that count at one.
    container "wave.seqera.io/wt/211e562aa32e/wave/build:duckdb-1.5.5_pyyaml-6.0.2--d70265250861aaf1@sha256:af953fd9ecb445cb0e62ecb3ca0427c2abb805feb0e7f3e9fd41982e9e03c756"

    input:
    tuple val(replicate), path(profile_jsons, stageAs: 'profiles_in/*')
    val   pack_variables_json
    val   vocabulary_release
    val   generator_params_json

    output:
    tuple val(replicate), path("${prefix}.parquet"), emit: candidates
    path "versions.yml",                             emit: versions

    script:
    // The baseline replicate is null (s4-1 brief: "Aggregate per
    // REPLICATE... baseline replicate is null"); its file is the literal
    // path the card's OUT slot names, `candidates.parquet`. A harness
    // replicate's file carries its seed the same way PERMUTE_OUTCOME's
    // permuted tables do (<prefix>.p<seed>.*), so 100 replicates never
    // collide on one filename.
    prefix = replicate == null ? 'candidates' : "candidates.p${replicate}"
    """
    propose_candidates.py \\
        --profile-glob 'profiles_in/*.json' \\
        --pack-variables '${pack_variables_json}' \\
        --generator-params '${generator_params_json}' \\
        --out-candidates '${prefix}.parquet'

    cat <<-END_VERSIONS > versions.yml
    "${task.process}":
        python: \$(python3 --version | sed 's/Python //')
        duckdb: \$(python3 -c "import duckdb; print(duckdb.__version__)")
        vocabulary_release: "${vocabulary_release}"
    END_VERSIONS
    """
}
