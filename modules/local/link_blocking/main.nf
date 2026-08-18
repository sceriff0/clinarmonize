//
// §3.1 — block candidate pairs so comparison is tractable.
//
// Contract (docs/steps/s3-1.md):
//   IN   a cohort's datasets, one row per patient-record
//   OUT  candidate pairs (left_id, right_id, blocking_rule_id)
//        link/blocking_report.json  { rule_id, n_pairs, n_records_unblocked }
//   SIDE none
//
// ONE task for the whole run, not one per cohort. blocking_report.json is a
// single document in §3.1's own OUT slot, and its done-when reads it with
// `jq -e '.[] | .n_records_unblocked'` — one array whose entries are RULES.
// n_records_unblocked is the recall ceiling's shadow across the run, and a
// per-cohort split would give N reports none of which answers the question
// that field asks. bin/link_blocking.py keys pairs by cohort internally and
// never proposes one that crosses a cohort (§1.1: the JOIN happens inside a
// cohort, the UNION across them).
//
// outcome_variables is passed in from the pack, never inferred here: it is
// what makes §3.1's nogo ("never let a blocking rule reference the outcome
// variable") enforceable by the FLAG rather than by a column name
// (Global Constraint 1).
//
process LINK_BLOCKING {
    tag "link:block:${replicate == null ? 'baseline' : 'p' + replicate}"
    label 'process_single'

    conda "${moduleDir}/environment.yml"
    container "docker.io/bolt3x/clinarmonize-duckdb@sha256:056f3260afbddaf99bfbd881b25f318b24ead3103bc626e4401e4f5afa03a7e0"

    input:
    // The REPLICATE key travels with the tables, exactly as it does through
    // PROPOSE_CANDIDATES / PROPOSE_CHANNELS / PROPOSE_LEDGER. It is null for
    // the baseline -- the real run -- and the seed for a §10.1 replicate
    // (ADR-004 widened the harness to 'map', which needs link re-run per
    // permuted replicate). Without it the three link processes could only be
    // chained by channel POSITION, and a queue channel's order is task
    // completion order: replicate 7's pairs would be scored against
    // replicate 3's records the first time two tasks finished out of order.
    tuple val(replicate), val(tables_json)
    path  blocking_rules
    val   outcome_variables_json
    val   max_block_size
    val   max_pairs_warn_frac

    output:
    tuple val(replicate), path("link/candidate_pairs.parquet"), emit: pairs
    tuple val(replicate), path("link/blocking_report.json"),    emit: report
    path "versions.yml",                                        emit: versions

    script:
    """
    mkdir -p link
    link_blocking.py \\
        --tables '${tables_json}' \\
        --blocking-rules '${blocking_rules}' \\
        --outcome-variables '${outcome_variables_json}' \\
        --max-block-size ${max_block_size} \\
        --max-pairs-warn-frac ${max_pairs_warn_frac} \\
        --out-pairs link/candidate_pairs.parquet \\
        --out-report link/blocking_report.json

    cat <<-END_VERSIONS > versions.yml
    "${task.process}":
        python: \$(python3 --version | sed 's/Python //')
        duckdb: \$(python3 -c "import duckdb; print(duckdb.__version__)")
        pyyaml: \$(python3 -c "import yaml; print(yaml.__version__)")
    END_VERSIONS
    """
}
