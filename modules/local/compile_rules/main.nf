//
// §5.2 — compile decisions into versioned, addressable mapping rules.
//
// Contract (docs/steps/s5-2.md):
//   IN   ch_confirmed's decisions.json (§5.1's output — this process's ONLY
//        run-level data input; rule_id_prefix/fail_on_rule_collision are
//        config, and the pack's own `version` string is threaded in as a
//        plain val, same reasoning as buildLedgerParamsJson & friends)
//   OUT  rules/ruleset.json  [ {rule_id, rule_version, kind, from, to, params} ]
//   SIDE none; rule_id is a content hash, so an unchanged rule keeps its id
//
process COMPILE_RULES {
    tag "confirm"
    label 'process_single'

    // Same image every other propose/confirm stage uses.
    conda "${moduleDir}/environment.yml"
    container "docker.io/bolt3x/clinarmonize-duckdb@sha256:056f3260afbddaf99bfbd881b25f318b24ead3103bc626e4401e4f5afa03a7e0"

    input:
    path decisions_json
    val  compile_params_json
    val  pack_version

    output:
    path "rules/ruleset.json", emit: ruleset
    path "versions.yml",       emit: versions

    script:
    // The card's own OUT slot carries the 'rules/' prefix (unlike
    // ledger.proposed.yaml's bare outdir-root path) — written into a real
    // 'rules/' subdirectory here so Nextflow's publishDir preserves it
    // verbatim; conf/modules.config points COMPILE_RULES's publishDir at
    // the bare outdir root (not "${outdir}/compile") for the same reason
    // PROPOSE_LEDGER's own override exists.
    """
    mkdir -p rules

    compile_rules.py \\
        --decisions '${decisions_json}' \\
        --compile-params '${compile_params_json}' \\
        --pack-version '${pack_version}' \\
        --out-ruleset rules/ruleset.json

    cat <<-END_VERSIONS > versions.yml
    "${task.process}":
        python: \$(python3 --version | sed 's/Python //')
    END_VERSIONS
    """
}
