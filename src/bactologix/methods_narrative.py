"""Readable Methods from frozen configuration and separately attributed evidence.

This module does not run tools, inspect today's environments, or infer execution
from the existence of an output. Parameters remain configuration even when a
native record reports that a stage completed.
"""
import re

from .methods_flow import TASKS, _rule_tool
from .methods_parameters import GENESCOPE, per_tool


UNKNOWN = 'Not recorded'
EXPERIMENTAL_HYBAS = {
    '08c315479f5493ce0b1f44ba26da3c7f2e0d748b',
    '79c5b7f342e02bd5b60bf7847fc5f4fb441e1fd4',
    '0d334449d69fe2208b79affad2f5162ac801be05',
    '7cbd29d942a3d70bc1f6c7be176a75ce4bb7f2a8',
}


def _bool(value):
    return value is True or isinstance(value, str) and value.lower() in {'true', 'yes', '1', 'on'}


def _text(value):
    return UNKNOWN if value is None or str(value).strip().lower() in {'', 'unknown', 'unavailable', 'na', 'n/a'} else str(value)


def allocation(run, cfg):
    """Describe frozen scheduling choices without claiming observed memory use."""
    from .compute_memory import policy
    limits = policy(run, config=cfg)
    sentence = ('The saved allocation was ' + _text(run.get('cores')) + ' CPU cores and '
                + _text(run.get('memory_mb')) + ' MB RAM; this is the workflow allocation, not measured per-tool resource use.')
    if limits['swap_mb']:
        sentence += (f" An additional {limits['swap_mb']} MB disk-swap allowance was configured, "
                     f"giving a requested total scheduler budget of {limits['scheduler_mb']} MB. "
                     'The allowance does not establish actual swap consumption or increase physical RAM; '
                     'individual tool memory settings remain separately recorded. '
                     'The native workflow may further reduce the RAM portion; its resolved-settings record '
                     'and log identify the effective scheduler budget.')
    return sentence


def _incomplete(row, evidence):
    native = evidence.get('native_run', {}).get('incomplete_modules', [])
    return (row.get('complete') is False or row.get('scope_incomplete') or row.get('upstream_incomplete')
        or (row.get('module_decision') or {}).get('scope_incomplete')
        or (row.get('completion_summary') or {}).get('selected_execution') == 'incomplete'
        or (row.get('completion_summary') or {}).get('deferred_candidates')
        or row.get('module') in native
        or str(row.get('sample_id')) + ':' + str(row.get('module')) in native)


