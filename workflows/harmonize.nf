/*
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    IMPORT MODULES / SUBWORKFLOWS / FUNCTIONS
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
*/
include { INGEST; resolvePackPath; sha256Hex } from '../subworkflows/local/ingest'
include { PROFILE_COLUMNS } from '../modules/local/profile_columns/main'
include { MANIFEST_PROFILING_FAILURES } from '../modules/local/manifest_profiling_failures/main'
include { PERMUTE_OUTCOME } from '../modules/local/permute_outcome/main'
include { INVARIANT_REPORT } from '../modules/local/invariant_report/main'
//
// §10.1 — the permuted replicates are profiled by the SAME process as the
// baseline, under an alias. An alias rather than a second module because
// the harness only means anything if the permuted tables travel the
// identical code path the real run uses; a purpose-built "profile for the
// test" module is a path the invariant would not actually be tested on.
// DSL2 permits one invocation per process name, and the baseline run still
// needs its own, so the alias is what buys the second call site.
//
include { PROFILE_COLUMNS as PROFILE_COLUMNS_PERMUTED } from '../modules/local/profile_columns/main'
include { PROPOSE_CANDIDATES } from '../modules/local/propose_candidates/main'
include { PROPOSE_CHANNELS } from '../modules/local/propose_channels/main'
include { PROPOSE_LEDGER } from '../modules/local/propose_ledger/main'
include { LINK_BLOCKING } from '../modules/local/link_blocking/main'
include { LINK_SCORE } from '../modules/local/link_score/main'
include { LINK_RESOLVE } from '../modules/local/link_resolve/main'
include { CONFIRM_LEDGER } from '../modules/local/confirm_ledger/main'
include { COMPILE_RULES } from '../modules/local/compile_rules/main'

/*
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    THE NINE-STAGE GRAPH (§0.9)

    'ingest', 'profile', 'link', 'propose' and 'confirm' are implemented. A
    run that tries to reach any other stage fails with a clear message
    instead of silently succeeding as if that stage had run.

    implementedStages() was NOT a contiguous prefix of stageGraph() for the
    whole of phase 0: 'link' (§3) was unbuilt while 'propose' (§4), which
    sits AFTER it in the canonical order, was -- because §0.7 puts link at
    build-order item 7, after propose and confirm, "so the invariant work is
    not blocked behind entity resolution", and §0.9 confirms the dependency
    direction (§2 feeds §4, while §3 feeds §6). That is why the gate below
    checks the REQUESTED stage's own membership rather than walking the
    graph for the first gap.

    §3 is now built, so the set happens to be contiguous again -- and the
    mechanism stays exactly as it was. It is not an accident to be tidied
    away: §6-§9 are unbuilt, and the identical situation recurs the moment
    any later stage is built out of graph order. Do not "fix" this into a
    prefix walk.
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
*/
def stageGraph() {
    return ['ingest', 'profile', 'link', 'propose', 'confirm', 'map', 'derive', 'coverage', 'emit']
}

def implementedStages() {
    return ['ingest', 'profile', 'link', 'propose', 'confirm'] as Set
}

//
// §3.1's done-when stops after BLOCKING (`--stop_after block`) while
// §3.3's stops after the whole of link (`--stop_after link`). 'block' is
// therefore a stop point the NINE-stage graph of §0.9 does not have and
// must not gain: stageGraph() is the spec's own stage list, and adding a
// tenth entry to it would change what every `graph.indexOf(...)` comparison
// in this workflow means.
//
// It is modelled as a SUB-STAGE of link instead: it occupies link's
// position in the graph for every ordering question, and additionally halts
// the link block after §3.1. One map, one lookup, and stageGraph() is
// untouched.
//
def subStages() {
    return ['block': 'link']
}

def resolveGraphStage(String stage) {
    return subStages()[stage] ?: stage
}

