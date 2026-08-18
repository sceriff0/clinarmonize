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
include { MAP_CONCEPTS } from '../modules/local/map_concepts/main'
include { CONVERT_UNITS } from '../modules/local/convert_units/main'
include { VALUE_MAP } from '../modules/local/value_map/main'
//
// ADR-004 -- `--invariant_scope map` re-runs LINK and MAP on every permuted
// replicate, through the SAME processes the baseline uses, under aliases.
// Identical reasoning to PROFILE_COLUMNS_PERMUTED above: a purpose-built
// "link for the test" module would be a path the invariant is not actually
// tested on. §5 is NOT aliased and never re-runs -- it is a human gate, and
// a map-scoped replicate holds the rules fixed at the BASELINE's ruleset,
// which is what makes this measure the §3 -> §6 edge rather than re-measuring
// the proposer's own claim.
//
include { LINK_BLOCKING as LINK_BLOCKING_PERMUTED } from '../modules/local/link_blocking/main'
include { LINK_SCORE    as LINK_SCORE_PERMUTED    } from '../modules/local/link_score/main'
include { LINK_RESOLVE  as LINK_RESOLVE_PERMUTED  } from '../modules/local/link_resolve/main'
include { MAP_CONCEPTS  as MAP_CONCEPTS_PERMUTED  } from '../modules/local/map_concepts/main'
//
// §6.2 is INSIDE the 'map' stage, so ADR-004's `mapped/` artefact is this
// process's output and not MAP_CONCEPTS'. Digesting §6.1's output instead
// would leave the harness measuring an artefact the run does not publish --
// and the argument that it makes no difference (a unit conversion is a fixed
// function of the mapped rows, so it cannot introduce a dependence on the
// outcome that was not already there) is exactly the shape of soundness
// argument ADR-003 made for the propose scope and ADR-004 had to widen when
// it stopped holding. Cheaper to measure the published bytes than to keep
// re-deriving why measuring something else is equivalent.
//
include { CONVERT_UNITS as CONVERT_UNITS_PERMUTED } from '../modules/local/convert_units/main'
//
// §6.3 is INSIDE the 'map' stage too, and it is now the LAST writer of
// `mapped/` -- so ADR-004's artefact is THIS process's output. The amendment
// at the foot of ADR-004 says why the alias is not optional: `ch_mapped` is
// rebound at each stage of §6 and ARTEFACT_DIGEST consumes it, so a §6.3 that
// ran on the baseline and not on the replicates would digest a baseline
// carrying value_as_concept_id against replicates that never had the column,
// and every replicate would differ from the baseline for a reason that is not
// a leak. Running it and NOT digesting it is the mirror failure: [SUCCESS]
// over bytes the run does not publish.
//
include { VALUE_MAP as VALUE_MAP_PERMUTED } from '../modules/local/value_map/main'
include { ARTEFACT_DIGEST } from '../modules/local/artefact_digest/main'

/*
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    THE NINE-STAGE GRAPH (§0.9)

    'ingest', 'profile', 'link', 'propose', 'confirm' and 'map' are
    implemented. A run that tries to reach any other stage fails with a
    clear message instead of silently succeeding as if that stage had run.

    implementedStages() was NOT a contiguous prefix of stageGraph() for the
    whole of phase 0: 'link' (§3) was unbuilt while 'propose' (§4), which
    sits AFTER it in the canonical order, was -- because §0.7 puts link at
    build-order item 7, after propose and confirm, "so the invariant work is
    not blocked behind entity resolution", and §0.9 confirms the dependency
    direction (§2 feeds §4, while §3 feeds §6). That is why the gate below
    checks the REQUESTED stage's own membership rather than walking the
    graph for the first gap.

    §3 and §6.1 are now built, so the set happens to be contiguous again --
    and the mechanism stays exactly as it was. It is not an accident to be
    tidied away: §7-§9 are unbuilt, and the identical situation recurs the
    moment any later stage is built out of graph order. Do not "fix" this
    into a prefix walk.
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
*/
def stageGraph() {
    return ['ingest', 'profile', 'link', 'propose', 'confirm', 'map', 'derive', 'coverage', 'emit']
}