class _Narrator:
    def __init__(self, run, doc, cfg, known):
        self.run, self.doc, self.cfg, self.known = run, doc, cfg, known
        candidate = doc.get('evidence') or {}
        self.evidence = candidate if (doc.get('completed')
            and str(doc.get('evidence_origin', '')).startswith('Completion-time snapshot')
            and 'sample identity' in (candidate.get('identity') or {}).get('checks', [])) else {}
        self.decisions = self.evidence.get('decisions') or {}
        self.records = {r.get('module'): r for r in self.evidence.get('records', [])}
        self.software = {r['tool']: r for r in doc.get('software', [])}
        self.params = per_tool(run, cfg, known)
        self.used_params, self.used_versions, self.warnings = set(), set(), []
        self.configured_count = 0
        self.trace = (doc.get('runtime_evidence') or {}).get('rule_trace')
        self.jobs = {}
        if known and isinstance(self.trace, dict):
            for row in self.trace.get('jobs', []):
                tool = _rule_tool(run.get('tool'), row.get('rule', ''))
                if tool:
                    self.jobs.setdefault(tool, []).append(row)

    def label(self, tool):
        title = TASKS.get(tool, (tool,))[0]
        row = self.software.get(tool, {})
        if tool in self.used_versions:
            return title
        self.used_versions.add(tool)
        version, scope = row.get('version', UNKNOWN), row.get('scope', '')
        if not version or version == UNKNOWN:
            return title + ' (version not recorded)'
        if 'environment package' in scope.lower():
            qualifier = 'environment inventory only; invoked version unverified'
        elif any(word in scope.lower() for word in ('unverified', 'not established', 'must be verified')):
            qualifier = 'output metadata; execution attribution unverified'
        else:
            qualifier = scope or 'recorded metadata'
        return title + ' (version ' + str(version) + '; ' + qualifier + ')'

    def settings(self, tool):
        if tool in self.used_params:
            return ''
        self.used_params.add(tool)
        clauses = self.params.get(tool, [])
        return ' The parameter specification was ' + '; '.join(clauses).rstrip('.') + '.' if clauses else ''

    def status(self, tool):
        skipped = self.decisions.get('skipped_stages') or {}
        if tool in skipped:
            return 'skipped', _text(skipped[tool])
        row = self.records.get(tool)
        if row:
            if row.get('reused') is True or row.get('cache_hit') is True:
                return 'reused', _text(row.get('reason'))
            if row.get('execution_status') == 'completed' and _incomplete(row, self.evidence):
                return 'incomplete', _text(row.get('reason'))
            status = row.get('execution_status')
            return {'completed':'executed', 'failed':'failed', 'blocked':'blocked',
                    'disabled':'skipped', 'not_requested':'skipped',
                    'not_started':'not started', 'not_assessed':'not assessed'}.get(status, 'configured'), _text(row.get('reason'))
        stages = self.decisions.get('executed_stages') or []
        if tool in stages or tool == 'racon' and any(re.fullmatch(r'racon[1-9][0-9]*', s) for s in stages):
            return 'executed', ''
        if tool == self.decisions.get('assembler'):
            return 'assembler', ''
        return 'configured', ''

    def step(self, tool, action, rules=None):
        title = self.label(tool)
        status, reason = self.status(tool)
        if status == 'executed':
            sentence = 'The retained native record reports that ' + title + ' was used to ' + action + '.'
        elif status == 'assembler':
            sentence = 'The retained native record identifies ' + title + ' as the assembler. Its configured role was to ' + action + '.'
        elif status == 'reused':
            sentence = 'Existing ' + title + ' results were reused for the stage intended to ' + action + '; no new execution is attributed to this attempt.'
            if _incomplete(self.records.get(tool, {}), self.evidence):
                sentence += ' The reused result has incomplete recorded scope.'
        elif status in {'skipped', 'failed', 'blocked', 'incomplete', 'not started', 'not assessed'}:
            sentence = ('The retained native record marks the ' + title + ' stage, intended to ' + action + ', as '
                        + status + (': ' + reason if reason != UNKNOWN else '') + '.')
        else:
            prefixes = ('The saved workflow specified ', 'For this run, the configuration selected ', 'The processing specification included ')
            sentence = prefixes[self.configured_count % len(prefixes)] + title + ' to ' + action + '.'
            self.configured_count += 1
        jobs = [j for j in self.jobs.get(tool, []) if rules is None or j.get('rule') in rules]
        if jobs:
            # A conditional rule may complete by forwarding/creating empty files.
            # Keep scheduler outcome separate even when there is native evidence.
            outcomes = list(dict.fromkeys(_text(j.get('rule')) + ' (' + _text(j.get('status')) + ')' for j in jobs))
            sentence += ' The controller recorded ' + ', '.join(outcomes) + '.'
        return sentence + self.settings(tool)

    def intro(self):
        name = self.doc.get('pipeline') or {'hybas':'HybAs', 'basis':'BaSIS', 'micpscope':'GeneScope'}.get(self.run.get('tool'), 'Workflow')
        action = 'was used through Bactologix to coordinate the computational workflow' if self.doc.get('completed') or self.doc.get('runtime_evidence') or self.doc.get('stage_narrative') else 'was selected in Bactologix for the computational workflow'
        sentence = (name + ' ' + action + ' for sample ' + _text(self.run.get('sample', {}).get('id'))
                    + ' (run ' + _text(self.run.get('id')) + '; source commit ' + _text(self.run.get('commit')) + ').')
        sentence += ' ' + allocation(self.run, self.cfg)
        controller = self.software.get('snakemake', {})
        runtime = self.doc.get('runtime_evidence') or {}
        if runtime.get('versions', {}).get('snakemake'):
            sentence += ' Snakemake ' + _text(runtime['versions']['snakemake']) + ' orchestrated this recorded invocation.'
        elif controller.get('version', UNKNOWN) != UNKNOWN:
            sentence += ' Snakemake version metadata was ' + _text(controller['version']) + ' (' + _text(controller.get('scope')) + ').'
        else:
            sentence += ' The producing Snakemake version was not recorded.'
        return sentence

    def coverage(self):
        pieces = []
        if not self.doc.get('completed'):
            pieces.append('This attempt is not recorded as completed; the following is a configured Methods draft, with any recorded outcomes identified explicitly.')
        elif not self.evidence:
            pieces.append('Attributable native per-step evidence is unavailable; processing below describes the saved workflow, and does not assert that every configured tool ran.')
        else:
            pieces.append('Native execution, skip and reuse records are identified below; all other processing descriptions remain the saved workflow specification.')
        if isinstance(self.trace, dict):
            if not self.trace.get('jobs'):
                pieces.append('No rule events were recorded for this invocation; no earlier attempt is substituted and this does not establish that no analytical work occurred.')
            if self.trace.get('coverage') == 'partial' or self.trace.get('truncated'):
                pieces.append('The controller trace is partial; unrecorded steps and their order cannot be reconstructed.')
        pieces.append('Controller rule completion does not establish every internal tool invocation; conditional rules may forward existing results. Parameter values are saved settings or reviewed source arguments, as labelled; unrecorded internal tool defaults are not reconstructed.')
        return ' '.join(pieces)


