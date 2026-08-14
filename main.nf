#!/usr/bin/env nextflow
/*
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    clinarmonize/clinicalharmonize
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    Github : https://github.com/clinarmonize/clinicalharmonize
----------------------------------------------------------------------------------------
*/

/*
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    IMPORT FUNCTIONS / MODULES / SUBWORKFLOWS / WORKFLOWS
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
*/

include { CLINICALHARMONIZE  } from './workflows/harmonize'
include { PIPELINE_INITIALISATION } from './subworkflows/local/utils_nfcore_clinicalharmonize_pipeline'
include { PIPELINE_COMPLETION     } from './subworkflows/local/utils_nfcore_clinicalharmonize_pipeline'
include { getGenomeAttribute      } from './subworkflows/local/utils_nfcore_clinicalharmonize_pipeline'

/*
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    GENOME PARAMETER VALUES
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
*/

// TODO nf-core: Remove this line if you don't need a FASTA file
//   This is an example of how to use getGenomeAttribute() to fetch parameters
//   from igenomes.config using `--genome`
params.fasta = getGenomeAttribute('fasta')

/*
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    NAMED WORKFLOWS FOR PIPELINE
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
*/

//
// WORKFLOW: Run main analysis pipeline depending on type of input
//
workflow CLINARMONIZE_CLINICALHARMONIZE {

    take:
    samplesheet // string: path to the two-level samplesheet read in from --input

    main:

    //
    // WORKFLOW: Run pipeline
    //
    CLINICALHARMONIZE (
        samplesheet,
        params.concept_pack,
        params.stop_after,
        params.allow_single_dataset,
        params.unseal,
        params.locked_model,
        params.unseal_log,
        params.params_hash_file,
        params.max_unique_listed,
        params.date_formats,
        params.example_k,
        params.na_strings,
        params.unit_header_patterns,
        params.ucum_release,
        params.infer_unit_from_range,
        params.max_failed_frac,
        params.fail_sample_k,
        params.permute_outcome_seed,
        params.invariant_n_permutations,
        params.invariant_scope,
        params.max_candidates_per_column,
        params.candidate_generators,
        params.channel_weights,
        params.enabled_channels,
        params.unit_factor_candidates,
        params.emit_confirmation_plots,
        params.ledger_top_k,
        params.ledger_float_precision,
        params.confirmed_ledger,
        params.require_rationale,
        params.allow_stale_ledger,
        params.rule_id_prefix,
        params.fail_on_rule_collision,
        params.outdir,
    )
    emit:
    datasets   = CLINICALHARMONIZE.out.datasets   // channel: [ meta(cohort_id, dataset_id, role, holdout), path(table) ]
    pack       = CLINICALHARMONIZE.out.pack       // channel: [ pack_hash, [variable, ...] ]
    candidates = CLINICALHARMONIZE.out.candidates // channel: [ val(replicate), path(candidates.parquet) ]
    evidence   = CLINICALHARMONIZE.out.evidence   // channel: [ val(replicate), path(evidence.parquet) ]
    ledger     = CLINICALHARMONIZE.out.ledger     // channel: [ val(replicate), path(ledger.proposed.yaml) ]
    confirmed  = CLINICALHARMONIZE.out.confirmed  // channel: [ cohort_id, dataset_id, column, variable, concept_id, rule_id ]
}
/*
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    RUN MAIN WORKFLOW
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
*/

workflow {

    main:
    //
    // SUBWORKFLOW: Run initialisation tasks
    //
    PIPELINE_INITIALISATION (
        params.version,
        params.validate_params,
        params.monochrome_logs,
        args,
        params.outdir,
        params.input,
        params.help,
        params.help_full,
        params.show_hidden
    )

    //
    // WORKFLOW: Run main workflow
    //
    // params.input is passed directly (not via PIPELINE_INITIALISATION.out.samplesheet):
    // that output is itself a channel-wrapped value once emitted, and §1.1's ingest
    // needs the plain path string to read+validate the samplesheet itself.
    //
    CLINARMONIZE_CLINICALHARMONIZE (
        params.input
    )
    //
    // SUBWORKFLOW: Run completion tasks
    //
    PIPELINE_COMPLETION (
        params.email,
        params.email_on_fail,
        params.plaintext_email,
        params.outdir,
        params.monochrome_logs,
        channel.empty() // no MultiQC in this phase; kept for interface compatibility with PIPELINE_COMPLETION
    )
}

/*
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    THE END
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
*/
