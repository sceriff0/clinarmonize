//
// §6.2 — convert units through UCUM, refusing ambiguity.
//
// Contract (docs/steps/s6-2.md):
//   IN   mapped/<domain>.parquet + rules (unit_in, unit_out) + pack ranges
//   OUT  same rows, value_as_number converted; unit_concept_id set
//        qc/unit_conversions.json  [{rule_id, from, to, factor, n_rows}]
//   SIDE fails the run on an ambiguous or missing conversion factor
//
// This process REPLACES mapped/ rather than adding a directory beside it.
// The card's OUT slot is "same rows, value_as_number converted", and §6.3's
// IN slot is "mapped rows with categorical values" -- §6 is a chain over one
// artefact, not three copies of it at different stages of completeness. So
// conf/modules.config publishes THIS process's mapped/ and not
// MAP_CONCEPTS's: a results/mapped/ written twice, once unconverted and once
// converted, would be resolved by whichever task finished last.
//
// The staged input therefore lands in mapped_in/ and the output is written
// to mapped/, which also means a task that fails the post-condition
// publishes nothing at all -- correct, because the alternative is a
// results/mapped/ carrying values the run itself declared implausible.
//
// One whole-run task, for the reason MAP_CONCEPTS is: --plausible_range's
// post-condition is evaluated per pack VARIABLE across every rule that
// writes it, and two cohorts' columns mapping to one variable are one
// distribution. A per-dataset task could not see the other half of its own
// check, and a half-sized cohort's wrong factor would hide inside a right
// one.
//
process CONVERT_UNITS {
    tag "map:units:${replicate == null ? 'baseline' : 'p' + replicate}"
    label 'process_single'

    conda "${moduleDir}/environment.yml"
    container "docker.io/bolt3x/clinarmonize-duckdb@sha256:056f3260afbddaf99bfbd881b25f318b24ead3103bc626e4401e4f5afa03a7e0"

    input:
    // Keyed by `replicate` and staged as ONE tuple for the same reason
    // MAP_CONCEPTS's input is (ADR-004): the map-scoped harness runs this
    // on every permuted replicate, and pairing a replicate's tables with
    // another replicate's by channel POSITION would be correct only for as
    // long as there is exactly one of them.
    tuple val(replicate), path(mapped_files, stageAs: 'mapped_in/*')
    path  ruleset
    val   pack_variables_json
    // A `path`, not a val: --unit_conversion_table is the only source of
    // conversions this pipeline has, so it is staged into the task and
    // therefore recorded in the run's provenance like any other input file.
    // A path string read from inside the container would resolve against
    // whatever the container could see.
    path  unit_conversion_table
    val   convert_params_json

    output:
    tuple val(replicate), path("mapped/*.parquet"),          emit: mapped
    tuple val(replicate), path("qc/unit_conversions.json"),  emit: conversions
    path "versions.yml",                                     emit: versions

    script:
    """
    mkdir -p mapped qc
    convert_units.py \\
        --mapped-glob 'mapped_in/*.parquet' \\
        --ruleset '${ruleset}' \\
        --pack-variables '${pack_variables_json}' \\
        --unit-conversion-table '${unit_conversion_table}' \\
        --convert-params '${convert_params_json}' \\
        --out-dir mapped \\
        --out-qc qc/unit_conversions.json

    cat <<-END_VERSIONS > versions.yml
    "${task.process}":
        python: \$(python3 --version | sed 's/Python //')
        duckdb: \$(python3 -c "import duckdb; print(duckdb.__version__)")
        pyyaml: \$(python3 -c "import yaml; print(yaml.__version__)")
    END_VERSIONS
    """
}
