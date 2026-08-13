/*
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    IMPORT MODULES / SUBWORKFLOWS / FUNCTIONS
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
*/
include { INGEST; resolvePackPath; sha256Hex } from '../subworkflows/local/ingest'
include { PROFILE_COLUMNS } from '../modules/local/profile_columns/main'
include { MANIFEST_PROFILING_FAILURES } from '../modules/local/manifest_profiling_failures/main'

/*
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    THE NINE-STAGE GRAPH (§0.9)

    Only 'ingest' (Phase 0, Task 2) and 'profile' (Phase 0, Task 3) are
    implemented in this phase. A run that tries to reach any later stage
    fails with a clear message instead of silently succeeding as if that
    stage had run.
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
*/
def stageGraph() {
    return ['ingest', 'profile', 'link', 'propose', 'confirm', 'map', 'derive', 'coverage', 'emit']
}

def implementedStages() {
    return ['ingest', 'profile'] as Set
}

def requireStageImplemented(String stage) {
    if (!(stage in implementedStages())) {
        error("Stage '${stage}' is not implemented in this phase (Phase 0, Task 3 implements 'ingest' and 'profile'). Re-run with --stop_after profile.")
    }
}

//
// The first stage in stageGraph() that implementedStages() does not cover,
// or null if the whole graph is implemented. Used to decide, BEFORE any
// process runs, whether --stop_after (or its default, the graph's last
// stage) asks the run to reach further than this phase goes.
//
def firstUnimplementedStage(List graph) {
    return graph.find { !(it in implementedStages()) }
}

/*
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    §1.2 — the holdout seal

    Contract (docs/steps/s1-2.md):
      ch_open = ch_datasets.filter { meta, _ ->
          !meta.holdout || meta.dataset_id in params.unseal
      }
    Trap: the filter has to happen here, at channel construction, upstream of
    any process that would stage the file into a work directory. A filter
    inside a process that *reads* the table is decoration -- by then the path
    is already on disk. resolveHoldoutLock() below runs BEFORE the filter is
    even built, so an unmatched --unseal refuses the whole run rather than
    silently dropping the offending id.
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
*/

//
// §1.2's "the run's params hash" -- what locked_model.json's params_hash is
// checked against, and (per §11.1's params_hash_file help_text) what that
// file records. Deliberately excludes: (a) the unsealing params themselves
// (unseal/locked_model/unseal_log/params_hash_file) -- including them would
// make the hash of a "lock this analysis" run differ from the hash of the
// later "check this analysis against its lock" run by construction, since
// the latter always sets --unseal and the former never does, so no lock
// could ever match; and (b) execution/output-location params (outdir,
// publish_dir_mode, monochrome_logs, validate_params, help*, version) that
// don't change what analysis is being run, only where/how it's reported.
//
def computeParamsHash(String input, String concept_pack, boolean allow_single_dataset) {
    def canonical = [
        "input=${input}",
        "concept_pack=${resolvePackPath(concept_pack)}",
        "allow_single_dataset=${allow_single_dataset}",
    ].join('\n')
    return sha256Hex(canonical.bytes)
}

//
// The git commit of the pipeline code actually about to run -- compared
// against locked_model.json's analysis_git_sha. Uses `git rev-parse` rather
// than workflow.commitId, which Nextflow only populates for a pulled/
// versioned pipeline (e.g. `nextflow run org/pipe -r v1.0`), not a plain
// local-directory run -- exactly how this pipeline is run in this phase.
//
def currentGitSha() {
    try {
        def proc = ['git', '-C', projectDir.toString(), 'rev-parse', 'HEAD'].execute()
        proc.waitFor()
        return proc.exitValue() == 0 ? proc.text.trim() : null
    }
    catch (Exception e) {
        return null
    }
}