def _hybas(n):
    cfg, run, decisions = n.cfg, n.run, n.decisions
    mode = run.get('sample', {}).get('mode')
    experimental = run.get('commit') in EXPERIMENTAL_HYBAS or bool(cfg.get('input_manifest'))
    if experimental:
        paragraphs = ['The experimental HybAs library workflow specified the saved input manifest and platform settings for route selection. Regular HybAs assembly and filtering defaults are not substituted for this source.']
        tools = list(dict.fromkeys([*n.jobs, decisions.get('assembler'),
            *('racon' if re.fullmatch(r'racon[1-9][0-9]*', s) else s for s in decisions.get('executed_stages', [])),
            *(decisions.get('skipped_stages') or {})]))
        for tool in tools:
            if tool in TASKS:
                paragraphs.append(n.step(tool, TASKS[tool][1].rstrip('.').lower()))
        paragraphs.append('These recorded stages are described individually; their ordering and dependencies are limited to the declared file relationships in the run diagram.')
        return paragraphs
    if mode not in {'hybrid', 'illumina_only', 'ont_only'}:
        n.warnings.append('The saved input mode has no reviewed narrative route; processing order was not inferred.')
        return ['The saved input mode was ' + _text(mode) + '; a source-matched processing route could not be established.']
    short, long = mode in {'hybrid', 'illumina_only'}, mode in {'hybrid', 'ont_only'}
    inputs = {'hybrid':'paired Illumina and Oxford Nanopore reads', 'illumina_only':'paired Illumina reads', 'ont_only':'Oxford Nanopore reads'}[mode]
    raw = ['The saved input route comprised ' + inputs + '.']
    if short:
        raw.append(n.step('fastqc', 'characterize the quality of the raw short reads without modifying their sequences'))
    if long:
        raw.append(n.step('nanoplot', 'summarize raw long-read quality and length distributions', rules={'nanoplot_raw'}))
    paragraphs = [' '.join(raw)]
    native_mode = decisions.get('run_mode')
    if native_mode and native_mode != mode:
        note = ('The native summary records input mode ' + _text(native_mode) + ', whereas the saved input route was ' +
                _text(mode) + '. The route described here is the saved specification; actual input consumption is unresolved.')
        paragraphs.insert(0, note)
        n.warnings.append(note)
    decision = str(cfg.get('kraken_decision_mode', '')).lower()
    if decision == 'off' and not _bool(cfg.get('run_kraken')):
        paragraphs.append('Taxonomic profiling with Kraken2 and taxonomic read selection were disabled in the saved workflow.')
    elif _bool(cfg.get('run_kraken')) and decision in {'manual', 'auto', 'aware'}:
        tax = [n.step('kraken2', 'classify the registered reads against the selected taxonomy database before preprocessing'),
               n.step('krakentools', 'apply the ' + decision + ' taxonomic read-selection policy to the per-platform classifications')]
        if decisions.get('kraken_decision'):
            tax.append('The retained selection decision was ' + _text(decisions['kraken_decision']) + '.')
        if decisions.get('kraken_selected_target_taxid'):
            tax.append('The recorded target was taxid ' + _text(decisions['kraken_selected_target_taxid']) +
                       (' (' + _text(decisions['kraken_selected_target_name']) + ')' if decisions.get('kraken_selected_target_name') else '') + '.')
        paragraphs.append(' '.join(tax))
    else:
        paragraphs.append('The saved taxonomy flags do not establish a consistent enabled or disabled route; no taxonomic processing is asserted.')
    preprocess = []
    if short:
        preprocess.append(n.step('fastp', 'filter and trim the selected short-read pairs before assembly'))
    if long:
        preprocess.append(n.step('filtlong', 'filter the selected long reads before assembly'))
        preprocess.append(n.step('nanoplot', 'summarize the filtered long-read quality and length distributions', rules={'nanoplot_filt'}))
    paragraphs.append(' '.join(preprocess))
    paragraphs.append(n.step('unicycler', 'construct the draft assembly from the processed ' + inputs))
    polish = str(cfg.get('polish_mode', 'none')).lower()
    polishing = ['The requested polishing mode was ' + polish + '.']
    rounds = cfg.get('racon_rounds', 0)
    if long and polish != 'none' and rounds:
        polishing.append(n.step('minimap2', 'align processed long reads to the assembly for the requested Racon consensus rounds'))
        polishing.append(n.step('racon', 'update the assembly consensus from the reads and corresponding alignments'))
    actual_rounds = list(dict.fromkeys(s for s in decisions.get('executed_stages', []) if re.fullmatch(r'racon[1-9][0-9]*', s)))
    if actual_rounds:
        polishing.append('The native record lists ' + str(len(actual_rounds)) + ' executed Racon stages (' + ', '.join(actual_rounds) + '); this count is distinct from the requested round count.')
    if long and polish in {'medaka', 'tripolish'}:
        polishing.append(n.step('medaka', 'apply model-based consensus polishing to the preceding assembly stage'))
        if 'medaka' in decisions.get('executed_stages', []):
            polishing.append('The recorded effective Medaka model was ' + _text(decisions.get('medaka_model_effective')) +
                             '; the requested model setting is retained separately.')
    if short and polish == 'tripolish' and _bool(cfg.get('run_polypolish')):
        polishing.append(n.step('bwa', 'index the preceding assembly and align the processed short reads for Polypolish', rules={'bwa_index', 'polypolish_align'}))
        polishing.append(n.step('polypolish', 'update the consensus using those short-read alignments'))
        polishing.append('A separate configured mapping-quality branch used paired-end BWA alignments for reporting; these are distinct from the per-mate alignments supplied to Polypolish.')
        qc_jobs = [row for row in n.jobs.get('bwa', []) if row.get('rule') == 'illumina_map']
        if qc_jobs:
            polishing.append('For that mapping-quality branch, the controller recorded ' + ', '.join('illumina_map (' + _text(row.get('status')) + ')' for row in qc_jobs) + '.')
        polishing.append(n.step('samtools', 'index assembly sequence, sort and index the mapping-quality alignments, and count alignments for reporting and the Polypolish mapped-alignment guard', rules={'bwa_index', 'illumina_map', 'polypolish'}))
    if polish == 'none':
        polishing.append('No polishing stages were requested.')
    paragraphs.append(' '.join(polishing))
    final = 'The saved workflow selected and copied the final assembly from the available assembly or polishing stages.'
    if decisions.get('final_source_stage'):
        final += ' The retained record identifies ' + _text(decisions['final_source_stage']) + ' as the source of the final assembly; skipped stages are not presented as its producer.'
    paragraphs.append(final)
    assessment = [n.step('seqkit', 'summarize sequence counts and lengths for the available read and assembly files'), n.step('quast', 'calculate assembly statistics from the final FASTA')]
    if _bool(cfg.get('run_busco')):
        assessment.append(n.step('busco', 'assess marker completeness and duplication against the selected lineage dataset'))
    if _bool(cfg.get('run_prokka')):
        assessment.append(n.step('prokka', 'predict and annotate genomic features and export their coordinates, nucleotide sequences and protein sequences'))
    if _bool(cfg.get('run_multiqc')):
        assessment.append(n.step('multiqc', 'aggregate the available quality-control reports'))
    assessment.append('Quality summaries and annotations describe the resulting assembly; they do not by themselves establish biological function or deployment suitability.')
    paragraphs.append(' '.join(assessment))
    return paragraphs


