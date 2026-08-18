//
// §3.3 — threshold into links, and emit the match-weight histogram.
//
// Contract (docs/steps/s3-3.md):
//   IN   link/scores.parquet
//   OUT  link/links.parquet        (person_id, cohort_id, [source_row_id])
//        link/match_histogram.png  both thresholds marked
//        link/link_report.json     { n_in, n_persons, n_clerical, collapse_ratio }
//   SIDE none
//
// The histogram is emitted UNCONDITIONALLY (§3.3 nogo): the run that most
// needs it is the run nobody thought to ask for it. It is drawn by hand in
// bin/link_resolve.py rather than by matplotlib — §0.8 pins the toolchain
// and this image is duckdb + PyYAML; adding a plotting library to draw one
// bar chart would mean rebuilding and re-pinning the image across every
// module copy, and a hand-written PNG is byte-deterministic where a
// rasterised figure is not.
//
// links.parquet's source_row_id is a LIST — the second nested type, and the
// same round-trip caveat LINK_SCORE's header states applies to it.
//
process LINK_RESOLVE {
    tag "link:resolve:${replicate == null ? 'baseline' : 'p' + replicate}"
    label 'process_single'

    conda "${moduleDir}/environment.yml"
    container "docker.io/bolt3x/clinarmonize-duckdb@sha256:056f3260afbddaf99bfbd881b25f318b24ead3103bc626e4401e4f5afa03a7e0"

    input:
    // [replicate, tables_json, scores] as ONE tuple -- see LINK_BLOCKING's
    // note on why the three link processes are chained by KEY, not position.
    tuple val(replicate), val(tables_json), path(scores)
    val   match_threshold
    val   clerical_threshold
    val   max_collapse_ratio_drop

    output:
    tuple val(replicate), path("link/links.parquet"),        emit: links
    tuple val(replicate), path("link/match_histogram.png"),  emit: histogram
    tuple val(replicate), path("link/link_report.json"),     emit: report
    tuple val(replicate), path("link/clerical_review.json"), emit: clerical
    path "versions.yml",                                     emit: versions

    script:
    """
    mkdir -p link
    link_resolve.py \\
        --scores '${scores}' \\
        --tables '${tables_json}' \\
        --match-threshold ${match_threshold} \\
        --clerical-threshold ${clerical_threshold} \\
        --max-collapse-ratio-drop ${max_collapse_ratio_drop} \\
        --out-links link/links.parquet \\
        --out-histogram link/match_histogram.png \\
        --out-report link/link_report.json \\
        --out-clerical link/clerical_review.json

    cat <<-END_VERSIONS > versions.yml
    "${task.process}":
        python: \$(python3 --version | sed 's/Python //')
        duckdb: \$(python3 -c "import duckdb; print(duckdb.__version__)")
    END_VERSIONS
    """
}