//
// Validate --unseal against locked_model.json, INCLUDING the hash match the
// nogo demands ("Do not let --unseal proceed without a hash-matching lock").
// A lock that is never checked against the analysis about to run is the
// "log-only unsealing" alternative the card's Alternatives table explicitly
// rejects, wearing a lock's costume -- so existence + `covers` alone is not
// enough; params_hash and analysis_git_sha must match too.
//
// Returns the validated lock (with a `requested` key recording exactly which
// ids this run asked to unseal), or null if --unseal was not requested at
// all. HoldoutPolicy_admit() below is the only consumer of that return value.
//
def resolveHoldoutLock(List unsealList, String lockedModelPath, String unsealLogPath, String paramsHash, String gitSha) {
    if (!unsealList) {
        return null
    }

    def lockFile = file(lockedModelPath)
    if (!lockFile.exists()) {
        error("Refusing to start: --unseal ${unsealList.join(',')} was requested but no locked_model.json was found at '${lockedModelPath}'. No held-out dataset was admitted; '${unsealLogPath}' is unchanged.")
    }

    def lock
    try {
        lock = new groovy.json.JsonSlurper().parse(lockFile)
    }
    catch (Exception e) {
        error("Refusing to start: '${lockedModelPath}' is not valid JSON (${e.message}). No held-out dataset was admitted; '${unsealLogPath}' is unchanged.")
    }

    def covers = ((lock?.covers ?: []) as List)*.toString() as Set
    def uncovered = unsealList.findAll { !(it in covers) }
    if (uncovered) {
        error("Refusing to start: locked_model.json at '${lockedModelPath}' does not cover dataset(s) ${uncovered.join(',')} (it covers: ${covers ? covers.join(',') : '(none)'}). No held-out dataset was admitted; '${unsealLogPath}' is unchanged.")
    }

    if (lock.params_hash != paramsHash) {
        error("Refusing to start: locked_model.json at '${lockedModelPath}' does not hash-match this run (lock params_hash=${lock.params_hash}, this run=${paramsHash}). No held-out dataset was admitted; '${unsealLogPath}' is unchanged.")
    }
    if (gitSha != null && lock.analysis_git_sha != gitSha) {
        error("Refusing to start: locked_model.json at '${lockedModelPath}' does not hash-match this run (lock analysis_git_sha=${lock.analysis_git_sha}, this run=${gitSha}). No held-out dataset was admitted; '${unsealLogPath}' is unchanged.")
    }

    // Hash-matching lock, every requested id covered: append-only audit
    // trail, logged BEFORE any held-out row is admitted into ch_open below.
    def logFile = file(unsealLogPath)
    def ts = java.time.Instant.now().toString()
    unsealList.each { id ->
        logFile.append("${ts}\t${id}\t${lock.analysis_git_sha}\t${lock.params_hash}\n")
    }

    lock.requested = unsealList
    return lock
}

//
// alts seam (docs/steps/s1-2.md): HoldoutPolicy -- admit(Meta, Lock?) -> bool
//
// Today's only implementation: a boolean holdout flag plus an explicit,
// hash-matched per-dataset --unseal allow-list. The card's Alternatives table
// names the swap candidate: per-fold rotation (LODO), where the flag becomes
// a fold assignment and the lock covers a fold rather than a run -- that
// would replace only this function's body (and the shape of `lock`), not any
// caller.
//
def HoldoutPolicy_admit(Map meta, Map lock) {
    if (!meta.holdout) {
        return true
    }
    return lock != null && meta.dataset_id in (lock.requested ?: [])
}

/*
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    §2.1 / §2.2 / §2.3 — the profiler

    Contract (docs/steps/s2-1.md, s2-2.md, s2-3.md): PROFILE_COLUMNS
    (modules/local/profile_columns/main.nf) is the per-dataset half —
    everything that is a pure function of one table's bytes. The
    run-level half lives here: collating every dataset's failure manifest
    into the single profiles/_failed.json the §2.3 contract names, and
    enforcing its SIDE clause -- "exit non-zero when the failure RATE
    exceeds --max_failed_frac" -- which is a property of the WHOLE run,
    not of any one dataset.
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
*/