def _basis(n):
    cfg, run = n.cfg, n.run
    followup = run.get('run_type') == 'followup'
    paragraphs = []
    if followup:
        paragraphs.append('The BaSIS follow-up was configured from retained screening evidence associated with parent run ' + _text(run.get('parent')) +
                          '. The screening outputs are inherited inputs; the screening tools are not attributed as newly executed by this follow-up.')
    else:
        paragraphs.append('The saved BaSIS workflow supplied assembly and available annotation inputs to the selected assessment modules. These modules form separate evidence branches; they are not a serial chain of analyses.')
        exclusions = (cfg.get('sample_module_overrides') or {}).get(run.get('sample', {}).get('id'), {})
        selected = []
        for tool, options in (cfg.get('modules') or {}).items():
            if (not isinstance(options, dict) or not _bool(options.get('enabled')) or tool in exclusions
                    or tool == 'pathogenfinder2' and not _bool(cfg.get('extend', True))):
                continue
            if tool in TASKS:
                selected.append(n.step(tool, TASKS[tool][1].rstrip('.').lower()))
            else:
                selected.append('The saved configuration enabled module ' + str(tool) + '; its operation has no reviewed narrative description.')
        paragraphs.extend(selected or ['No assessment module was selected in the accepted configuration.'])
        if exclusions:
            paragraphs.append('Sample-specific exclusions were recorded for ' + ', '.join(map(str, exclusions)) + '.')
        paragraphs.append(n.step('normalize', 'convert available module outputs to the common evidence schema while retaining module status and provenance') + ' ' +
                          n.step('report', 'assemble the screening report from that evidence'))
    deep = cfg.get('deep') or {}
    if followup or _bool(deep.get('enabled') if isinstance(deep, dict) else deep):
        paragraphs.append(n.step('deepsafe', 'review selected candidates using the saved screening evidence and configured follow-up stages') +
                          ' The follow-up report is separate from the screening report; aggregate completion does not establish that every internal stage completed.')
    else:
        paragraphs.append('DeepSafe follow-up was disabled in the saved configuration.')
    paragraphs.append('Module outputs are computational evidence within their recorded scope and reference coverage; absent matches or a completed workflow are not evidence of deployment safety.')
    return paragraphs


