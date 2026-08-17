//
// §3.2 — score pairs with a Fellegi–Sunter model, missing as its own outcome.
//
// Contract (docs/steps/s3-2.md):
//   IN   candidate pairs + the cohort's records
//   OUT  link/scores.parquet  (left_id, right_id, match_weight,
//                              per_field_agreement)
//        link/model.json      the estimated m and u per field, per level
//   SIDE none
//
// scores.parquet's per_field_agreement is a LIST of STRUCT — the first
// NESTED parquet type this pipeline writes. tools/parquet_roundtrip_probe.py
// gained a LIST/STRUCT fixture in the same change: the container ships
// duckdb 1.5.5 and the cluster host resolves 1.4.5 (it cannot resolve 1.5.x
// — duckdb >= 1.5.0 requires python >= 3.10 and the host interpreter is
// 3.9), and nested encodings are where two minor versions have a genuinely
// new surface. VARCHAR/BIGINT/DOUBLE was already proven identical in both
// directions; this is not.
//
// One task for the whole run, matching LINK_BLOCKING: m is estimated by EM
// over the candidate set and u by sampling random pairs, and both are
// run-level quantities. Fitting them per cohort would make match weights
// incomparable across the UNION — which is the one thing a log-likelihood
// ratio is for.
//
process LINK_SCORE {
    tag "link:score"
    label 'process_single'

    conda "${moduleDir}/environment.yml"
    container "docker.io/bolt3x/clinarmonize-duckdb@sha256:056f3260afbddaf99bfbd881b25f318b24ead3103bc626e4401e4f5afa03a7e0"

    input:
    val   tables_json
    path  candidate_pairs
    path  comparison_spec
    val   outcome_variables_json
    val   em_iterations
    val   u_from_random_pairs
    val   random_pair_n

    output:
    path "link/scores.parquet", emit: scores
    path "link/model.json",     emit: model
    path "versions.yml",        emit: versions

    script:
    """
    mkdir -p link
    link_score.py \\
        --tables '${tables_json}' \\
        --pairs '${candidate_pairs}' \\
        --comparison-spec '${comparison_spec}' \\
        --outcome-variables '${outcome_variables_json}' \\
        --em-iterations ${em_iterations} \\
        --u-from-random-pairs ${u_from_random_pairs} \\
        --random-pair-n ${random_pair_n} \\
        --out-scores link/scores.parquet \\
        --out-model link/model.json

    cat <<-END_VERSIONS > versions.yml
    "${task.process}":
        python: \$(python3 --version | sed 's/Python //')
        duckdb: \$(python3 -c "import duckdb; print(duckdb.__version__)")
        pyyaml: \$(python3 -c "import yaml; print(yaml.__version__)")
    END_VERSIONS
    """
}