//
// Serialises the profiling knobs PROFILE_COLUMNS needs into one JSON blob
// passed as a single `val` input, rather than threading eight separate
// process inputs (several of them lists) through Groovy-to-bash
// interpolation.
//
def buildProfileParamsJson(
    Integer maxUniqueListed,
    List dateFormats,
    Integer exampleK,
    List naStrings,
    String ucumRelease,
    Boolean inferUnitFromRange,
    Integer failSampleK
) {
    return groovy.json.JsonOutput.toJson([
        max_unique_listed    : maxUniqueListed,
        date_formats         : dateFormats,
        example_k            : exampleK,
        na_strings           : naStrings,
        ucum_release         : ucumRelease,
        infer_unit_from_range: inferUnitFromRange,
        fail_sample_k        : failSampleK,
    ])
}

/*
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    PROCESSES
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
*/

//
// The first (and, in this phase, only) process to ever touch a dataset's
// path: it stages ch_open into a work directory and writes a small
// per-dataset manifest. It exists so the seal is verifiable as a DAG
// property, not just a channel-content assertion: only rows that survive
// the filter above are ever staged here, so a held-out row that leaks
// through would show up in .nextflow.log as a submitted task.
//
process STAGE_OPEN_DATASET {
    tag "${meta.cohort_id}:${meta.dataset_id}"
    label 'process_single'

    container "docker.io/library/ubuntu@sha256:3b06811b2afd352be909dd088a004166d665dc76d38b13eada33522a9d915c6f"

    input:
    tuple val(meta), path(table)

    output:
    tuple val(meta), path("${meta.cohort_id}__${meta.dataset_id}.manifest.json"), emit: manifest
    path "versions.yml", emit: versions

    script:
    """
    cat <<-END_MANIFEST > ${meta.cohort_id}__${meta.dataset_id}.manifest.json
    {"cohort_id":"${meta.cohort_id}","dataset_id":"${meta.dataset_id}","role":"${meta.role}","holdout":${meta.holdout},"table":"${table.name}"}
    END_MANIFEST

    cat <<-END_VERSIONS > versions.yml
    "${task.process}":
        coreutils: \$(cat --version | head -n1 | sed -n 's/.*) //p')
    END_VERSIONS
    """
}

/*
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    RUN MAIN WORKFLOW
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
*/