def requireStageImplemented(String stage) {
    if (!(resolveGraphStage(stage) in implementedStages())) {
        error("Stage '${stage}' is not implemented in this phase (implemented: ${implementedStages().sort().join(', ')}). Re-run with --stop_after set to one of those.")
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
// §4.1 -- the pack's declared variable set and its pinned vocabulary
// release (Ruling R14), read directly rather than through INGEST.out.pack:
// that channel carries [pack_hash, pack.variables] only (§1.1's OUT slot,
// an interface Task 5a was told is authoritative and not to redesign),
// which does not carry the raw `vocabulary` key PROPOSE_CANDIDATES needs to
// record. INGEST already validated the pack's structure once; this is a
// second read of the same small YAML file, not a second source of truth
// for it. There is no ATHENA release in this repo and no downloader pinned
// by §0.8, so `vocabulary` is read and recorded (PROPOSE_CANDIDATES writes
// it into versions.yml) -- never resolved.
//
def loadPackForPropose(String packPath) {
    def packFile = file(resolvePackPath(packPath), checkIfExists: true)
    def pack = new org.yaml.snakeyaml.Yaml().load(packFile.text)
    return [pack.variables, pack.vocabulary]
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
    §4.1 — candidate generation

    Contract (docs/steps/s4-1.md):
      IN   profiles/*.json + the concept pack + the pinned ATHENA release
      OUT  propose/candidates.parquet (cohort_id, dataset_id, column,
                                        variable, concept_id, generator_id)
      SIDE none; a column with zero candidates is emitted with a null concept_id
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
*/

//
// Serialises the recall-ceiling knobs PROPOSE_CANDIDATES needs into one
// JSON blob, same reasoning as buildProfileParamsJson above.
//
def buildProposeParamsJson(Integer maxCandidatesPerColumn, List candidateGenerators) {
    return groovy.json.JsonOutput.toJson([
        max_candidates_per_column: maxCandidatesPerColumn,
        candidate_generators     : candidateGenerators,
    ])
}

/*
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    §4.2 — six independent evidence channels

    Contract (docs/steps/s4-2.md):
      IN   propose/candidates.parquet + profiles/*.json + pack + vocabulary
      OUT  propose/evidence.parquet (candidate_key, channel, score, detail)
           propose/confirmation_plots/<candidate_key>.svg (Ruling R16)
      SIDE none — a pure function; this purity is what §10.1 tests
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
*/

//
// Serialises the six-channel knobs PROPOSE_CHANNELS needs into one JSON
// blob, same reasoning as buildProfileParamsJson/buildProposeParamsJson
// above.
//
def buildChannelParamsJson(Map channelWeights, List enabledChannels, List unitFactorCandidates, Boolean emitConfirmationPlots) {
    return groovy.json.JsonOutput.toJson([
        channel_weights        : channelWeights,
        enabled_channels       : enabledChannels,
        unit_factor_candidates : unitFactorCandidates,
        emit_confirmation_plots: emitConfirmationPlots,
    ])
}

/*
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    §4.3 — the deterministic proposal ledger

    Contract (docs/steps/s4-3.md):
      IN   propose/evidence.parquet (+ propose/candidates.parquet, for
           excluded_candidates -- Task 5b's own contract note)
      OUT  ledger.proposed.yaml -- ordered, stable, hashable
      SIDE none; identical inputs MUST yield an identical sha256
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
*/

//
// Serialises the ledger-writer's two knobs into one JSON blob, same
// reasoning as buildProfileParamsJson/buildProposeParamsJson/
// buildChannelParamsJson above.
//
def buildLedgerParamsJson(Integer ledgerTopK, Integer ledgerFloatPrecision) {
    return groovy.json.JsonOutput.toJson([
        ledger_top_k          : ledgerTopK,
        ledger_float_precision: ledgerFloatPrecision,
    ])
}

/*
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    §5.1 / §5.2 — the human gate, and the rule compiler behind it

    Contract (docs/steps/s5-1.md):
      IN   --confirmed_ledger ledger.confirmed.yaml (required to pass §5)
           ledger.proposed.yaml (for the staleness check)
      OUT  ch_confirmed : [ cohort_id, dataset_id, column, variable, concept_id, rule_id ]
      SIDE exits non-zero, with the diff, when the confirmed ledger is stale

    Contract (docs/steps/s5-2.md):
      IN   ch_confirmed
      OUT  rules/ruleset.json  [ {rule_id, rule_version, kind, from, to, params} ]
      SIDE none; rule_id is a content hash, so an unchanged rule keeps its id

    ch_confirmed (this workflow's own emitted channel, matching s5-1's OUT
    slot literally) is assembled AFTER §5.2 has run, not before: rule_id is
    §5.2's own output field, never re-derived here or duplicated between the
    two cards. CONFIRM_LEDGER (§5.1) writes decisions.json; COMPILE_RULES
    (§5.2) reads it and writes rules/ruleset.json; this workflow reads THAT
    file back (Groovy JsonSlurper, the same pattern resolveHoldoutLock uses
    for locked_model.json) and re-shapes each compiled rule into the tuple
    s5-1's OUT slot names.
*/

//
// Serialises §5.1's two validation knobs into one JSON blob, same reasoning
// as buildProfileParamsJson & co. above.
//
def buildConfirmParamsJson(Boolean requireRationale, Boolean allowStaleLedger) {
    return groovy.json.JsonOutput.toJson([
        require_rationale : requireRationale,
        allow_stale_ledger: allowStaleLedger,
    ])
}

//
// Serialises §5.2's two knobs into one JSON blob.
//
def buildCompileParamsJson(String ruleIdPrefix, Boolean failOnRuleCollision) {
    return groovy.json.JsonOutput.toJson([
        rule_id_prefix        : ruleIdPrefix,
        fail_on_rule_collision: failOnRuleCollision,
    ])
}

/*
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    §10.1 — the outcome-permutation harness

    Contract (docs/steps/s10-1.md):
      for seed in 1..100:
          permute outcome column WITHIN each cohort
          run  --stop_after propose
          collect sha256(ledger.proposed.yaml)
      assert len(set(hashes)) == 1

    The loop runs INSIDE one Nextflow execution rather than as a shell loop
    around N of them: §14.2 requires this test on every push at 100
    permutations, and 100 JVM startups is the difference between a test that
    runs in CI and a test that gets moved to a nightly and then to nobody.
    One run, N replicate branches, same arithmetic.
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
*/

//
// --permute_outcome_seed is the card's "int or range": `7` arrives from the
// CLI as an Integer and `1..100` as a String, so both are normalised here
// into the one thing the rest of the harness wants -- the explicit list of
// seeds to run. Unset (the default) yields an empty list, which is what
// makes a normal run permute nothing at all.
//
def parseSeedSpec(seedSpec) {
    if (seedSpec == null || seedSpec.toString().trim() == '') {
        return []
    }
    def spec = seedSpec.toString().trim()
    def matcher = (spec =~ /^(\d+)(?:\.\.(\d+))?$/)
    if (!matcher.matches()) {
        error("--permute_outcome_seed must be an integer (e.g. 7) or a range (e.g. 1..100); got '${spec}'.")
    }
    def low = matcher[0][1] as int
    def high = matcher[0][2] != null ? matcher[0][2] as int : low
    if (high < low) {
        error("--permute_outcome_seed range '${spec}' ends before it starts.")
    }
    return (low..high) as List
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
    blocking_rules         // string:  path to the §3.1 blocking rule list
    max_block_size         // int:     a block larger than this is reported and skipped (§3.1)
    max_pairs_warn_frac    // number:  warns when pairs exceed this fraction of the cross product (§3.1)
    link_em_iterations     // int:     EM passes estimating m (§3.2)
    link_comparison_spec   // string:  path to the per-field comparison spec, missing level included (§3.2)
    link_u_from_random_pairs // boolean: estimate u by sampling rather than assuming it (§3.2)
    link_random_pair_n     // int:     sample size for the u estimate (§3.2)
    match_threshold        // number:  match weight above which two records are one person (§3.3)
    clerical_threshold     // number:  lower edge of the review band; reported, never auto-linked (§3.3)
    max_collapse_ratio_drop // number: fails the run when linkage collapses more than this share (§3.3)
    permute_outcome_seed   // int|str: test-only seed or seed range for the §10.1 harness
    invariant_n_permutations // int:   permutations required before a run counts as a proof (§10.1)
    invariant_scope        // string:  stage the invariant claim is held over (§10.1, ADR-003)
    max_candidates_per_column // int:  the recall ceiling on distinct variables proposed per column (§4.1)
    candidate_generators    // list:   which cheap generators propose candidates at all (§4.1)
    channel_weights         // map:    per-channel weight in a candidate's combined score (§4.2)
    enabled_channels        // list:   which of the six evidence channels score candidates at all (§4.2)
    unit_factor_candidates  // list:   the conversions unit_plausibility will try (§4.2)
    emit_confirmation_plots // boolean: emit the one plot that would kill each proposal (§4.2, Ruling R16: SVG)
    ledger_top_k            // int:     proposals written per column; the rest are counted, not listed (§4.3)
    ledger_float_precision  // int:     rounding, so a float's last bit cannot change the ledger's hash (§4.3)
    confirmed_ledger        // string?: path to ledger.confirmed.yaml -- the human gate; null stops the run (§5.1)
    require_rationale       // boolean: an accept with no rationale is rejected at parse time (§5.1)
    allow_stale_ledger      // boolean: escape hatch for a stale proposed_hash, logged loudly (§5.1)
    rule_id_prefix          // string:  cosmetic prefix; stability comes from the content hash (§5.2)
    fail_on_rule_collision  // boolean: two rules writing the same target cell is a defect, not a merge (§5.2)
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
    if (!(targetStage in graph) && !(targetStage in subStages().keySet())) {
        error("Unknown stage '${targetStage}' for --stop_after.")
    }
    // Every ORDERING question below asks where the run stops in the
    // nine-stage graph, and a sub-stage answers with its parent's position:
    // `--stop_after block` reaches exactly as far as `--stop_after link`
    // does, and stops partway through it. Resolved once here so no
    // comparison further down has to remember that 'block' is not in the
    // graph -- graph.indexOf('block') is -1, which would silently read as
    // "before ingest" and skip every stage.
    def graphStage = resolveGraphStage(targetStage)
    //
    // §10.1 — the invariant harness is the one caller allowed past the gate.
    //
    // The gate exists so a run cannot silently succeed as though an unbuilt
    // stage had run. But the harness's measurement IS which contract output
    // the unbuilt stages failed to produce, so refusing it here would
    // replace the finding with the refusal: the test would fail because the
    // gate said no, not because ledger.proposed.yaml was never written, and
    // those are different facts about the pipeline. Nothing becomes silent
    // by allowing it -- INVARIANT_REPORT names every replicate that
    // produced no ledger and withholds the verdict 'proof'.
    //
    def seeds = parseSeedSpec(permute_outcome_seed)
    def isInvariantRun = !seeds.isEmpty()

    // The exemption is bound to the ONE stage the harness is declared to
    // hold its claim over -- invariant_scope -- and to nothing else. Any
    // other --stop_after keeps the gate, so `--permute_outcome_seed 1..100
    // --stop_after emit` is still refused today, and `--stop_after map`
    // (§4/§5 exist; §6+ do not) is still refused rather than reporting
    // [SUCCESS] with 'map' never having run. The bypass covers exactly the
    // measurement it was written for -- kept even though 'propose' itself
    // is implemented as of Task 5a and so no longer NEEDS the bypass to
    // pass this particular check; it stays live for when invariant_scope
    // ever widens past 'propose'.
    def isInvariantScopeRun = isInvariantRun && targetStage == invariant_scope
    if (!isInvariantScopeRun) {
        requireStageImplemented(targetStage)
    }
    if (isInvariantRun && graph.indexOf(graphStage) < graph.indexOf('profile')) {
        error("--permute_outcome_seed needs a run that reaches at least 'profile'; --stop_after ${targetStage} stops before a permuted table is ever read, so the permutation could not affect anything.")
    }
    if (isInvariantRun && invariant_scope != 'propose') {
        error("--invariant_scope '${invariant_scope}' is not implemented. ADR-003 scopes the harness to the proposer; widening it is a design change requiring a superseding ADR, not a config edit.")
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

    // §4.1 — the baseline half of the replicate-keyed profile channel
    // PROPOSE_CANDIDATES consumes below. The baseline replicate is null
    // (s4-1 brief); populated only when the profile stage actually runs, so
    // a run that stops before 'profile' feeds PROPOSE_CANDIDATES nothing —
    // matching that it also never reaches the 'propose' block that would
    // consume it.
    def ch_baseline_profiles_by_replicate = channel.empty()

    if (graph.indexOf(graphStage) >= graph.indexOf('profile')) {
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

        ch_baseline_profiles_by_replicate = PROFILE_COLUMNS.out.profile.map { meta, f -> [null, f] }
    }

    //
    // §10.1 — the harness proper. Runs alongside the baseline above, never
    // instead of it: the permuted replicates are an extra measurement, and
    // results/profiles/ plus the --max_failed_frac gate stay about the data
    // the run was actually given.
    //
    // §4.1 — the harness half of the replicate-keyed profile channel;
    // populated only when the harness actually runs. meta.replicate is
    // already the int seed PROFILE_COLUMNS_PERMUTED's caller attached
    // below, so no re-parsing is needed here.
    def ch_permuted_profiles_by_replicate = channel.empty()

    // §10.1 -- INVARIANT_REPORT needs every replicate's permutation
    // manifest, but it cannot be CALLED until ch_ledgers (below, §4.3) is
    // known, and computing a ledger needs the permuted profiles this same
    // block produces. Collected here, called on after the propose block.
    def ch_permutation_manifests = channel.empty()

    if (isInvariantRun) {
        PERMUTE_OUTCOME(ch_open, file(resolvePackPath(concept_pack)), seeds)
        ch_versions = ch_versions.mix(PERMUTE_OUTCOME.out.versions.first())
        ch_permutation_manifests = PERMUTE_OUTCOME.out.manifest

        //
        // Re-key one dataset's N permuted tables into N independent channel
        // items. The replicate id is read back off the filename PERMUTE_OUTCOME
        // wrote it into: a process emits files, not structured records, so the
        // name is the only channel through which a per-file attribute can
        // cross that boundary. A file that does not match is a bug in this
        // pair of files and nowhere else, so it fails here rather than
        // silently profiling an unattributed replicate.
        //
        def ch_permuted = PERMUTE_OUTCOME.out.tables.flatMap { meta, tables ->
            (tables instanceof List ? tables : [tables]).collect { permuted ->
                def matcher = (permuted.name =~ /\.p(\d+)\.csv$/)
                if (!matcher.find()) {
                    error("PERMUTE_OUTCOME emitted '${permuted.name}', which carries no replicate id. Expected <cohort>.<dataset>.p<seed>.csv.")
                }
                [meta + [replicate: matcher.group(1) as int], permuted]
            }
        }

        // The permuted bytes re-enter the real pipeline here, one task per
        // (dataset, replicate), through the SAME profiling process the
        // baseline uses. Not batched and not skipped: bin/profile_columns.py
        // records example_values as the first k values in ROW ORDER, so a
        // shuffle genuinely changes the outcome column's evidence record.
        // A harness that reused the baseline profiles would be comparing a
        // run against itself and would report 'no leak' forever.
        PROFILE_COLUMNS_PERMUTED(ch_permuted, unitPatternsFile, profileParamsJson)
        ch_versions = ch_versions.mix(PROFILE_COLUMNS_PERMUTED.out.versions.first())

        ch_permuted_profiles_by_replicate = PROFILE_COLUMNS_PERMUTED.out.profile.map { meta, f -> [meta.replicate, f] }
    }

    /*
    ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
        §3 — link (s3-1 blocking, s3-2 scoring, s3-3 thresholding)

        Placed AFTER the profile block and BEFORE propose, matching
        stageGraph()'s canonical order -- but note that nothing below feeds
        anything above or below it in this workflow. §0.9's dependency
        direction is that §2 feeds §4 while §3 feeds §6, and §6 is not
        built; link's outputs are terminal for now.

        That is also why §10.1 does not widen in this change. The harness
        measures ledger.proposed.yaml (invariant_scope == 'propose', ADR-003),
        and no value computed here reaches the proposer: PROPOSE_CANDIDATES
        consumes profiles, not links. §3 DOES compute joint statistics across
        datasets -- m, u and match weights are exactly that -- so the moment
        §6 consumes links.parquet, the invariant's scope has to widen with
        it, in that change and not after it.

        The invariant is nonetheless enforced inside this stage rather than
        deferred to that day: bin/link_blocking.py refuses a blocking rule
        keyed on an outcome-flagged variable, and bin/link_score.py refuses a
        comparison field on one. Both refuse by the pack's FLAG, never by a
        column name (Global Constraint 1), and both kill the run rather than
        dropping the offending rule -- a silently dropped blocking rule
        lowers the recall ceiling with nothing in any report to say so.
    ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    */
    def ch_links = channel.empty()
    def ch_link_report = channel.empty()

    if (graph.indexOf(graphStage) >= graph.indexOf('link')) {
        // The admitted tables, as one JSON document. ch_open is a channel and
        // LINK_BLOCKING is a single whole-run task, so the channel has to be
        // collected before it can be handed over -- .toList() rather than a
        // per-item map, because blocking is inherently about the SET of
        // records (there are no pairs inside one table alone).
        //
        // It stays downstream of the §1.2 seal: ch_open is the filtered
        // channel, so a held-out dataset is never named in this JSON and
        // therefore never read by any link process.
        def ch_link_tables_json = ch_open
            .map { meta, table -> [cohort_id: meta.cohort_id, dataset_id: meta.dataset_id, path: table.toString()] }
            .toList()
            .map { rows -> groovy.json.JsonOutput.toJson(rows.sort { a, b -> (a.cohort_id + a.dataset_id) <=> (b.cohort_id + b.dataset_id) }) }

        // The pack's outcome-flagged variable NAMES -- the whole of what §3
        // is told about the outcome, and only so that it can refuse to use
        // it. Read from the same pack file loadPackForPropose() reads.
        def packVariablesForLink = new org.yaml.snakeyaml.Yaml().load(file(resolvePackPath(concept_pack)).text).variables
        def outcomeVariablesJson = groovy.json.JsonOutput.toJson(packVariablesForLink.findAll { it.outcome }*.name)

        LINK_BLOCKING(
            ch_link_tables_json,
            file(resolvePackPath(blocking_rules), checkIfExists: true),
            outcomeVariablesJson,
            max_block_size,
            max_pairs_warn_frac,
        )
        ch_versions = ch_versions.mix(LINK_BLOCKING.out.versions)

        // §3.1's done-when is `--stop_after block`: blocking has run, its
        // report is on disk, and nothing has been scored. 'block' is a
        // sub-stage of 'link' (see subStages() above), so it passes every
        // graph-order check as 'link' and stops here.
        if (targetStage != 'block') {
            LINK_SCORE(
                ch_link_tables_json,
                LINK_BLOCKING.out.pairs,
                file(resolvePackPath(link_comparison_spec), checkIfExists: true),
                outcomeVariablesJson,
                link_em_iterations,
                link_u_from_random_pairs,
                link_random_pair_n,
            )
            ch_versions = ch_versions.mix(LINK_SCORE.out.versions)

            LINK_RESOLVE(
                ch_link_tables_json,
                LINK_SCORE.out.scores,
                match_threshold,
                clerical_threshold,
                max_collapse_ratio_drop,
            )
            ch_versions = ch_versions.mix(LINK_RESOLVE.out.versions)
            ch_links = LINK_RESOLVE.out.links
            ch_link_report = LINK_RESOLVE.out.report
        }
    }

    //
    // §4.1 — candidate generation. Consumes the baseline profiles (replicate
    // null) AND, when the §10.1 harness is running, every permuted
    // replicate's profiles too, through the SAME process — one task per
    // REPLICATE (s4-1 brief: "Aggregate per REPLICATE, not per (dataset,
    // replicate)"), grouping both channels above by their replicate key.
    // With 100 seeded replicates plus the baseline that is 101 tasks, not
    // 300 — every dataset's profile for one replicate is collected first.
    //
    def ch_candidates = channel.empty()
    def ch_evidence = channel.empty()
    def ch_ledgers = channel.empty()

    if (graph.indexOf(graphStage) >= graph.indexOf('propose')) {
        def (packVariablesForPropose, vocabularyRelease) = loadPackForPropose(concept_pack)
        def packVariablesJson = groovy.json.JsonOutput.toJson(packVariablesForPropose)
        def proposeParamsJson = buildProposeParamsJson(max_candidates_per_column, candidate_generators)

        def ch_profiles_by_replicate = ch_baseline_profiles_by_replicate
            .mix(ch_permuted_profiles_by_replicate)
            .groupTuple()

        PROPOSE_CANDIDATES(
            ch_profiles_by_replicate,
            packVariablesJson,
            vocabularyRelease,
            proposeParamsJson,
        )
        ch_versions = ch_versions.mix(PROPOSE_CANDIDATES.out.versions)
        ch_candidates = PROPOSE_CANDIDATES.out.candidates

        //
        // §4.2 — score each candidate on six independent evidence channels.
        // Pairs each replicate's candidates.parquet with that SAME
        // replicate's profiles: ch_profiles_by_replicate is the identical
        // channel PROPOSE_CANDIDATES just consumed above, referenced again
        // here rather than re-derived — DSL2 channels broadcast to every
        // subscriber, so this does not "use it up". .join() keys the two
        // on `replicate` (null for the baseline, the harness seed
        // otherwise), giving PROPOSE_CHANNELS exactly one task per
        // replicate, same as PROPOSE_CANDIDATES.
        //
        def channelParamsJson = buildChannelParamsJson(channel_weights, enabled_channels, unit_factor_candidates, emit_confirmation_plots)
        def ch_candidates_with_profiles = PROPOSE_CANDIDATES.out.candidates.join(ch_profiles_by_replicate)

        PROPOSE_CHANNELS(
            ch_candidates_with_profiles,
            packVariablesJson,
            channelParamsJson,
        )
        ch_versions = ch_versions.mix(PROPOSE_CHANNELS.out.versions)
        ch_evidence = PROPOSE_CHANNELS.out.evidence

        //
        // §4.3 — the ranked, deterministic proposal ledger. Pairs each
        // replicate's candidates.parquet with that SAME replicate's
        // evidence.parquet via .join() on `replicate` (null for the
        // baseline, the harness seed otherwise), giving PROPOSE_LEDGER
        // exactly one task per replicate, same as PROPOSE_CANDIDATES /
        // PROPOSE_CHANNELS. This is the channel that becomes §10.1's
        // ch_ledgers below -- Task 4's placeholder `channel.empty()` line.
        //
        def ledgerParamsJson = buildLedgerParamsJson(ledger_top_k, ledger_float_precision)
        def ch_candidates_with_evidence = ch_candidates.join(ch_evidence)

        PROPOSE_LEDGER(
            ch_candidates_with_evidence,
            ledgerParamsJson,
        )
        ch_versions = ch_versions.mix(PROPOSE_LEDGER.out.versions)
        ch_ledgers = PROPOSE_LEDGER.out.ledger
    }

    //
    // §5.1 / §5.2 — the human gate, and the rule compiler behind it.
    //
    // §10.1 is scoped to the PROPOSER alone (ADR-003): nothing below reads
    // ch_permuted_profiles_by_replicate, PERMUTE_OUTCOME's output, or any
    // per-replicate seed. CONFIRM_LEDGER/COMPILE_RULES run exactly ONCE,
    // always against the BASELINE's ledger.proposed.yaml (replicate ==
    // null) -- a harness run in progress does not change this stage at all.
    //
    def ch_confirmed = channel.empty()

    if (graph.indexOf(graphStage) >= graph.indexOf('confirm')) {
        // The baseline's own ledger.proposed.yaml -- ch_ledgers already
        // carries it (replicate == null), so this is a second reference to
        // the SAME broadcast channel PROPOSE_LEDGER filled above, not a
        // second read of the file from disk.
        def ch_baseline_ledger = ch_ledgers
            .filter { replicate, ledger -> replicate == null }
            .map { replicate, ledger -> ledger }

        //
        // The human gate itself: null --confirmed_ledger stops the run,
        // with a clear message naming the path to write, under EVERY
        // profile, -profile test included. This is the ONLY place that
        // check lives (never inside CONFIRM_LEDGER's own script) precisely
        // so the auto-confirm boundary this card's brief warns about cannot
        // be tripped by a stray params.* flag: there is no
        // params.auto_confirm, no threshold, nothing a user could set
        // outside -profile test that would delete this gate, because
        // nothing besides a real, human-authored file on disk can ever
        // make this `if` false.
        //
        // Fix round 2 (I2): the refusal used to be an eager error() fired
        // the instant this line was reached -- but DSL2 builds the WHOLE
        // dataflow graph by executing this script top-to-bottom BEFORE any
        // task is actually scheduled (same reasoning requireStageImplemented's
        // own comment gives above), so an eager error() here aborted the run
        // before PROPOSE_LEDGER ever executed, and the message pointed the
        // user at a ledger.proposed.yaml that had never been written. The
        // check is now a `.subscribe` on ch_baseline_ledger instead: it only
        // fires once PROPOSE_LEDGER's baseline task has actually completed
        // (and its publishDir copy of ledger.proposed.yaml with it), so
        // 'propose' genuinely finishes and the path named in the message
        // exists on disk by the time the run stops.
        //
        if (!confirmed_ledger) {
            ch_baseline_ledger.subscribe { ledgerFile ->
                def suggestedPath = "${outdir}/ledger.confirmed.yaml"
                error(
                    "No confirmed ledger. §5's human gate requires a reviewed decision " +
                    "(accept/reject/remap/defer) for every column in '${outdir}/ledger.proposed.yaml' " +
                    "before the pipeline can proceed past 'propose'. Copy that file to " +
                    "'${suggestedPath}', add decision/variable/concept_id/confirmed_by/rationale/" +
                    "proposed_hash for each row, then re-run with --confirmed_ledger ${suggestedPath}."
                )
            }
        } else {
            def confirmParamsJson = buildConfirmParamsJson(require_rationale, allow_stale_ledger)

            CONFIRM_LEDGER(
                file(confirmed_ledger, checkIfExists: true),
                ch_baseline_ledger,
                confirmParamsJson,
            )
            ch_versions = ch_versions.mix(CONFIRM_LEDGER.out.versions)

            def compileParamsJson = buildCompileParamsJson(rule_id_prefix, fail_on_rule_collision)
            // rule_version is the PACK version that produced it (§5.2
            // Contract) -- a second, small read of the same pack YAML
            // loadPackForPropose already read above, same reasoning that
            // function's own comment gives (not a second source of truth,
            // just a second read).
            def packVersionForRules = new org.yaml.snakeyaml.Yaml().load(file(resolvePackPath(concept_pack)).text).version

            COMPILE_RULES(
                CONFIRM_LEDGER.out.decisions,
                compileParamsJson,
                packVersionForRules,
            )
            ch_versions = ch_versions.mix(COMPILE_RULES.out.versions)

            // ch_confirmed, matching s5-1's own OUT slot literally: each
            // compiled rule re-shaped into [cohort_id, dataset_id, column,
            // variable, concept_id, rule_id]. rule_id is READ from §5.2's
            // own output here, never re-derived -- there is exactly one
            // place in this pipeline that computes a rule_id
            // (bin/compile_rules.py).
            ch_confirmed = COMPILE_RULES.out.ruleset.flatMap { rulesetFile ->
                new groovy.json.JsonSlurper().parse(rulesetFile).collect { rule ->
                    [rule.from.cohort_id, rule.from.dataset_id, rule.from.column, rule.to.variable, rule.to.concept_id, rule.rule_id]
                }
            }
        }
    }

    //
    // §10.1 — now that ch_ledgers (§4.3, above) is known one way or the
    // other (a real per-replicate channel once 'propose' has run, or still
    // empty if --stop_after stopped short of it), report the harness's
    // verdict. Moved to AFTER the propose block (unlike Task 4's original
    // single combined `if (isInvariantRun)` block) because DSL2's Groovy
    // script executes top-to-bottom to BUILD the dataflow graph: ch_ledgers
    // has to be assigned its real value before this line references it, and
    // computing a real ledger needs the propose block's own output.
    // ch_permutation_manifests was collected in the earlier §10.1 block
    // (still in its original position, since it does not depend on
    // ch_ledgers) so nothing about PERMUTE_OUTCOME / PROFILE_COLUMNS_PERMUTED
    // moved.
    //
    if (isInvariantRun) {
        INVARIANT_REPORT(
            ch_permutation_manifests.collect(),
            // Hashed in Groovy rather than staged into the process: the
            // ledgers all share one filename by contract, so staging 100 of
            // them would force a rename that loses the only thing the
            // report needs them keyed by -- which replicate produced which.
            ch_ledgers.map { replicate, ledger -> "${replicate}=${sha256Hex(ledger.bytes)}" }.toSortedList(),
            // The resolved seed list, not the raw spec: parseSeedSpec()
            // above is the pipeline's only parser for `int or range`.
            seeds,
            invariant_n_permutations,
            invariant_scope,
        )
        ch_versions = ch_versions.mix(INVARIANT_REPORT.out.versions)
    }

    emit:
    datasets   = ch_open           // channel: [ meta(cohort_id, dataset_id, role, holdout), path(table) ]
    pack       = INGEST.out.pack   // channel: [ pack_hash, [variable, ...] ]
    candidates = ch_candidates     // channel: [ val(replicate), path(candidates.parquet) ] (§4.1; replicate is null for the baseline)
    evidence   = ch_evidence       // channel: [ val(replicate), path(evidence.parquet) ] (§4.2; replicate is null for the baseline)
    ledger     = ch_ledgers        // channel: [ val(replicate), path(ledger.proposed.yaml) ] (§4.3; replicate is null for the baseline)
    links      = ch_links          // channel: [ path(links.parquet) ] (§3.3)
    link_report = ch_link_report   // channel: [ path(link_report.json) ] (§3.3)
    confirmed  = ch_confirmed      // channel: [ cohort_id, dataset_id, column, variable, concept_id, rule_id ] (§5.1/§5.2)
    versions   = ch_versions       // channel: [ path(versions.yml) ]
}

/*
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    THE END
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
*/
