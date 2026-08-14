//
// §4.2 — score each candidate on six independent evidence channels.
//
// Contract (docs/steps/s4-2.md):
//   IN   propose/candidates.parquet + profiles/*.json + pack + vocabulary
//   OUT  propose/evidence.parquet (candidate_key, channel, score, detail)
//        propose/confirmation_plots/<candidate_key>.svg  (Ruling R16: SVG,
//        not PNG, in phase 0 -- no plotting library is on the §0.8
//        toolchain pins and Docker is unavailable to build one in)
//   SIDE none -- a pure function; this purity is what §10.1 tests
//
// One task per REPLICATE, exactly like PROPOSE_CANDIDATES: `candidates` and
// `profile_jsons` are that SAME replicate's candidates.parquet and every
// dataset's profile for it (workflows/harmonize.nf joins
// PROPOSE_CANDIDATES.out.candidates with the replicate-keyed profile
// channel PROPOSE_CANDIDATES itself already consumes, before calling this
// process).
//
// Ruling R14 -- value_overlap's reference is the PACK's own domain_values
// (no ATHENA release exists in this repo); a variable with none reports
// that it could not score, never a silent 0.
//
// Ruling R15 -- distribution_distance is circular in this phase (its
// reference is a CONFIRMED column, and §5 confirm is a later task). The
// channel is implemented and left enabled; with no reference source wired
// in yet it always reports "no reference" and is excluded from any later
// weighted total (§4.3's job, not this process's).
//
process PROPOSE_CHANNELS {
    tag "replicate:${replicate ?: 'baseline'}"
    label 'process_single'

    // Same digest-pinned Wave image PROFILE_COLUMNS and PROPOSE_CANDIDATES
    // use (python3, duckdb 1.5.5, pyyaml 6.0.2 baked in) -- this process
    // only needs python3 + duckdb, so reusing it keeps the pipeline's
    // trusted-image count at one.
    container "wave.seqera.io/wt/211e562aa32e/wave/build:duckdb-1.5.5_pyyaml-6.0.2--d70265250861aaf1@sha256:af953fd9ecb445cb0e62ecb3ca0427c2abb805feb0e7f3e9fd41982e9e03c756"

    input:
    tuple val(replicate), path(candidates_parquet), path(profile_jsons, stageAs: 'profiles_in/*')
    val   pack_variables_json
    val   channel_params_json

    output:
    tuple val(replicate), path("${prefix}.parquet"), emit: evidence
    tuple val(replicate), path("${plots_dir}"),       emit: confirmation_plots
    path "versions.yml",                              emit: versions

    script:
    // Same baseline/replicate naming convention as PROPOSE_CANDIDATES:
    // the baseline replicate (null) gets the literal path §4.2's OUT slot
    // names; a harness replicate carries its seed so 100 replicates never
    // collide on one filename.
    prefix = replicate == null ? 'evidence' : "evidence.p${replicate}"
    plots_dir = replicate == null ? 'confirmation_plots' : "confirmation_plots.p${replicate}"
    """
    mkdir -p '${plots_dir}'
    propose_channels.py \\
        --candidates '${candidates_parquet}' \\
        --profile-glob 'profiles_in/*.json' \\
        --pack-variables '${pack_variables_json}' \\
        --channel-params '${channel_params_json}' \\
        --out-evidence '${prefix}.parquet' \\
        --out-plots-dir '${plots_dir}'

    cat <<-END_VERSIONS > versions.yml
    "${task.process}":
        python: \$(python3 --version | sed 's/Python //')
        duckdb: \$(python3 -c "import duckdb; print(duckdb.__version__)")
    END_VERSIONS
    """
}
