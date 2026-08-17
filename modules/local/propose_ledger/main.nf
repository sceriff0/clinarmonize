//
// §4.3 — write the ranked, deterministic proposal ledger.
//
// Contract (docs/steps/s4-3.md):
//   IN   propose/evidence.parquet (+ propose/candidates.parquet, for
//        excluded_candidates -- Task 5b's own contract note)
//   OUT  ledger.proposed.yaml -- ordered, stable, hashable
//   SIDE none; identical inputs MUST yield an identical sha256
//
// One task per REPLICATE, exactly like PROPOSE_CANDIDATES / PROPOSE_CHANNELS:
// `candidates_parquet` and `evidence_parquet` are that SAME replicate's
// outputs (workflows/harmonize.nf joins PROPOSE_CANDIDATES.out.candidates
// with PROPOSE_CHANNELS.out.evidence on `replicate` before calling this
// process). This is the process whose per-replicate output channel becomes
// §10.1's `ch_ledgers` -- Task 4's placeholder `channel.empty()` line.
//
// R13 -- two publication paths for this ONE process's output:
//   - every replicate's ledger.p<seed>.yaml feeds ch_ledgers, which the
//     §10.1 harness hashes (Groovy-side, from ledger.bytes) and compares
//     across all 100+1 replicates.
//   - the baseline replicate's (replicate == null) file is ALSO published,
//     unsuppressed, to the literal outdir-root path the card's I/O slot
//     names -- results/ledger.proposed.yaml -- via conf/modules.config's
//     PROPOSE_LEDGER override (path: params.outdir, not the default
//     "${outdir}/propose" every other propose/* process publishes under;
//     this card's own OUT slot is 'ledger.proposed.yaml' with no 'propose/'
//     prefix, unlike s4-1/s4-2's).
//
process PROPOSE_LEDGER {
    tag "replicate:${replicate ?: 'baseline'}"
    label 'process_single'

    // Same image PROFILE_COLUMNS / PROPOSE_CANDIDATES /
    // PROPOSE_CHANNELS use (python3, duckdb 1.5.5, pyyaml 6.0.2 baked in) --
    // this process only needs python3 + duckdb + pyyaml, all three already
    // baked in, so reusing it keeps the pipeline's trusted-image count at one.
    conda "${moduleDir}/environment.yml"
    container "docker.io/bolt3x/clinarmonize-duckdb@sha256:056f3260afbddaf99bfbd881b25f318b24ead3103bc626e4401e4f5afa03a7e0"

    input:
    tuple val(replicate), path(candidates_parquet), path(evidence_parquet)
    val   ledger_params_json

    output:
    tuple val(replicate), path("${prefix}.yaml"), emit: ledger
    path "versions.yml",                          emit: versions

    script:
    // Same baseline/replicate naming convention as PROPOSE_CANDIDATES /
    // PROPOSE_CHANNELS: the baseline replicate (null) gets the literal file
    // name the card's OUT slot names; a harness replicate's file carries its
    // seed so 100 replicates never collide on one filename inside one task
    // stream. The bytes §10.1 hashes are read directly (sha256Hex(ledger.bytes)
    // in workflows/harmonize.nf), so the filename itself is never hashed and
    // this naming choice cannot affect the invariant's verdict.
    prefix = replicate == null ? 'ledger.proposed' : "ledger.proposed.p${replicate}"
    """
    propose_ledger.py \\
        --candidates '${candidates_parquet}' \\
        --evidence '${evidence_parquet}' \\
        --ledger-params '${ledger_params_json}' \\
        --out-ledger '${prefix}.yaml'

    cat <<-END_VERSIONS > versions.yml
    "${task.process}":
        python: \$(python3 --version | sed 's/Python //')
        duckdb: \$(python3 -c "import duckdb; print(duckdb.__version__)")
        pyyaml: \$(python3 -c "import yaml; print(yaml.__version__)")
    END_VERSIONS
    """
}
