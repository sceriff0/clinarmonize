//
// §10.1 / ADR-004 — reduce one replicate's published artefact to a single
// comparable digest.
//
// Contract (ADR-004, "What is hashed"):
//   IN   every parquet file of one replicate's mapped/ (including
//        _unmapped.parquet) + the float precision §4.3 rounds at
//   OUT  <prefix>.json  {artefact, digest, tables: [{table, n_rows, columns,
//        digest}]}
//   SIDE none
//
// Why a process and not a Groovy sha256Hex() the way the propose scope
// hashes its ledgers. A ledger is a YAML file whose bytes ARE its content,
// so hashing it in the workflow costs nothing and stages nothing. mapped/ is
// parquet: its bytes carry writer metadata, compression choices and
// row-group boundaries, none of which are the data, and reading it back
// canonically needs duckdb -- which lives in the container, not in the
// Nextflow JVM. ADR-004 is explicit that a byte digest here would make the
// harness sensitive to the encoder rather than to the data.
//
// One invocation for BOTH the baseline and every permuted replicate, keyed
// by `replicate` exactly as PROPOSE_CANDIDATES / PROPOSE_CHANNELS /
// PROPOSE_LEDGER are -- so there is no aliased second call site here, and no
// way for the baseline's digest to be computed by different code from the
// replicates it is compared against. (The link and map stages DO need
// aliases, because their baseline invocation is a real pipeline stage that
// publishes results; this one exists only for the harness.)
//
process ARTEFACT_DIGEST {
    tag "digest:${replicate == null ? 'baseline' : 'p' + replicate}"
    label 'process_single'

    conda "${moduleDir}/environment.yml"
    container "docker.io/bolt3x/clinarmonize-duckdb@sha256:056f3260afbddaf99bfbd881b25f318b24ead3103bc626e4401e4f5afa03a7e0"

    input:
    tuple val(replicate), path(artefact_files, stageAs: 'artefact_in/*')
    val   artefact_name
    val   float_precision

    output:
    tuple val(replicate), path("${prefix}.json"), emit: digest
    path "versions.yml",                          emit: versions

    script:
    // Same naming rule as PROPOSE_CANDIDATES': the baseline replicate is
    // null and gets the bare name, a harness replicate carries its seed, so
    // N replicates never collide on one filename.
    prefix = replicate == null ? 'digest' : "digest.p${replicate}"
    """
    artefact_digest.py \\
        --glob 'artefact_in/*.parquet' \\
        --artefact '${artefact_name}' \\
        --float-precision ${float_precision} \\
        --out-digest '${prefix}.json'

    cat <<-END_VERSIONS > versions.yml
    "${task.process}":
        python: \$(python3 --version | sed 's/Python //')
        duckdb: \$(python3 -c "import duckdb; print(duckdb.__version__)")
    END_VERSIONS
    """
}
