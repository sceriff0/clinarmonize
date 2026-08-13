//
// §1.1 — Validate the two-level samplesheet into a typed channel.
//
// Contract (docs/steps/s1-1.md):
//   IN   --input  samplesheet.csv
//        --concept_pack  assets/packs/<name>.yaml | path | url
//   OUT  ch_datasets : [ meta(cohort_id, dataset_id, role, holdout), path(table) ]
//        ch_pack     : [ pack_hash, [variable, ...] ]
//   SIDE fails fast on duplicate (cohort_id, dataset_id); writes no files
//
// Not here (nogo): this subworkflow never opens a table (only stats the path
// via nf-schema's `exists`), never infers role from a filename, and never
// accepts a sheet lacking the holdout column.
//

include { samplesheetToList } from 'plugin/nf-schema'

/*
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    FUNCTIONS
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
*/

//
// Trap (docs/steps/s1-1.md): cohort_id is NOT the primary key, (cohort_id,
// dataset_id) is. A sheet with two rows sharing both survives nf-schema's
// per-row type checks silently unless we check for it ourselves. Runs before
// samplesheetToList, i.e. before any channel is built and before any file is
// touched beyond the samplesheet itself: on failure, nothing is written.
//
def checkNoDuplicateDatasets(String sheetPath) {
    def rows = file(sheetPath, checkIfExists: true).splitCsv(header: true)
    def firstSeenAtRow = [:]
    def duplicates = []
    rows.eachWithIndex { row, i ->
        def key = [row.cohort_id, row.dataset_id]
        def rowNumber = i + 2 // +1 for 0-index, +1 for the header line
        if (firstSeenAtRow.containsKey(key)) {
            duplicates << "duplicate (cohort_id, dataset_id) = (${key[0]}, ${key[1]}) found in rows ${firstSeenAtRow[key]} and ${rowNumber}"
        }
        else {
            firstSeenAtRow[key] = rowNumber
        }
    }
    if (duplicates) {
        error("Samplesheet validation failed for '${sheetPath}': ${duplicates.join('; ')}.")
    }
}

//
// sha256 of the raw pack bytes. Used as the pack's identity (§1.1 OUT: pack_hash).
//
def sha256Hex(byte[] bytes) {
    def digest = java.security.MessageDigest.getInstance('SHA-256')
    return digest.digest(bytes).collect { b -> String.format('%02x', b) }.join()
}

//
// Structural validation for a concept pack, mirroring assets/schema_pack.json.
// The pack IS the target variable set (Ruling R2): at least one variable MUST
// be flagged outcome: true, because §0 the invariant defines "the outcome
// variable" as exactly that flag, never a hard-coded column name.
//
def validatePackStructure(Object pack, String packPath) {
    if (!(pack instanceof Map)) {
        error("Concept pack '${packPath}' is invalid: top level must be a mapping.")
    }
    def problems = []
    ['pack', 'version', 'vocabulary', 'variables'].each { key ->
        if (!pack.containsKey(key)) {
            problems << "missing top-level key '${key}'"
        }
    }
    if (problems) {
        error("Concept pack '${packPath}' is invalid: ${problems.join('; ')}.")
    }

    def variables = pack.variables
    if (!(variables instanceof List) || variables.isEmpty()) {
        error("Concept pack '${packPath}' is invalid: 'variables' must be a non-empty list.")
    }

    def varProblems = []
    variables.eachWithIndex { v, i ->
        def label = "variables[${i}]${v?.name ? " (${v.name})" : ''}"
        if (!v?.name) {
            varProblems << "${label} is missing 'name'"
        }
        if (!v?.domain) {
            varProblems << "${label} is missing 'domain'"
        }
        else if (v.domain == 'derived') {
            if (!v.derivation) {
                varProblems << "${label} has domain 'derived' but is missing 'derivation'"
            }
        }
        else {
            if (v.concept_id == null) {
                varProblems << "${label} is missing 'concept_id'"
            }
            if (!v.type) {
                varProblems << "${label} is missing 'type'"
            }
        }
    }
    if (varProblems) {
        error("Concept pack '${packPath}' is invalid: ${varProblems.join('; ')}.")
    }

    def hasOutcome = variables.any { it.outcome == true }
    if (!hasOutcome) {
        error("Concept pack '${packPath}' is invalid: no variable is flagged 'outcome: true'. §0 the invariant needs exactly this flag to identify the outcome variable.")
    }
}

//
// A relative --concept_pack (e.g. its default, assets/packs/minimal.yaml) is
// relative to the pipeline itself, not to whatever directory it happens to be
// launched from (nf-test, notably, launches from its own sandbox). URLs and
// absolute paths pass through unchanged.
//
def resolvePackPath(String packPath) {
    if (packPath ==~ /^[a-zA-Z][a-zA-Z0-9+.-]*:\/\/.*/) {
        return packPath
    }
    // NB: file(packPath).isAbsolute() is not a safe absoluteness check here --
    // file() eagerly resolves relative strings against the current working
    // directory before .isAbsolute() ever sees them, so it is always true.
    // Check the raw string instead.
    return packPath.startsWith('/') ? packPath : "${projectDir}/${packPath}"
}

//
// Load + validate the concept pack, returning [ pack_hash, [variable, ...] ].
//
def loadPack(String packPath) {
    def resolvedPath = resolvePackPath(packPath)
    def packFile = file(resolvedPath, checkIfExists: true)
    def pack = new org.yaml.snakeyaml.Yaml().load(packFile.text)
    validatePackStructure(pack, resolvedPath)
    def hash = sha256Hex(packFile.bytes)
    return [hash, pack.variables]
}

//
// alts seam (docs/steps/s1-1.md): SheetReader -- read(path) -> [(Meta, Path)]
//
// Today's only implementation parses samplesheet.csv via nf-schema's
// samplesheetToList against assets/schema_input.json. The card's Alternatives
// table names two swap candidates (a nested-YAML sheet; an OMOP-native
// manifest) -- both would replace only this function's body. Nothing else in
// INGEST, or in workflows/harmonize.nf, calls samplesheetToList directly.
//
def SheetReader_read(String path) {
    return samplesheetToList(path, "${projectDir}/assets/schema_input.json")
}

/*
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    SUBWORKFLOW
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
*/

workflow INGEST {

    take:
    input        // string: path to the two-level samplesheet (--input)
    concept_pack // string: path or URL to the concept pack (--concept_pack)

    main:
    checkNoDuplicateDatasets(input)

    def ch_datasets = channel.fromList(SheetReader_read(input))

    def (pack_hash, pack_variables) = loadPack(concept_pack)
    def ch_pack = channel.value([pack_hash, pack_variables])

    emit:
    datasets = ch_datasets // channel: [ meta(cohort_id, dataset_id, role, holdout), path(table) ]
    pack     = ch_pack     // channel: [ pack_hash, [variable, ...] ]
}