def _genescope(n):
    paragraphs = ['The saved GeneScope workflow specified the registered protein FASTA as its primary analysis input.']
    sidecars = [str(row.get('path', '')).replace('\\', '/').rsplit('/', 1)[-1]
                for row in n.run.get('inputs', []) if str(row.get('path', '')).endswith(('.gff', '.fna', '.interpro.tsv'))]
    if sidecars:
        paragraphs[0] += ' Recorded supplementary inputs were ' + ', '.join(sidecars) + '; their availability does not establish unrecorded consumption by every stage.'
    if n.cfg.get('reference_database'):
        paragraphs.append(n.step('diamond', 'compare the protein sequences with the selected application reference database and supply similarity candidates to the evidence analysis'))
    else:
        paragraphs.append('No application-database DIAMOND search was selected in the saved configuration.')
    descriptors = ('calculate sequence-derived protein descriptors and combine the available annotation and reference evidence')
    if n.run.get('commit') in GENESCOPE:
        descriptors = ('calculate molecular weight, theoretical isoelectric point (pI), mean hydropathy (GRAVY), '
                       'instability index, aromaticity and amino-acid counts, and combine the available annotation and reference evidence')
    paragraph = n.step('biopython', descriptors)
    paragraph += ' The source implementation specifies a separate GeneScope calculation for the aliphatic index alongside Biopython-derived descriptors.'
    if n.run.get('commit') in GENESCOPE:
        paragraph += (' Its normalization policy removes one terminal stop character before calculation; sequences containing '
                      'unsupported or ambiguous residues, including any remaining stop character, or empty normalized sequences '
                      'are retained with quality flags and a not-calculated status rather than assigned descriptor values.')
    paragraphs.append(paragraph)
    paragraphs.append(n.step('report', 'render the source-labelled GeneScope evidence and protein descriptor results'))
    paragraphs.append('Similarity candidates do not establish function, and sequence descriptors do not determine pH or temperature optima, shelf life, material performance or deployment safety.')
    return paragraphs