def implementedStages() {
    return ['ingest', 'profile', 'link', 'propose', 'confirm', 'map'] as Set
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

//
// §6.1's four Params rows, as one JSON object -- same shape and reasoning
// as the propose/confirm/compile bundles above: one quoted argument per
// stage rather than four positional flags a reordering could silently
// transpose.
//
def buildMapParamsJson(String cdmVersion, List cdmDomains, Number maxUnmappedFrac, Boolean keepSourceConcept) {
    return groovy.json.JsonOutput.toJson([
        cdm_version        : cdmVersion,
        cdm_domains        : cdmDomains,
        max_unmapped_frac  : maxUnmappedFrac,
        keep_source_concept: keepSourceConcept,
    ])
}

//
// §6.2's own params, as their own document rather than more keys on
// buildMapParamsJson's: bin/convert_units.py never reads a §6.1 param and
// bin/map_concepts.py never reads a §6.2 one, and a shared blob would let
// either start doing so without the wiring saying it had.
//
def buildConvertParamsJson(Boolean failOnImplausibleRange, List plausibleRangeQuantiles) {
    return groovy.json.JsonOutput.toJson([
        fail_on_implausible_range: failOnImplausibleRange,
        plausible_range_quantiles: plausibleRangeQuantiles,
    ])
}

//
// §6.3's three Params rows, as their own document -- same reasoning as
// buildConvertParamsJson's own: bin/value_map.py reads no §6.1 or §6.2 param
// and neither of those reads a §6.3 one, and a shared blob would let any of
// them start doing so without the wiring saying it had.
//
def buildValueParamsJson(Integer maxFanInWarn, Boolean emitAlluvial, String unmappedValuePolicy) {
    return groovy.json.JsonOutput.toJson([
        max_fan_in_warn      : maxFanInWarn,
        emit_alluvial        : emitAlluvial,
        unmapped_value_policy: unmappedValuePolicy,
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
    cdm_version             // string:  target OMOP CDM release; validated and recorded, nothing branches on it (§6.1)
    cdm_domains             // list:    the five-table subset emitted; never a claim of full conformance (§6.1)
    max_unmapped_frac       // number:  above this fraction of source values unmapped, the run fails (§6.1)
    keep_source_concept     // boolean: retains the pre-translation concept; off breaks --audit (§6.1)
    unit_conversion_table   // string:  analyte-aware factors; the ONLY source of conversions (§6.2)
    fail_on_implausible_range // boolean: a converted distribution outside the pack range stops the run (§6.2)
    plausible_range_quantiles // list:   which quantiles the range check reads, so outliers alone cannot fail it (§6.2)
    max_fan_in_warn         // int:     a collapse wider than this is flagged in the QC report (§6.3)
    emit_alluvial           // boolean: the plot that makes a collapse visible at all (§6.3)
    unmapped_value_policy   // string:  whether an unmappable value stops the run or is recorded (§6.3)
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
    // --stop_after emit` is still refused today whatever the scope, and
    // `--stop_after map` is allowed through only when the scope is 'map'
    // (ADR-004) rather than reporting [SUCCESS] with the mapper never
    // measured. The bypass covers exactly the measurement it was written
    // for -- kept even though both wired scopes are now implemented stages
    // and so no longer NEED it to pass this particular check; it stays live
    // for whenever invariant_scope widens to a stage that is not.
    def isInvariantScopeRun = isInvariantRun && targetStage == invariant_scope
    if (!isInvariantScopeRun) {
        requireStageImplemented(targetStage)
    }
    if (isInvariantRun && graph.indexOf(graphStage) < graph.indexOf('profile')) {
        error("--permute_outcome_seed needs a run that reaches at least 'profile'; --stop_after ${targetStage} stops before a permuted table is ever read, so the permutation could not affect anything.")
    }
    //
    // A seeded run may not be aimed PAST the scope the harness measures.
    //
    // This property used to hold by accident. Until §6.1, 'map' was
    // unimplemented, so requireStageImplemented() refused
    // `--permute_outcome_seed 1..100 --stop_after map` on its way past --
    // and tests/invariant.nf.test asserted exactly that refusal. Building
    // §6.1 made 'map' implemented and the refusal silently disappeared:
    // the run went all the way through the mapper while INVARIANT_REPORT
    // still measured nothing but ledger.proposed.yaml, and reported
    // [SUCCESS].
    //
    // That is the failure ADR-003's enum-refusal exists to prevent, arriving
    // through the other door. Stages after invariant_scope RUN and PUBLISH
    // on such a run, and the harness's verdict says nothing whatever about
    // them -- a reader who asked for `--stop_after map` and got a green
    // invariant report would reasonably conclude the mapper had been
    // measured. So the check is now explicit and no longer a function of
    // which stages happen to be built.
    //
    if (isInvariantRun && graph.indexOf(graphStage) > graph.indexOf(invariant_scope)) {
        error(
            "--permute_outcome_seed is set and --stop_after ${targetStage} runs PAST --invariant_scope " +
            "'${invariant_scope}'. The harness holds its claim over '${invariant_scope}' and nothing after it " +
            "(ADR-003, docs/adr/0003-invariant-scope-is-the-proposer.md; ADR-004 for 'map'); the later stages " +
            "would run, publish, and be covered by a [SUCCESS] that never measured them. Stop at " +
            "'${invariant_scope}', or set --invariant_scope to a wired scope that reaches ${targetStage}."
        )
    }
    //
    // Which scopes this pipeline can actually MEASURE. The list is here and
    // in bin/invariant_report.py's SCOPE_ARTEFACTS, and the two must agree:
    // this one decides which stages RUN per replicate, that one decides
    // which artefacts are HASHED, and a scope wired in one but not the other
    // is exactly a [SUCCESS] over a claim nothing measured.
    //
    // 'all' stays refused. §8.3's standardized-difference forest legitimately
    // reads outcome variables for REPORTING, so an end-to-end scope would go
    // red honestly and then get relaxed -- and a harness that has been
    // relaxed once is a harness nobody trusts again.
    //
    def implementedScopes = ['propose', 'map'] as Set
    if (isInvariantRun && !(invariant_scope in implementedScopes)) {
        error("--invariant_scope '${invariant_scope}' is not implemented (implemented: ${implementedScopes.sort().join(', ')}). Widening the harness is a design change requiring an ADR, not a config edit: ADR-003 (docs/adr/0003-invariant-scope-is-the-proposer.md) scopes it to the proposer and ADR-004 (docs/adr/0004-invariant-scope-widens-to-map.md) composes that with the §3 -> §6 edge §6.1 opened. Until a scope has both an ADR and its wiring, it stays refused, so that no run can report success having measured a scope it did not run.")
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

    // ADR-004 -- the permuted tables, re-keyed by replicate and collected
    // into the one-JSON-document shape §3 and §6 both take. Assigned inside
    // the harness block below and consumed by the map-scoped block far
    // further down, for the same top-to-bottom-script reason
    // ch_permutation_manifests is declared here rather than there.
    def ch_permuted_tables_json = channel.empty()

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

        // ADR-004 -- the same document ch_link_tables_json builds for the
        // baseline, one per REPLICATE. Built from ch_permuted (the re-keyed
        // permuted tables) rather than from ch_open, which is the whole
        // point: a map-scoped replicate links and maps the PERMUTED bytes.
        //
        // Sorted by (cohort_id, dataset_id) exactly as the baseline's is, so
        // the JSON string a replicate hands to LINK_BLOCKING differs from the
        // baseline's only in the paths -- never in the order of its entries,
        // which would otherwise make the digest a function of which
        // PERMUTE_OUTCOME task finished first.
        ch_permuted_tables_json = ch_permuted
            .map { meta, table -> [meta.replicate, [cohort_id: meta.cohort_id, dataset_id: meta.dataset_id, path: table.toString()]] }
            .groupTuple()
            .map { replicate, rows -> [replicate, groovy.json.JsonOutput.toJson(rows.sort { a, b -> (a.cohort_id + a.dataset_id) <=> (b.cohort_id + b.dataset_id) })] }
    }

    /*
    ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
        §3 — link (s3-1 blocking, s3-2 scoring, s3-3 thresholding)

        Placed AFTER the profile block and BEFORE propose, matching
        stageGraph()'s canonical order -- and nothing here feeds the propose
        block below it. §0.9's dependency direction is that §2 feeds §4 while
        §3 feeds §6, so link's one consumer is the §6.1 block further down,
        which joins links.parquet for person_id.

        §3 computes joint statistics across datasets -- m, u and match
        weights are exactly that -- and none of them reaches the proposer:
        PROPOSE_CANDIDATES consumes profiles, not links. They DO reach §6.1,
        which joins links.parquet for person_id, and that is the edge ADR-004
        widened the harness across. Under `--invariant_scope map` these three
        processes are re-run per permuted replicate under their _PERMUTED
        aliases (see the harness block after §6.1 below), so the joint
        statistics computed here are inside the measurement rather than
        beside it.

        The invariant is nonetheless enforced inside this stage rather than
        deferred to that day: bin/link_blocking.py refuses a blocking rule
        keyed on an outcome-flagged variable, and bin/link_score.py refuses a
        comparison field on one. Both refuse by the pack's FLAG, never by a
        column name (Global Constraint 1), and both kill the run rather than
        dropping the offending rule -- a silently dropped blocking rule
        lowers the recall ceiling with nothing in any report to say so.
    ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    */
    // The pack's outcome-flagged variable NAMES -- the whole of what §3
    // is told about the outcome, and only so that it can refuse to use
    // it. Read from the same pack file loadPackForPropose() reads.
    //
    // Hoisted OUT of the link block (ADR-004): the map-scoped harness block
    // further down invokes the same three link processes on permuted bytes
    // and needs the identical value. Re-deriving it there would put a second
    // reading of "which variable is the outcome" in this file, and the whole
    // of Global Constraint 1 is that there is exactly one.
    def packVariablesForLink = new org.yaml.snakeyaml.Yaml().load(file(resolvePackPath(concept_pack)).text).variables
    def outcomeVariablesJson = groovy.json.JsonOutput.toJson(packVariablesForLink.findAll { it.outcome }*.name)
    // Resolved here rather than at each call site, so the baseline and the
    // permuted replicates provably read the SAME rule file and the SAME
    // comparison spec. checkIfExists now fires on any run rather than only on
    // one that reaches 'link' -- a misspelt --blocking_rules is a defect
    // whether or not this particular invocation would have got as far as
    // opening it.
    def blockingRulesFile = file(resolvePackPath(blocking_rules), checkIfExists: true)
    def comparisonSpecFile = file(resolvePackPath(link_comparison_spec), checkIfExists: true)

    // The admitted tables, as one JSON document. ch_open is a channel and
    // LINK_BLOCKING is a single whole-run task, so the channel has to be
    // collected before it can be handed over -- .toList() rather than a
    // per-item map, because blocking is inherently about the SET of
    // records (there are no pairs inside one table alone).
    //
    // It stays downstream of the §1.2 seal: ch_open is the filtered
    // channel, so a held-out dataset is never named in this JSON and
    // therefore never read by any link process.
    //
    // Keyed by replicate -- null, the baseline -- for the same reason
    // PROPOSE_LEDGER's channel is: the harness's permuted replicates travel
    // the identical processes under an alias, and the key is what pairs each
    // stage's output with the SAME replicate's input rather than with
    // whichever task finished first. §6 reads it too, so it is built once
    // here rather than once per block.
    def ch_tables_json_by_replicate = ch_open
        .map { meta, table -> [cohort_id: meta.cohort_id, dataset_id: meta.dataset_id, path: table.toString()] }
        .toList()
        .map { rows -> [null, groovy.json.JsonOutput.toJson(rows.sort { a, b -> (a.cohort_id + a.dataset_id) <=> (b.cohort_id + b.dataset_id) })] }

    def ch_links = channel.empty()
    def ch_link_report = channel.empty()

    if (graph.indexOf(graphStage) >= graph.indexOf('link')) {
        LINK_BLOCKING(
            ch_tables_json_by_replicate,
            blockingRulesFile,
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
                ch_tables_json_by_replicate.join(LINK_BLOCKING.out.pairs),
                comparisonSpecFile,
                outcomeVariablesJson,
                link_em_iterations,
                link_u_from_random_pairs,
                link_random_pair_n,
            )
            ch_versions = ch_versions.mix(LINK_SCORE.out.versions)

            LINK_RESOLVE(
                ch_tables_json_by_replicate.join(LINK_SCORE.out.scores),
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
    def ch_ruleset = channel.empty()

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

            // §6.1's IN slot names rules/ruleset.json itself, not the
            // re-shaped ch_confirmed above: the mapper needs `kind` and the
            // full from/to objects, and re-shaping them back out of a tuple
            // would put a second reading of the ruleset's structure in the
            // repo. The FILE is what travels.
            ch_ruleset = COMPILE_RULES.out.ruleset
        }
    }

    /*
    ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
        §6.1 — apply concept mappings into OMOP rows.

        The first stage that consumes §3's output. §0.9's dependency
        direction is that §2 feeds §4 while §3 feeds §6, and this is that
        second edge: links.parquet supplies person_id, and nothing else in
        the pipeline does.

        That edge is what the phase-1 handoff flagged as the §10.1 trigger.
        It is measured by --invariant_scope map, whose wiring is the block
        immediately below this one (ADR-004,
        docs/adr/0004-invariant-scope-widens-to-map.md): the harness's claim
        reaches the mapped tables, so a joint statistic computed in §3 and
        carried into §6 is inside the measurement rather than beside it.
    ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    */
    def ch_mapped = channel.empty()
    def ch_unmapped = channel.empty()
    def ch_unit_conversions = channel.empty()
    def ch_value_collapse = channel.empty()
    def ch_value_unmapped = channel.empty()

    // Hoisted out of the map block for the same reason outcomeVariablesJson
    // was hoisted out of the link block: the map-scoped harness below feeds
    // MAP_CONCEPTS_PERMUTED the identical pack view and the identical params.
    def (packVariablesForMap, vocabularyReleaseForMap) = loadPackForPropose(concept_pack)
    def packVariablesForMapJson = groovy.json.JsonOutput.toJson(packVariablesForMap)
    def mapParamsJson = buildMapParamsJson(cdm_version, cdm_domains, max_unmapped_frac, keep_source_concept)
    def convertParamsJson = buildConvertParamsJson(fail_on_implausible_range, plausible_range_quantiles)
    def valueParamsJson = buildValueParamsJson(max_fan_in_warn, emit_alluvial, unmapped_value_policy)
    // Resolved once, outside the block, for the same reason: the permuted
    // replicates below convert against the IDENTICAL factor table. A second
    // file() call is a second chance for the two halves of the harness to be
    // reading different conversions.
    def unitConversionTableFile = file(resolvePackPath(unit_conversion_table), checkIfExists: true)

    if (graph.indexOf(graphStage) >= graph.indexOf('map')) {
        // The tables document and the links arrive as ONE tuple keyed by
        // replicate (null here -- the baseline): §6 must map the bytes THIS
        // replicate linked, and pairing them by channel position would be
        // correct only for as long as there is exactly one replicate.
        MAP_CONCEPTS(
            ch_tables_json_by_replicate.join(ch_links),
            ch_ruleset,
            packVariablesForMapJson,
            vocabularyReleaseForMap,
            mapParamsJson,
        )
        ch_versions = ch_versions.mix(MAP_CONCEPTS.out.versions)
        ch_unmapped = MAP_CONCEPTS.out.unmapped

        //
        // §6.2 — convert units through UCUM, refusing ambiguity.
        //
        // Not a separate stage. §0.9's graph has nine entries and 'map' is
        // one of them; §6.1, §6.2 and §6.3 are its parts, exactly as §3.1 to
        // §3.3 are 'link'. `--stop_after map` therefore reaches through this
        // process, and there is no `--stop_after` value that stops between
        // §6.1 and §6.2 -- a mapped table whose units were never converted is
        // not a smaller result, it is a result in unknown units.
        //
        // §6.3 (below) consumes THIS process's output and rebinds ch_mapped
        // onward, so the converted artefact is what gets value-mapped and
        // there is only ever one mapped/ in the repo's vocabulary. This is
        // where the chain hands over, not where it ends.
        //
        CONVERT_UNITS(
            MAP_CONCEPTS.out.mapped,
            ch_ruleset,
            packVariablesForMapJson,
            unitConversionTableFile,
            convertParamsJson,
        )
        ch_versions = ch_versions.mix(CONVERT_UNITS.out.versions)
        ch_unit_conversions = CONVERT_UNITS.out.conversions

        //
        // §6.3 — map value vocabularies, and show every collapse.
        //
        // The third part of 'map', and the same non-stage §6.2 is: there is
        // no --stop_after value between §6.1, §6.2 and §6.3, because a mapped
        // table whose categorical columns still carry each cohort's own value
        // grain is not a smaller result, it is a result in unharmonized
        // vocabularies.
        //
        // ch_mapped is REBOUND here, taking over from CONVERT_UNITS, so the
        // single publisher of results/mapped/ (conf/modules.config) and the
        // artefact ADR-004's digest is taken over are the SAME bytes -- which
        // is the whole point of there being exactly one `mapped/` in this
        // repo's vocabulary. §7 will take it over in turn.
        //
        VALUE_MAP(
            CONVERT_UNITS.out.mapped,
            ch_ruleset,
            packVariablesForMapJson,
            valueParamsJson,
        )
        ch_versions = ch_versions.mix(VALUE_MAP.out.versions)
        ch_mapped = VALUE_MAP.out.mapped
        ch_value_collapse = VALUE_MAP.out.collapse
        ch_value_unmapped = VALUE_MAP.out.unmapped_values
    }

    /*
    ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
        §10.1 / ADR-004 — the map-scoped harness.

        The trigger the phase-1 and phase-2 handoffs both named has fired:
        §6.1 joins link/links.parquet for person_id, which is a statistic
        computed jointly across records and the first value to reach a
        published artefact without passing through ledger.proposed.yaml.
        ADR-003's soundness argument does not cover it, so the harness's
        claim widens to cover it instead.

        What a map-scoped replicate runs, and what it deliberately does not:

            permute the outcome column WITHIN each cohort   (unchanged)
            profile the permuted tables                     PROFILE_COLUMNS_PERMUTED
            link   the permuted tables                      LINK_*_PERMUTED
            map    them against the BASELINE's ruleset      MAP_CONCEPTS_PERMUTED
            convert units, BASELINE factors and params      CONVERT_UNITS_PERMUTED
            value-map, BASELINE ruleset and params          VALUE_MAP_PERMUTED
            digest mapped/ canonically                      ARTEFACT_DIGEST

        The last three lines grow with §6. `mapped/` is whatever the LAST
        writer of it wrote, and each part of §6 has taken that role from the
        one before (ADR-004's amendments). A replicate that stopped at an
        earlier part would be digesting a different artefact from the baseline
        it is compared against, which reads as a leak in every replicate.

        §5 is NOT re-run. It is a human gate: ledger.confirmed.yaml carries a
        proposed_hash keyed to the BASELINE's ledger.proposed.yaml, and a
        permuted replicate produces a different proposed ledger, so
        CONFIRM_LEDGER would reject it as stale -- correctly. There is no way
        to confirm 100 permuted replicates and no honest way to fake it. So
        the rules are held FIXED at the baseline's ruleset, and what varies is
        exactly what flows through linkage into mapping: the newly-exposed
        surface, and nothing else.

        The ledger is still hashed per replicate as well (the propose scope is
        composed into this one, never replaced by it -- bin/invariant_report.py
        builds the composite). §6 leaking would not excuse §4 leaking.
    ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    */
    // "<replicate>=<canonical digest of mapped/>", one per replicate.
    // channel.value([]) rather than channel.empty() so INVARIANT_REPORT still
    // has a value to consume under the 'propose' scope: an empty CHANNEL on a
    // val input would stall the process forever, and the harness's own report
    // is the last thing that should be able to hang a run.
    def ch_mapped_hashes = channel.value([])

    if (isInvariantRun && invariant_scope == 'map' && graph.indexOf(graphStage) >= graph.indexOf('map')) {
        // The baseline's ruleset, as a VALUE channel. ch_ruleset is a queue
        // channel carrying exactly one item, and a queue channel pairs with a
        // multi-item input ONCE -- the second replicate would wait forever
        // for a second ruleset that never comes. .first() is what turns "one
        // item" into "the same item for every replicate", and it is also the
        // literal expression of ADR-004's rules-held-fixed decision.
        def ch_baseline_ruleset = ch_ruleset.first()

        LINK_BLOCKING_PERMUTED(
            ch_permuted_tables_json,
            blockingRulesFile,
            outcomeVariablesJson,
            max_block_size,
            max_pairs_warn_frac,
        )
        ch_versions = ch_versions.mix(LINK_BLOCKING_PERMUTED.out.versions.first())

        LINK_SCORE_PERMUTED(
            ch_permuted_tables_json.join(LINK_BLOCKING_PERMUTED.out.pairs),
            comparisonSpecFile,
            outcomeVariablesJson,
            link_em_iterations,
            link_u_from_random_pairs,
            link_random_pair_n,
        )
        ch_versions = ch_versions.mix(LINK_SCORE_PERMUTED.out.versions.first())

        LINK_RESOLVE_PERMUTED(
            ch_permuted_tables_json.join(LINK_SCORE_PERMUTED.out.scores),
            match_threshold,
            clerical_threshold,
            max_collapse_ratio_drop,
        )
        ch_versions = ch_versions.mix(LINK_RESOLVE_PERMUTED.out.versions.first())

        MAP_CONCEPTS_PERMUTED(
            ch_permuted_tables_json.join(LINK_RESOLVE_PERMUTED.out.links),
            ch_baseline_ruleset,
            packVariablesForMapJson,
            vocabularyReleaseForMap,
            mapParamsJson,
        )
        ch_versions = ch_versions.mix(MAP_CONCEPTS_PERMUTED.out.versions.first())

        // §6.2 on every replicate, for the reason the import comment gives:
        // the artefact ADR-004 names is `mapped/`, and after §6.2 that is
        // this process's output. The baseline's digest below comes from
        // ch_mapped, which is already CONVERT_UNITS'; digesting the
        // replicates before conversion would compare two different artefacts
        // and call the result one hash set.
        //
        // The factor table and the params are the BASELINE's, exactly as the
        // ruleset is (ADR-004: what varies across replicates is the permuted
        // outcome and nothing else -- a per-replicate conversion table would
        // introduce a second moving part and make a red verdict unattributable).
        CONVERT_UNITS_PERMUTED(
            MAP_CONCEPTS_PERMUTED.out.mapped,
            ch_baseline_ruleset,
            packVariablesForMapJson,
            unitConversionTableFile,
            convertParamsJson,
        )
        ch_versions = ch_versions.mix(CONVERT_UNITS_PERMUTED.out.versions.first())

        // §6.3 on every replicate, for the reason §6.2 runs on every
        // replicate: `mapped/` is whatever the LAST writer of it wrote, and
        // that is now this process. The ruleset is the BASELINE's, so the
        // collapse groups are held fixed exactly as the column maps and the
        // conversion factors are -- what varies across replicates is the
        // permuted outcome and nothing else.
        //
        // The card's own value-level argument for digesting §6.3's output
        // rather than §6.2's is stronger than §6.2's was for its own case: a
        // value collapse is many-to-one, so it can only ever LOSE
        // distinctions. Digesting before it would compare artefacts whose
        // distinctions had not yet been merged, and a leak that survived only
        // as a difference between two values that collapse into one would be
        // visible there and absent from the bytes the run publishes -- which
        // is a harness reporting on something nobody reads.
        VALUE_MAP_PERMUTED(
            CONVERT_UNITS_PERMUTED.out.mapped,
            ch_baseline_ruleset,
            packVariablesForMapJson,
            valueParamsJson,
        )
        ch_versions = ch_versions.mix(VALUE_MAP_PERMUTED.out.versions.first())

        // ONE invocation over the baseline AND every replicate, mixed into a
        // single channel -- so there is no second call site whose code could
        // differ from the one that digested the run the replicates are
        // compared against. (The link and map stages needed aliases because
        // their baseline invocation is a real pipeline stage that publishes
        // results; this process exists only for the harness, so it does not.)
        ARTEFACT_DIGEST(
            ch_mapped.mix(VALUE_MAP_PERMUTED.out.mapped),
            'mapped/',
            ledger_float_precision,
        )
        ch_versions = ch_versions.mix(ARTEFACT_DIGEST.out.versions.first())

        // Read out of the digest JSON in Groovy, the same way ch_confirmed is
        // read out of ruleset.json above: the process has already completed
        // by the time this map() sees its output, so the file is on disk.
        ch_mapped_hashes = ARTEFACT_DIGEST.out.digest
            .map { replicate, digestFile -> "${replicate}=${new groovy.json.JsonSlurper().parse(digestFile).digest}" }
            .toSortedList()
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
            // ADR-004 -- the second artefact of the composed 'map' scope, and
            // an empty list under 'propose'. NOT hashed in Groovy the way the
            // ledgers just above are: mapped/ is parquet, whose BYTES carry
            // writer metadata, compression choices and row-group boundaries,
            // none of which are the data. ARTEFACT_DIGEST reads it back
            // through duckdb instead (bin/artefact_digest.py).
            ch_mapped_hashes,
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
    links      = ch_links          // channel: [ val(replicate), path(links.parquet) ] (§3.3; replicate is null for the baseline)
    link_report = ch_link_report   // channel: [ val(replicate), path(link_report.json) ] (§3.3; replicate is null for the baseline)
    confirmed  = ch_confirmed      // channel: [ cohort_id, dataset_id, column, variable, concept_id, rule_id ] (§5.1/§5.2)
    mapped     = ch_mapped         // channel: [ val(replicate), [path(mapped/*.parquet)] ] (§6.1 mapped, §6.2 converted, §6.3 value-mapped)
    unit_conversions = ch_unit_conversions // channel: [ val(replicate), path(qc/unit_conversions.json) ] (§6.2)
    value_collapse   = ch_value_collapse   // channel: [ val(replicate), path(qc/value_collapse.json) ] (§6.3)
    value_unmapped   = ch_value_unmapped   // channel: [ val(replicate), path(qc/value_unmapped.json) ] (§6.3)
    versions   = ch_versions       // channel: [ path(versions.yml) ]
}

/*
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    THE END
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
*/
