//
// §5.1 — accept a confirmed ledger and refuse to guess when it is absent.
//
// Contract (docs/steps/s5-1.md):
//   IN   --confirmed_ledger ledger.confirmed.yaml (required to pass §5)
//        ledger.proposed.yaml (for the staleness check)
//   OUT  decisions.json — the validated, normalised confirmed rows (§5.2's
//        own IN); ch_confirmed itself is assembled one step further on, in
//        workflows/harmonize.nf, once §5.2 has attached each row's rule_id
//   SIDE exits non-zero, with the diff, when the confirmed ledger is stale
//
// The absence of --confirmed_ledger is NOT this process's job to detect:
// workflows/harmonize.nf refuses (error(), printing the path to write)
// BEFORE this process is ever invoked. That is a run-shape decision ("do
// not start"), not a per-row parsing one, and keeping it in Groovy is what
// makes the auto-confirm boundary (this card's own risk) explicit and
// impossible to trip by accident: there is no params.* flag anywhere that
// can make this process run without a real, human-authored file on disk,
// under ANY profile, -profile test included.
//
process CONFIRM_LEDGER {
    tag "confirm"
    label 'process_single'

    // Same image every other propose/confirm stage uses
    // (python3, duckdb 1.5.5, pyyaml 6.0.2 baked in). duckdb is unused here
    // (this script only ever hashes bytes and parses two small YAML files)
    // -- reused anyway to keep the pipeline's trusted-image count at one.
    conda "${moduleDir}/environment.yml"
    container "docker.io/bolt3x/clinarmonize-duckdb:1.5.5_pyyaml6.0.2"

    input:
    path confirmed_ledger_file
    path proposed_ledger_file
    val  confirm_params_json

    output:
    path "decisions.json", emit: decisions
    path "versions.yml",   emit: versions

    script:
    """
    confirm_ledger.py \\
        --confirmed '${confirmed_ledger_file}' \\
        --proposed '${proposed_ledger_file}' \\
        --confirm-params '${confirm_params_json}' \\
        --out-decisions decisions.json

    cat <<-END_VERSIONS > versions.yml
    "${task.process}":
        python: \$(python3 --version | sed 's/Python //')
        pyyaml: \$(python3 -c "import yaml; print(yaml.__version__)")
    END_VERSIONS
    """
}