def build(run, document, cfg, known_source=False):
    """Return manuscript-style prose; the caller supplies already verified inputs."""
    n = _Narrator(run, document, cfg, known_source)
    stage = document.get('stage_narrative') or []
    if stage and document.get('completed_stage') in {'aware', 'identification'}:
        # These paragraphs come from the Dev caller's checksummed stage receipt.
        # A stopped stage must never acquire assembly/polishing prose from config.
        paragraphs = [n.intro(), *stage]
        if known_source:
            settings = n.settings('kraken2')
            if document['completed_stage'] == 'aware':
                settings += n.settings('krakentools')
            if settings:
                paragraphs.append('The saved taxonomy parameter specification remains separate from the recorded stage outcomes.' + settings)
        return {'paragraphs': paragraphs, 'status': document.get('state', 'Recorded stage Methods draft'), 'warnings': []}
    paragraphs = [n.intro(), n.coverage()]
    if not known_source:
        paragraphs.append('A source-matched workflow description is unavailable. The explicit saved settings and any raw controller rule records are retained in the full export supplement; tool identities, processing order and source-defined thresholds are not inferred.')
    else:
        handler = {'hybas':_hybas, 'basis':_basis, 'micpscope':_genescope}.get(run.get('tool'))
        if handler:
            paragraphs.extend(handler(n))
        paragraphs.append('Other saved settings, including values for disabled or conditional stages, are retained in the full export settings supplement.')
    paragraphs.extend(stage)
    variant = run.get('variant')
    if variant:
        paragraphs.append('This variant derives from run ' + _text(variant.get('parent')) +
                          '; retained checkpoints may supply existing results. Parent processing and resource use are not attributed as new execution in this attempt.')
    transition = run.get('followup_transition')
    if transition:
        paragraphs.append('The saved screening source was ' + _text(transition.get('screening_commit')) +
                          ', and the selected follow-up source was ' + _text(transition.get('commit')) + '.')
    status = 'Run-specific Methods draft' if document.get('completed') else 'Configured Methods draft — execution incomplete'
    return {'paragraphs':paragraphs, 'status':status, 'warnings':n.warnings}
