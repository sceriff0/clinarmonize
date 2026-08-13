/*
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    IMPORT MODULES / SUBWORKFLOWS / FUNCTIONS
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
*/
include { INGEST } from '../subworkflows/local/ingest'

/*
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    THE NINE-STAGE GRAPH (§0.9)

    Only 'ingest' is implemented in this phase (Phase 0, Task 2). A run that
    tries to reach any later stage fails with a clear message instead of
    silently succeeding as if that stage had run.
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
*/
def stageGraph() {
    return ['ingest', 'profile', 'link', 'propose', 'confirm', 'map', 'derive', 'coverage', 'emit']
}

def implementedStages() {
    return ['ingest'] as Set
}

def requireStageImplemented(String stage) {
    if (!(stage in implementedStages())) {
        error("Stage '${stage}' is not implemented in this phase (Phase 0, Task 2 implements only 'ingest'). Re-run with --stop_after ingest.")
    }
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
    is already on disk. checkUnsealLock() below runs BEFORE the filter is
    even built, so an unmatched --unseal refuses the whole run rather than
    silently dropping the offending id.
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
*/
def checkUnsealLock(List unsealList, String lockedModelPath, String unsealLogPath) {
    if (!unsealList) {
        return
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

    // All requested ids are covered by the lock: append-only audit trail,
    // hashed/logged BEFORE any held-out row is admitted into ch_open below.
    def logFile = file(unsealLogPath)
    def ts = java.time.Instant.now().toString()
    def gitSha = lock.analysis_git_sha ?: 'unknown'
    def paramsHash = lock.params_hash ?: 'unknown'
    unsealList.each { id ->
        logFile.append("${ts}\t${id}\t${gitSha}\t${paramsHash}\n")
    }
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
    input         // string:  path to the two-level samplesheet (--input)
    concept_pack  // string:  path or URL to the concept pack (--concept_pack)
    stop_after    // string?: stage after which the pipeline halts
    unseal        // string:  comma-separated dataset id(s) admitted despite being held out (--unseal)
    locked_model  // string:  path to locked_model.json, checked against --unseal
    unseal_log    // string:  append-only audit trail for admitted holdouts
    outdir        // string:  output directory

    main:
    def ch_versions = channel.empty()

    // --unseal is a comma-separated string on the CLI (nf-schema does not
    // cast a bare CLI value into a list-typed param); parse it once here.
    def unsealList = (unseal ?: '').split(',')*.trim().findAll { it }

    //
    // §1.1 — validate + parse the samplesheet and the concept pack
    //
    INGEST(input, concept_pack)

    //
    // §1.2 — the holdout seal, applied at channel construction
    //
    checkUnsealLock(unsealList, locked_model, unseal_log)

    def ch_open = INGEST.out.datasets.filter { meta, _table ->
        !meta.holdout || meta.dataset_id in unsealList
    }

    //
    // Stage gate. Everything past 'ingest' fails loudly in this phase
    // rather than silently succeeding, and fails before any further work
    // (including staging) happens.
    //
    def graph = stageGraph()
    def targetStage = stop_after ?: graph.last()
    if (!(targetStage in graph)) {
        error("Unknown stage '${targetStage}' for --stop_after.")
    }
    if (targetStage != 'ingest') {
        requireStageImplemented(graph[graph.indexOf('ingest') + 1])
    }

    //
    // Stage the admitted datasets. This is the only place a table's bytes
    // are ever staged in this phase; it only ever sees ch_open.
    //
    STAGE_OPEN_DATASET(ch_open)
    ch_versions = ch_versions.mix(STAGE_OPEN_DATASET.out.versions)

    STAGE_OPEN_DATASET.out.manifest
        .map { meta, manifest -> manifest }
        .collectFile(name: 'ingest_manifest.json', storeDir: "${outdir}/ingest", newLine: true, sort: { it.name })

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
