//
// §6.1 — apply concept mappings into OMOP rows.
//
// Contract (docs/steps/s6-1.md):
//   IN   linked cohort tables + rules/ruleset.json + pinned vocabulary
//   OUT  mapped/<table>.parquet    one row per (person, concept, datetime)
//        mapped/_unmapped.parquet  every source value with no standard concept
//   SIDE none
//
// One whole-run task, not one per dataset. A rule names its own
// (cohort_id, dataset_id, column) and the mapped tables are a UNION across
// every admitted dataset, so splitting by dataset would produce N partial
// copies of each CDM table for a later stage to concatenate -- and
// --max_unmapped_frac is a claim about the RUN's coverage, which no single
// dataset's task can evaluate.
//
// The "pinned vocabulary" of the IN slot is the pack's `vocabulary` key,
// recorded in versions.yml below and never resolved (Ruling R14; see
// bin/map_concepts.py's own docstring for why, and for what changes the day
// a release is vendored).
//
// mapped/_unmapped.parquet is emitted UNCONDITIONALLY, empty or not — the
// card's done-when says so in as many words, and a mapper that only writes
// files it has rows for makes "no unmapped values" and "the stage never ran"
// the same observation.
//
process MAP_CONCEPTS {
    tag "map:concepts"
    label 'process_single'

    conda "${moduleDir}/environment.yml"
    container "docker.io/bolt3x/clinarmonize-duckdb@sha256:056f3260afbddaf99bfbd881b25f318b24ead3103bc626e4401e4f5afa03a7e0"

    input:
    val   tables_json
    path  ruleset
    path  links
    val   pack_variables_json
    val   vocabulary_release
    val   map_params_json

    output:
    path "mapped/*.parquet",             emit: mapped
    path "mapped/_unmapped.parquet",     emit: unmapped
    path "versions.yml",                 emit: versions

    script:
    """
    mkdir -p mapped
    map_concepts.py \\
        --tables '${tables_json}' \\
        --ruleset '${ruleset}' \\
        --links '${links}' \\
        --pack-variables '${pack_variables_json}' \\
        --vocabulary-release '${vocabulary_release}' \\
        --map-params '${map_params_json}' \\
        --out-dir mapped

    cat <<-END_VERSIONS > versions.yml
    "${task.process}":
        python: \$(python3 --version | sed 's/Python //')
        duckdb: \$(python3 -c "import duckdb; print(duckdb.__version__)")
        vocabulary: ${vocabulary_release}
    END_VERSIONS
    """
}
