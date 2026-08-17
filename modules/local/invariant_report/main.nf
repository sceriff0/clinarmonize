//
// §10.1 — the harness's verdict: tests/invariant/report.json.
//
// Contract (docs/steps/s10-1.md):
//   IN   every replicate's permutation manifest + every replicate's
//        sha256(ledger.proposed.yaml)
//   OUT  tests/invariant/report.json  {n_permutations, n_distinct_hashes}
//   SIDE none
//
// A real process rather than a Groovy operator for the same reason
// MANIFEST_PROFILING_FAILURES is one: whatever this reports has to surface
// through the normal per-process stdout/stderr a run and an nf-test failure
// both show, not through a dataflow thread nobody reads.
//
// This process always exits 0, including when the verdict is not `proof`.
// The gate is the nf-test assertion (§14.2: the harness runs in CI on every
// push), and a report that only exists when the news is good is a report
// you cannot debug a red build with — the failing run is exactly the one
// whose per-replicate hashes you need to read.
//
process INVARIANT_REPORT {
    label 'process_single'

    conda "${moduleDir}/environment.yml"
    container "docker.io/bolt3x/clinarmonize-duckdb:1.5.5_pyyaml6.0.2"

    input:
    path permutation_manifests, stageAs: 'permutations_in/*'
    val  ledger_hashes
    // The RESOLVED seed list, not the raw --permute_outcome_seed spec.
    // parseSeedSpec() in workflows/harmonize.nf is this pipeline's only
    // parser for the card's `int or range`; handing the spec over here
    // would mean a second one, and two parsers of one grammar are two
    // grammars as soon as either is edited.
    val  seeds
    val  n_required
    val  scope

    output:
    path "report.json",  emit: report
    path "versions.yml", emit: versions

    script:
    // "<replicate>=<sha256>", one per replicate that produced a ledger.
    // Empty until §4 exists, which is what makes the harness report
    // no-ledger rather than a vacuous agreement across zero hashes.
    def ledger_args = ledger_hashes.collect { "--ledger-hash '${it}'" }.join(' ')
    """
    invariant_report.py \\
        --permutation-glob 'permutations_in/*.json' \\
        --seeds '${seeds.join(',')}' \\
        --n-required ${n_required} \\
        --scope '${scope}' \\
        --out-report report.json \\
        ${ledger_args}

    cat <<-END_VERSIONS > versions.yml
    "${task.process}":
        python: \$(python3 --version | sed 's/Python //')
    END_VERSIONS
    """
}