workflow CLINICALHARMONIZE {

    take:
    input                  // string:  path to the two-level samplesheet (--input)
    concept_pack           // string:  path or URL to the concept pack (--concept_pack)
    stop_after             // string?: stage after which the pipeline halts
    allow_single_dataset   // boolean: permits a cohort with one dataset (§3, not yet built)
    unseal                 // string:  comma-separated dataset id(s) admitted despite being held out (--unseal)
    locked_model           // string:  path to locked_model.json, checked against --unseal
    unseal_log             // string:  append-only audit trail for admitted holdouts
    params_hash_file       // string:  path this run's params hash is recorded to
    max_unique_listed      // int:     caps the stored unique-value set per column (§2.1)
    date_formats           // list:    strftime formats that parse as a date (§2.1)
    example_k              // int:     example values carried into the ledger (§2.1)
    na_strings             // list:    values counted as missing (§2.1)
    unit_header_patterns   // string:  path to the header unit-regex patterns (§2.2)
    ucum_release           // string:  pinned UCUM grammar version, recorded only (§2.2)
    infer_unit_from_range  // boolean: add a low-confidence unit candidate from the value range (§2.2)
    max_failed_frac        // number:  failure-rate threshold above which the run refuses (§2.3)
    fail_sample_k          // int:     offending values carried into the failure manifest (§2.3)
    outdir                 // string:  output directory

    main:
    def ch_versions = channel.empty()

    // --unseal is a comma-separated string on the CLI (nf-schema does not
    // cast a bare CLI value into a list-typed param); parse it once here.
    def unsealList = (unseal ?: '').split(',')*.trim().findAll { it }

    //
    // §1.1 — validate + parse the samplesheet and the concept pack. Must run
    // BEFORE params_hash_file is written below: §1.1's SIDE clause is "fails
    // fast on duplicate (cohort_id, dataset_id); writes no files", and a
    // duplicate sheet has to fail inside INGEST() before this workflow
    // writes anything at all.
    //
    INGEST(input, concept_pack)

    // This run's identity: what locked_model.json's params_hash and
    // analysis_git_sha are checked against (§1.2 nogo: "hash-matching
    // lock"). Recorded to params_hash_file regardless of whether --unseal is
    // in play, per §11.1's params_hash_file help_text.
    def paramsHash = computeParamsHash(input, concept_pack, allow_single_dataset)
    def gitSha = currentGitSha()
    def paramsHashFile = file(params_hash_file)
    paramsHashFile.parent?.mkdirs()
    paramsHashFile.text = paramsHash

    //
    // §1.2 — the holdout seal, applied at channel construction
    //
    def lock = resolveHoldoutLock(unsealList, locked_model, unseal_log, paramsHash, gitSha)

    def ch_open = INGEST.out.datasets.filter { meta, _table -> HoldoutPolicy_admit(meta, lock) }

    //
    // Stage gate. Everything past the last implemented stage fails loudly
    // in this phase rather than silently succeeding, and fails before any
    // further work (including staging) happens.
    //
    def graph = stageGraph()
    def targetStage = stop_after ?: graph.last()
    if (!(targetStage in graph)) {
        error("Unknown stage '${targetStage}' for --stop_after.")
    }
    def firstMissingStage = firstUnimplementedStage(graph)
    if (firstMissingStage != null && graph.indexOf(targetStage) >= graph.indexOf(firstMissingStage)) {
        requireStageImplemented(firstMissingStage)
    }

    //
    // Stage the admitted datasets. This is the only place a table's bytes
    // are staged for the 'ingest' manifest in this phase; it only ever sees
    // ch_open.
    //
    STAGE_OPEN_DATASET(ch_open)
    ch_versions = ch_versions.mix(STAGE_OPEN_DATASET.out.versions)

    STAGE_OPEN_DATASET.out.manifest
        .map { meta, manifest -> manifest }
        .collectFile(name: 'ingest_manifest.json', storeDir: "${outdir}/ingest", newLine: true, sort: { it.name })

    //
    // §2.1 / §2.2 / §2.3 — profile every admitted dataset. Consumes ch_open
    // directly: it is already the sealed, holdout-filtered channel (§1.2),
    // and PROFILE_COLUMNS is the second (after STAGE_OPEN_DATASET) and last
    // process in this phase to touch a table's bytes, so the seal must never
    // be re-derived or re-checked here.
    //
    if (graph.indexOf(targetStage) >= graph.indexOf('profile')) {
        def unitPatternsFile = file(resolvePackPath(unit_header_patterns))
        def profileParamsJson = buildProfileParamsJson(
            max_unique_listed,
            date_formats,
            example_k,
            na_strings,
            ucum_release,
            infer_unit_from_range,
            fail_sample_k,
        )

        PROFILE_COLUMNS(ch_open, unitPatternsFile, profileParamsJson)
        ch_versions = ch_versions.mix(PROFILE_COLUMNS.out.versions)

        // The run-level half of the §2.3 FailurePolicy seam: collates every
        // dataset's failure manifest into profiles/_failed.json and refuses
        // the run once the aggregate failure rate exceeds max_failed_frac.
        // Needs every dataset's PROFILE_COLUMNS output collected first — a
        // single dataset's failure rate is not the run's failure rate.
        MANIFEST_PROFILING_FAILURES(
            PROFILE_COLUMNS.out.profile.map { m, f -> f }.collect(),
            PROFILE_COLUMNS.out.failed.map { m, f -> f }.collect(),
            max_failed_frac,
        )
        ch_versions = ch_versions.mix(MANIFEST_PROFILING_FAILURES.out.versions)
    }

    emit:
    datasets = ch_open           // channel: [ meta(cohort_id, dataset_id, role, holdout), path(table) ]
    pack     = INGEST.out.pack   // channel: [ pack_hash, [variable, ...] ]
    versions = ch_versions       // channel: [ path(versions.yml) ]
}

/*
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    THE END
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
*/
