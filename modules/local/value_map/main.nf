//
// §6.3 — map value vocabularies, and show every collapse.
//
// Contract (docs/steps/s6-3.md):
//   IN   mapped rows with categorical values + value-map rules
//   OUT  same rows, value_as_concept_id set
//        qc/value_collapse.json  [{rule_id, from:[str], to:str, n_rows}]
//        qc/alluvial_<variable>.png   raw value -> canonical concept
//   SIDE none
//
// This process is now the LAST writer of mapped/, and therefore its only
// publisher. §6.2 took that role from MAP_CONCEPTS when it started rewriting
// the same five tables plus _unmapped.parquet under the same names; §6.3
// takes it from CONVERT_UNITS for exactly the same reason. Two processes
// with a publishDir into ${params.outdir} are resolved by whichever task
// finishes last, so results/mapped/ would carry value-mapped or unmapped
// rows depending on task scheduling -- and a categorical column that
// silently kept its source grain is the same class of error as a unit that
// was never converted. CONVERT_UNITS still publishes its own
// qc/unit_conversions.json; only mapped/ moved (conf/modules.config).
//
// The staged input lands in mapped_in/ and the output is written to mapped/,
// which also means a task that fails under --unmapped_value_policy fail
// publishes nothing at all -- correct, because the alternative is a
// results/mapped/ whose value vocabulary the run itself declared incomplete.
//
// One whole-run task, for the reason MAP_CONCEPTS and CONVERT_UNITS are:
// the alluvial plot is per pack VARIABLE, and two cohorts' columns mapping
// to one variable are one picture. A per-dataset task would draw half of it
// and could not see the other half's collapse.
//
process VALUE_MAP {
    tag "map:values:${replicate == null ? 'baseline' : 'p' + replicate}"
    label 'process_single'

    conda "${moduleDir}/environment.yml"
    container "docker.io/bolt3x/clinarmonize-duckdb@sha256:056f3260afbddaf99bfbd881b25f318b24ead3103bc626e4401e4f5afa03a7e0"

    input:
    // Keyed by `replicate` and staged as ONE tuple, same reasoning as
    // MAP_CONCEPTS' and CONVERT_UNITS' inputs (ADR-004): the map-scoped
    // harness runs this on every permuted replicate, and pairing a
    // replicate's tables with another replicate's by channel POSITION would
    // be correct only for as long as there is exactly one of them.
    tuple val(replicate), path(mapped_files, stageAs: 'mapped_in/*')
    path  ruleset
    val   pack_variables_json
    val   value_params_json

    output:
    tuple val(replicate), path("mapped/*.parquet"),         emit: mapped
    tuple val(replicate), path("qc/value_collapse.json"),   emit: collapse
    tuple val(replicate), path("qc/value_unmapped.json"),   emit: unmapped_values
    // Optional because --emit_alluvial can be false and because a run whose
    // ledger declares no value_map at all draws nothing. NOT optional in the
    // sense the nogo forbids: bin/value_map.py records on every qc entry
    // whether a plot was drawn and why, so an absent PNG is never a silent
    // suppression.
    tuple val(replicate), path("qc/alluvial_*.png"),        emit: alluvial, optional: true
    path "versions.yml",                                    emit: versions

    script:
    """
    mkdir -p mapped qc
    value_map.py \\
        --mapped-glob 'mapped_in/*.parquet' \\
        --ruleset '${ruleset}' \\
        --pack-variables '${pack_variables_json}' \\
        --value-params '${value_params_json}' \\
        --out-dir mapped \\
        --out-qc qc/value_collapse.json \\
        --out-unmapped qc/value_unmapped.json \\
        --out-plot-dir qc

    cat <<-END_VERSIONS > versions.yml
    "${task.process}":
        python: \$(python3 --version | sed 's/Python //')
        duckdb: \$(python3 -c "import duckdb; print(duckdb.__version__)")
    END_VERSIONS
    """
}
