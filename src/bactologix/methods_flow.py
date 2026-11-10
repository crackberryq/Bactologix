"""Run dataflow from saved settings or bound controller declarations, never probes."""
from collections import defaultdict, deque
import hashlib
import html
import re
import textwrap

CAPTION = ('This composite workflow is not scientifically validated. Configured dependencies and '
           'controller-declared file paths do not establish observed file transfers, internal tool execution or result correctness.')
CONFIGURED = 'Configured; execution not established'
TASKS = {
 'fastp': ('fastp', 'Filter and trim short-read sequences.', 'Prepare short reads for assembly.'),
 'fastqc': ('FastQC', 'Summarize short-read quality.', 'Expose input-quality characteristics; reports do not modify reads.'),
 'filtlong': ('Filtlong', 'Select long reads using saved filtering settings.', 'Prepare long-read inputs for assembly and polishing.'),
 'nanoplot': ('NanoPlot', 'Summarize long-read lengths and quality.', 'Document read characteristics without changing sequences.'),
 'kraken2': ('Kraken2', 'Classify reads against the selected taxonomy database.', 'Provide evidence for the separate read-selection decision.'),
 'krakentools': ('Read selection / KrakenTools', 'Apply the configured or recorded taxonomy-selection policy.', 'Select downstream inputs; classification and selection remain distinct.'),
 'unicycler': ('Unicycler', 'Construct an assembly from processed reads.', 'Produce an assembly for subsequent polishing or assessment.'),
 'spades': ('SPAdes', 'Construct an assembly from configured library inputs.', 'Produce an assembly for the experimental library route.'),
 'flye': ('Flye', 'Construct an assembly from configured long reads.', 'Produce an assembly for the experimental long-read route.'),
 'minimap2': ('Minimap2', 'Align long reads to the assembly.', 'Provide alignment evidence for consensus polishing.'),
 'racon': ('Racon', 'Compute consensus updates from reads and alignments.', 'Apply requested polishing rounds; improvement is not assumed.'),
 'medaka': ('Medaka', 'Apply the selected model to consensus polishing.', 'Produce the configured consensus; model suitability requires review.'),
 'bwa': ('BWA', 'Index an assembly and align short reads.', 'Provide short-read alignments to Polypolish.'),
 'samtools': ('SAMtools', 'Index sequence files and process alignment files and counts.', 'Prepare mapping QC and distinguish mapped alignments from an empty polishing input.'),
 'polypolish': ('Polypolish', 'Update assembly consensus using short-read alignments.', 'Apply configured polishing; correctness needs independent assessment.'),
 'quast': ('QUAST', 'Calculate assembly quality statistics.', 'Describe assembly characteristics without certifying biological accuracy.'),
 'busco': ('BUSCO', 'Assess markers against the selected lineage dataset.', 'Estimate marker completeness and duplication within dataset limits.'),
 'prokka': ('Prokka', 'Annotate assembly features and export gene/protein files.', 'Provide predicted coordinates and sequences for downstream analyses.'),
 'multiqc': ('MultiQC', 'Aggregate available quality reports.', 'Collect summaries without repeating underlying analyses.'),
 'seqkit': ('SeqKit', 'Summarize sequence counts and lengths.', 'Record basic sequence-file characteristics.'),
 'diamond': ('DIAMOND', 'Compare proteins with selected reference sequences.', 'Identify similarity candidates; similarity alone does not establish function.'),
 'biopython': ('Biopython / GeneScope analysis', 'Calculate protein descriptors and integrate available annotation/reference evidence.', 'Describe sequence-derived properties, not environmental optima or deployment performance.'),
 'amrfinder': ('AMRFinderPlus', 'Compare sequences with curated resistance-associated references.', 'Report contextual evidence; absent matches do not certify safety.'),
 'rgi': ('CARD / RGI', 'Evaluate sequences against CARD detection models.', 'Report model-supported candidates and evidence categories.'),
 'vfdb': ('DIAMOND (VFDB search)', 'Search proteins against configured VFDB references.', 'Collect similarity evidence; database identity is separate from tool version.'),
 'bacmet': ('DIAMOND (BacMet search)', 'Search proteins against configured BacMet references.', 'Collect similarity evidence without asserting a demonstrated phenotype.'),
 'antismash': ('antiSMASH', 'Predict biosynthetic gene-cluster regions.', 'Describe candidate genomic regions for review.'),
 'genomad': ('geNomad', 'Classify candidate mobile-element regions.', 'Provide genomic context for other evidence.'),
 'mobsuite': ('MOB-suite', 'Characterize candidate plasmid-associated context.', 'Provide mobile-element context for review.'),
 'integronfinder': ('IntegronFinder', 'Identify candidate integron-associated regions.', 'Add genomic-context evidence.'),
 'isescan': ('ISEScan', 'Detect candidate insertion-sequence regions.', 'Add insertion-sequence context.'),
 'icescreen': ('ICEscreen', 'Screen for integrative/conjugative-element context.', 'Report context within tool and database applicability limits.'),
 'crisprcasfinder': ('CRISPRCasFinder', 'Identify candidate CRISPR arrays and associated features.', 'Describe corresponding genomic context.'),
 'minced': ('MinCED', 'Identify candidate CRISPR repeat arrays.', 'Provide repeat-array evidence for review.'),
 'mlst': ('MLST', 'Compare loci with the selected typing scheme.', 'Report scheme-dependent typing where applicable.'),
 'pathogenfinder2': ('PathogenFinder2', 'Generate protein embeddings and model classifications.', 'Record model output within its applicability limits, not a safety determination.'),
 'deepsafe': ('DeepSafe composite follow-up', 'Review selected candidates using saved screening evidence and configured stages.', 'Provide additional evidence; aggregate completion does not prove all internal tools ran.'),
 'normalize': ('BaSIS evidence normalization', 'Convert module outputs into the common evidence schema.', 'Preserve status, scope and provenance for reporting.'),
 'report': ('Report renderer', 'Render supplied analysis records into a report.', 'Present evidence and limitations without validating the workflow.'),
}
STATUS = {'configured': CONFIGURED, 'unverified': 'Attribution unverified',
 'reported_executed': 'Native summary reports execution', 'reported_completed': 'Native module reports completion',
 'reported_incomplete': 'Native module reports incomplete scope', 'reused': 'Reused result; no new execution established',
 'skipped': 'Native record reports skipped', 'failed': 'Native record reports failure', 'blocked': 'Native record reports blocked',
 'recorded': 'Recorded evidence', 'declared': 'Scheduler-declared file paths',
 'rule_completed': 'Controller reports rule completed', 'rule_failed': 'Controller reports rule failed',
 'rule_reported': 'Controller reported rule; completion unknown'}

def _text(value):
    return '' if value is None else str(value)

def _bool(value):
    return value is True or isinstance(value, str) and value.strip().lower() in {'true', 'yes', '1', 'on'}

def _basename(value):
    return _text(value).replace('\\', '/').rstrip('/').rsplit('/', 1)[-1]

class _Builder:
    def __init__(self, run, doc, known):
        self.run, self.doc, self.known = run, doc, known
        self.nodes, self.edges, self.warnings, self.lookup = [], [], [], {}
        self.software = {r['tool']: r for r in doc.get('software', [])}
        evidence = doc.get('evidence') or {}
        self.evidence = evidence if (_text(doc.get('evidence_origin')).startswith('Completion-time snapshot')
            and 'sample identity' in (evidence.get('identity') or {}).get('checks', [])) else {}
        self.records = {r.get('module'): r for r in self.evidence.get('records', [])}

    def add(self, key, label, kind='data', **extra):
        node = dict(id=key, kind=kind, label=label, what='', why='', status='configured',
                    status_label=CONFIGURED, evidence='Saved settings and pinned workflow; execution not established', inputs=[], outputs=[])
        node.update(extra)
        self.nodes.append(node)
        self.lookup[key] = node
        return key

    def edge(self, source, target, label='', kind='configured_dependency', **extra):
        if source == target or source not in self.lookup or target not in self.lookup:
            return
        self.edges.append(dict(source=source, target=target, kind=kind, label=label or
             ('Configured dependency' if kind == 'configured_dependency' else 'Scheduler-declared matching paths'), **extra))
        self.lookup[target]['inputs'].append(self.lookup[source]['label'])
        self.lookup[source]['outputs'].append(self.lookup[target]['label'])

    def tool(self, key, tool, label=None):
        title, what, why = TASKS[tool]
        software, row = self.software.get(tool, {}), self.records.get(tool)
        status, evidence = 'configured', 'Saved settings and pinned workflow; execution not established'
        if row:
            execution = row.get('execution_status')
            if row.get('reused') is True or row.get('cache_hit') is True:
                status = 'reused'
            elif execution == 'completed':
                incomplete = (row.get('complete') is False or row.get('scope_incomplete') or row.get('upstream_incomplete')
                    or (row.get('module_decision') or {}).get('scope_incomplete')
                    or (row.get('completion_summary') or {}).get('selected_execution') == 'incomplete'
                    or (row.get('completion_summary') or {}).get('deferred_candidates')
                    or tool in self.evidence.get('native_run', {}).get('incomplete_modules', [])
                    or str(row.get('sample_id')) + ':' + tool in self.evidence.get('native_run', {}).get('incomplete_modules', []))
                status = 'reported_incomplete' if incomplete else 'reported_completed'
            else:
                status = {'failed':'failed', 'blocked':'blocked', 'disabled':'skipped', 'not_requested':'skipped'}.get(execution, 'unverified')
            evidence = 'Retained native module record: ' + _text(execution) + ('; ' + _text(row['reason']) if row.get('reason') else '')
        decisions = self.evidence.get('decisions', {})
        if tool in decisions.get('skipped_stages', {}):
            status, evidence = 'skipped', 'Retained native skip record: ' + _text(decisions['skipped_stages'][tool])
        elif tool in decisions.get('executed_stages', []) or (tool == 'racon' and any(
                re.fullmatch(r'racon[1-9][0-9]*', str(stage)) for stage in decisions.get('executed_stages', []))):
            status, evidence = 'reported_executed', 'Retained native polishing summary; individual command I/O not recorded'
            if tool == 'racon':
                evidence += '; recorded stages: ' + ', '.join(str(stage) for stage in decisions.get('executed_stages', [])
                    if re.fullmatch(r'racon[1-9][0-9]*', str(stage)))
        return self.add(key, label or title, 'tool', tool=tool, what=what, why=why, status=status,
            status_label=STATUS[status], evidence=evidence, version=software.get('version', 'Not recorded'),
            version_scope=software.get('scope', 'Version not recorded'))

    def step(self, key, tool, sources, output, label=None):
        node = self.tool(key, tool, label)
        for source in sources:
            self.edge(source, node)
        data = self.add(key + '-output', output)
        self.edge(node, data)
        return data

def _unresolved(b, label):
    source = b.add('saved-inputs', 'Saved input records')
    middle = b.add('unresolved-workflow', label, kind='unresolved', status='unverified',
                   status_label=STATUS['unverified'], evidence='No accepted source-specific dependency template')
    output = b.add('unresolved-results', 'Result association / producing steps not established',
                   status='unverified', status_label=STATUS['unverified'])
    b.edge(source, middle, 'Unresolved workflow relationship', 'unresolved')
    b.edge(middle, output, 'Unresolved workflow relationship', 'unresolved')

def _genescope(b, cfg):
    proteins = b.add('proteins', 'Saved protein FASTA input')
    inputs = [proteins]
    # Never inspect today's adjacent files to infer historical sidecar consumption.
    for i, row in enumerate(b.run.get('inputs', [])):
        name = _basename(row.get('path', ''))
        if name.endswith(('.gff', '.interpro.tsv', '.fna')):
            inputs.append(b.add('sidecar-' + str(i), 'Recorded sidecar: ' + name))
    if b.run.get('sample', {}).get('evidence'):
        inputs.append(b.add('supplied-evidence', 'Saved supplied evidence input'))
    if cfg.get('reference_database'):
        database = b.add('reference-database', 'Selected application reference database')
        reference = b.step('reference-search', 'diamond', [proteins, database],
                           'reference-search.json / reference-search.hits.tsv')
        inputs.append(reference)
    results = b.step('protein-properties', 'biopython', inputs, 'results.json / proteins.tsv')
    b.step('genescope-report', 'report', [results], 'GeneScope report.html', 'GeneScope report renderer')

def _hybas(b, cfg):
    mode = b.run.get('sample', {}).get('mode')
    if b.run.get('commit') in {'08c315479f5493ce0b1f44ba26da3c7f2e0d748b',
                               '79c5b7f342e02bd5b60bf7847fc5f4fb441e1fd4',
                               '0d334449d69fe2208b79affad2f5162ac801be05'} or cfg.get('input_manifest'):
        b.warnings.append('Experimental library topology is not inferred from filenames or regular HybAs defaults. A bound controller trace is required for its rule/file diagram.')
        _unresolved(b, 'Experimental HybAs dependencies not recorded')
        for tool in [b.evidence.get('decisions', {}).get('assembler'), *b.evidence.get('decisions', {}).get('executed_stages', [])]:
            if tool in TASKS and 'native-' + tool not in b.lookup:
                node = b.tool('native-' + tool, tool)
                b.lookup[node]['evidence'] += '; dependency links unavailable'
        return
    if mode not in {'hybrid', 'ont_only', 'illumina_only'}:
        b.warnings.append('Input mode is outside the reviewed topology; tool order is not inferred.')
        _unresolved(b, 'Input route is unresolved')
        return
    raw, processed, reports = [], [], []
    short, long = mode in {'hybrid', 'illumina_only'}, mode in {'hybrid', 'ont_only'}
    if short:
        raw.append(b.add('short-reads', 'Illumina reads (registered files merged by workflow)'))
        reports.append(b.step('short-qc', 'fastqc', [raw[-1]], 'FastQC quality reports'))
    if long:
        raw.append(b.add('long-reads', 'ONT reads (registered files merged by workflow)'))
        reports.append(b.step('long-qc', 'nanoplot', [raw[-1]], 'Raw long-read quality reports'))
    selected = {key: key for key in raw}
    if _bool(cfg.get('run_kraken')) and str(cfg.get('kraken_decision_mode', '')).lower() != 'off':
        database = b.add('taxonomy-database', 'Selected Kraken2 taxonomy database')
        profiles = b.step('taxonomy', 'kraken2', [*raw, database], 'Per-platform taxonomy profiles')
        selection = b.tool('read-selection', 'krakentools')
        for key in [*raw, profiles]:
            b.edge(key, selection)
        for key in list(selected):
            value = b.add('selected-' + key, 'Selected Illumina reads' if key == 'short-reads' else 'Selected ONT reads')
            b.edge(selection, value)
            selected[key] = value
    if short:
        processed.append(b.step('short-preprocessing', 'fastp', [selected['short-reads']], 'Processed Illumina read pairs'))
    if long:
        processed.append(b.step('long-preprocessing', 'filtlong', [selected['long-reads']], 'Filtered ONT reads'))
        reports.append(b.step('filtered-long-qc', 'nanoplot', [processed[-1]], 'Filtered long-read quality reports'))
    assembly = b.step('assembly', 'unicycler', processed, 'Draft assembly FASTA')
    polish = str(cfg.get('polish_mode', 'none')).lower()
    if long and polish != 'none' and cfg.get('racon_rounds', 0):
        mapped = b.step('long-alignments', 'minimap2', [assembly, processed[-1]], 'Long-read alignments for configured Racon rounds')
        assembly = b.step('racon', 'racon', [assembly, processed[-1], mapped], 'Racon-stage assembly',
                          'Racon (' + _text(cfg.get('racon_rounds')) + ' configured rounds)')
    if long and polish in {'medaka', 'tripolish'}:
        assembly = b.step('medaka', 'medaka', [assembly, processed[-1]], 'Medaka-stage assembly')
    if short and polish == 'tripolish' and _bool(cfg.get('run_polypolish')):
        mapped = b.step('short-alignments', 'bwa', [assembly, processed[0]], 'Short-read alignments for Polypolish')
        paired = b.step('paired-mapping-qc', 'bwa', [assembly, processed[0]], 'Paired short-read QC alignments', 'BWA · separate paired mapping QC')
        b.lookup['paired-mapping-qc'].update(what='Align paired short reads to the pre-Polypolish assembly for mapping QC.',
            why='Provide a separate reporting branch; Polypolish uses the separate-mate alignments.')
        reports.append(b.step('alignment-qc', 'samtools', [paired], 'Sorted/indexed alignment files and QC counts'))
        assembly = b.step('polypolish', 'polypolish', [assembly, mapped], 'Polypolish-stage assembly')
    final = b.add('final-assembly', 'Final assembly selection / copied FASTA')
    b.edge(assembly, final)
    reports.append(b.step('sequence-statistics', 'seqkit', list(dict.fromkeys([*raw, *selected.values(), *processed, final])),
                          'Read-stage and assembly sequence statistics'))
    reports.append(b.step('quast', 'quast', [final], 'QUAST assembly statistics'))
    if _bool(cfg.get('run_busco')):
        lineage = b.add('busco-lineage', 'Selected BUSCO lineage dataset')
        reports.append(b.step('busco', 'busco', [final, lineage], 'BUSCO completeness summary'))
    if _bool(cfg.get('run_multiqc')):
        reports.append(b.step('multiqc', 'multiqc', reports[:], 'Aggregated QC report'))
    if _bool(cfg.get('run_prokka')):
        reports.append(b.step('prokka', 'prokka', [final], 'Prokka annotation / GFF / protein FASTA'))
    if b.run.get('commit') in {'a99e00b041a184f36cfdf8a2ab5b61f0f255a02d',
                               '87c1ab81c8652bd10db47954193e27ad802cfc86',
                               '585abb34ed4401da64b37a9fd629ba8adc236396',
                               '5db662262962bc822f2fe501234d15b882d5b042'}:
        b.step('hybas-report', 'report', [final, *reports], 'HybAs report and retained metadata', 'HybAs report renderer')
    b.warnings.append('Polishing arrows show configured dependencies, not a claim every requested stage ran. Native skip/reuse records are labelled separately.')

def _basis(b, cfg):
    followup = b.run.get('run_type') == 'followup'
    if followup:
        source = b.add('screening-evidence', 'Retained parent screening evidence', status='reused',
                       status_label=STATUS['reused'], evidence='Saved parent run ' + _text(b.run.get('parent')))
        records = [source]
    else:
        source = b.add('annotated-inputs', 'Saved assembly / annotation inputs (FASTA, FAA, GFF, GenBank)')
        exclusions = cfg.get('sample_module_overrides', {}).get(b.run.get('sample', {}).get('id'), {})
        records = []
        for tool, options in cfg.get('modules', {}).items():
            if tool not in TASKS or not isinstance(options, dict) or not _bool(options.get('enabled')) or tool in exclusions:
                continue
            if tool == 'pathogenfinder2' and not _bool(cfg.get('extend', True)):
                continue
            raw = b.step(tool, tool, [source], tool + ' native outputs')
            records.append(b.step(tool + '-normalization', 'normalize', [raw], tool + ' normalized evidence'))
        if not records:
            b.warnings.append('No screening module is enabled in the accepted saved settings.')
        b.step('screening-report', 'report', records or [source], 'Screening report.html / evidence.json', 'BaSIS screening report renderer')
    deep_cfg = cfg.get('deep') or {}
    if followup or _bool(deep_cfg.get('enabled') if isinstance(deep_cfg, dict) else deep_cfg):
        deep = b.step('deepsafe', 'deepsafe', records, 'DeepSafe follow-up evidence')
        b.step('followup-report', 'report', [*records, deep], 'Separate DeepSafe follow-up report', 'BaSIS follow-up report renderer')

def _rule_tool(pipeline, rule):
    if pipeline == 'micpscope':
        return {'protein_properties':'biopython', 'reference_search':'diamond', 'report':'report'}.get(rule)
    if pipeline == 'basis':
        if rule in {'followup_reports', 'safety_report', 'safety_deep_report', 'report'}:
            return 'report'
        if rule in TASKS:
            return rule
        if rule in {'normalize_native', 'normalize_extend'}:
            return 'normalize'
        if rule.startswith('pathogenfinder2_'):
            return 'pathogenfinder2'
        if rule == 'crisprcasfinder_assembly':
            return 'crisprcasfinder'
        if rule.startswith('safety_deep_'):
            return 'deepsafe'
        return 'report' if rule in {'followup_reports', 'safety_report', 'report'} else None
    if pipeline == 'hybas':
        return {'fastqc_pre':'fastqc', 'nanoplot_raw':'nanoplot', 'nanoplot_filt':'nanoplot',
            'nanoplot_pacbio':'nanoplot', 'kraken2_illumina':'kraken2', 'kraken2_nanopore':'kraken2',
            'kraken2_pacbio':'kraken2', 'clean_illumina':'krakentools', 'clean_ont':'krakentools',
            'clean_pacbio':'krakentools', 'fastp_trim':'fastp', 'fastp_libraries':'fastp',
            'filtlong_ont':'filtlong', 'unicycler':'unicycler', 'spades_libraries':'spades',
            'flye_pacbio':'flye', 'racon_map':'minimap2', 'racon_consensus':'racon', 'medaka':'medaka',
            'bwa_index':'bwa', 'polypolish_align':'bwa', 'illumina_map':'bwa', 'polypolish':'polypolish',
            'quast':'quast', 'busco':'busco', 'prokka':'prokka', 'seqkit_stats':'seqkit',
            'multiqc':'multiqc', 'hybas_report':'report', 'computational_report':'report', 'research_report':'report'}.get(rule)
    return None

def _trace(b, trace):
    jobs, seen = [], set()
    for row in trace.get('jobs', [])[:128]:
        if (not isinstance(row, dict) or not isinstance(row.get('id'), str) or
            not isinstance(row.get('rule'), str) or not row['rule'] or
            not all(isinstance(row.get(k, []), list) and all(isinstance(p, str) for p in row.get(k, [])) for k in ('inputs', 'outputs'))):
            b.warnings.append('A malformed controller job was excluded from the diagram.')
            continue
        identity = row['id']
        if identity in seen:
            b.warnings.append('A duplicate controller job identity was excluded from the diagram.')
            continue
        seen.add(identity)
        key = 'rule-' + hashlib.sha256(identity.encode()).hexdigest()[:16]
        tool = _rule_tool(b.run.get('tool'), row['rule']) if b.known else None
        if tool:
            b.tool(key, tool, TASKS[tool][0] + ' · ' + row['rule'])
        else:
            b.add(key, 'Rule: ' + row['rule'], kind='tool', tool='',
                what='The controller reported this workflow rule; a tool-level description is unavailable.',
                why='Retain the recorded step without guessing its internal executables.',
                version='Not recorded', version_scope='Rule identity is not a software version')
        node = b.lookup[key]
        node['rule'], node['job_id'] = row['rule'], identity
        node['declared_inputs'], node['declared_outputs'] = row.get('inputs', [])[:32], row.get('outputs', [])[:32]
        status = {'completed':'rule_completed', 'failed':'rule_failed'}.get(row.get('status'), 'rule_reported')
        if node['status'] not in {'reused', 'skipped', 'reported_incomplete', 'failed', 'blocked'}:
            node['status'], node['status_label'] = status, STATUS[status]
        node['evidence'] += '; bound controller record: ' + _text(row.get('status')) + '. Rule completion does not prove every internal tool executed.'
        jobs.append((key, row))
    _trace_edges(b, trace, jobs)

def _trace_edges(b, trace, jobs):
    producers, grouped = defaultdict(list), defaultdict(list)
    for index, (key, row) in enumerate(jobs):
        for path in row.get('outputs', [])[:32]:
            producers[path].append((index, key))
    consumed = set()
    for index, (target, row) in enumerate(jobs):
        external = []
        for path in row.get('inputs', [])[:32]:
            options = producers.get(path, [])
            if len(options) == 1 and options[0][0] < index:
                grouped[options[0][1], target].append(path)
                consumed.add(path)
            else:
                external.append(path)
                if options:
                    b.warnings.append('Some matching paths have ambiguous producers or declaration order; their dependency is not inferred.')
        if external:
            key = target + '-inputs'
            label = 'Inputs/resources: ' + ', '.join(_basename(p) for p in external[:2]) + (' …' if len(external) > 2 else '')
            b.add(key, label, status='declared',
                status_label=STATUS['declared'], paths=external,
                evidence='Declared for this rule; may include existing/reused files and workflow code')
            b.edge(key, target, ', '.join(_basename(p) for p in external[:4]), 'scheduler_declared', paths=external)
    for (source, target), paths in grouped.items():
        b.edge(source, target, ', '.join(_basename(p) for p in paths[:4]), 'scheduler_declared', paths=paths)
    for key, row in jobs:
        outputs = [p for p in row.get('outputs', [])[:32] if p not in consumed]
        if outputs:
            label = 'Outputs: ' + ', '.join(_basename(p) for p in outputs[:2]) + (' …' if len(outputs) > 2 else '')
            data = b.add(key + '-outputs', label, status='declared',
                status_label=STATUS['declared'], paths=outputs,
                evidence='Declared outputs; content and existence are not rechecked at export')
            b.edge(key, data, ', '.join(_basename(p) for p in outputs[:4]), 'scheduler_declared', paths=outputs)
    b.warnings.extend(_text(w) for w in trace.get('warnings', []))
    if trace.get('truncated') or len(trace.get('jobs', [])) > 128:
        b.warnings.append('The controller trace or diagram reached its size limit; this is a partial graph.')
    b.warnings.append('Scheduler-declared I/O is not observed file transfer or proof an internal tool ran. Existing/reused files may appear as external inputs.')
    if not jobs:
        b.add('no-reported-jobs', 'No rule events recorded for this invocation', kind='unresolved',
            status='unverified', status_label='No rule trace events',
            evidence='A no-op, early exit or unavailable log events may explain this; absence of analytical work is not established.')

def _levels(nodes, edges):
    indegree, children, levels = {n['id']:0 for n in nodes}, defaultdict(list), {}
    for edge in edges:
        indegree[edge['target']] += 1
        children[edge['source']].append(edge['target'])
    pending = deque(key for key, degree in indegree.items() if degree == 0)
    while pending:
        key = pending.popleft()
        levels.setdefault(key, 0)
        for child in children[key]:
            levels[child] = max(levels.get(child, 0), levels[key] + 1)
            indegree[child] -= 1
            if indegree[child] == 0:
                pending.append(child)
    if len(levels) != len(nodes):
        raise ValueError('Run dataflow contains a cycle')
    return levels

def build(run, document, effective_config, *, known_source=False):
    """Caller verifies source/configuration and any controller receipt identity."""
    b = _Builder(run, document, known_source)
    controller = b.software.get('snakemake', {})
    recorded = bool(document.get('runtime_evidence'))
    b.add('controller', 'Snakemake workflow controller', kind='controller', tool='snakemake',
        what='Schedule workflow dependencies and manage rule execution.',
        why='Coordinate the workflow; controller success does not validate its science.',
        version=controller.get('version', 'Not recorded'), version_scope=controller.get('scope', 'Not recorded'),
        status='recorded' if recorded else 'unverified', status_label=STATUS['recorded' if recorded else 'unverified'],
        evidence=controller.get('scope', 'The producing controller version was not recorded'))
    trace = (document.get('runtime_evidence') or {}).get('rule_trace')
    traced = isinstance(trace, dict) and isinstance(trace.get('jobs'), list)
    if traced:
        _trace(b, trace)
    elif not known_source:
        _unresolved(b, 'Workflow source / tool dependencies unverified')
        b.warnings.append('No accepted source-specific template is available; tool dependencies are not guessed.')
    elif run.get('tool') in {'hybas', 'basis', 'micpscope'}:
        {'hybas':_hybas, 'basis':_basis, 'micpscope':_genescope}[run['tool']](b, effective_config)
    else:
        _unresolved(b, 'Unsupported workflow topology')
    if not traced:
        b.warnings.append('No bound per-rule controller trace is available. This diagram describes saved settings and reviewed source, with attributable native status labels.')
    levels = _levels(b.nodes, b.edges)
    for number, node in enumerate(b.nodes, 1):
        node['number'], node['level'] = number, levels[node['id']]
        node['inputs'], node['outputs'] = list(dict.fromkeys(node['inputs'])), list(dict.fromkeys(node['outputs']))
    coverage = ('Recorded controller rule/file declarations; internal tool execution may remain unverified'
                if traced else 'Configured dataflow; per-rule execution trace unavailable')
    if traced and (trace.get('coverage') == 'partial' or trace.get('truncated') or
                   any('excluded' in w or 'ambiguous' in w or 'size limit' in w for w in b.warnings)):
        coverage = 'Partial controller rule/file declarations; some events or dependencies are unavailable'
    graph = dict(schema=1, run_id=run.get('id'), sample_id=run.get('sample', {}).get('id'),
        pipeline=document.get('pipeline'), source_commit=run.get('commit'), config_sha256=run.get('config_sha256'),
        workflow_sha256=run.get('workflow_sha256'), known_source=bool(known_source),
        title='Run dataflow · ' + _text(run.get('id')), caption=CAPTION,
        coverage=dict(label=coverage, observed_file_transfers=False,
            explanation=('Arrows match scheduler-declared paths for jobs recorded in this invocation; only reported jobs are shown.'
                         if traced else 'Arrows show configured source dependencies, not observed execution or measured transfers.')),
        nodes=b.nodes, edges=b.edges, warnings=list(dict.fromkeys(b.warnings)))
    graph['tool_steps'] = [node for node in b.nodes if node['kind'] == 'tool']
    return graph

def _layout(graph):
    """Keep local dependencies local; give every long dependency its own lane."""
    card, positions, groups, y, row = 330, {}, defaultdict(list), 182, 0
    by_id = {node['id']: node for node in graph['nodes']}
    targets = {edge['target'] for edge in graph['edges']}
    children, parents = defaultdict(list), defaultdict(list)
    for edge in graph['edges']:
        children[edge['source']].append(edge['target'])
        parents[edge['target']].append(edge['source'])
    for node in graph['nodes']:
        level = node['level']
        # A database used late in the workflow belongs beside its consumer,
        # rather than adding a disconnected row above every read dependency.
        if node['kind'] == 'data' and node['id'] not in targets and children[node['id']]:
            level = min(by_id[key]['level'] for key in children[node['id']]) - 1
        groups[level].append(node)
    columns = min(4, max(3, max((len(group) for group in groups.values()), default=0)))
    content_width = columns * card + (columns - 1) * 18
    for level in sorted(groups):
        def parent_position(node):
            values = [positions[key][0] for key in parents[node['id']] if key in positions]
            return sum(values) / len(values) if values else content_width / 2
        groups[level].sort(key=parent_position)
        for start in range(0, len(groups[level]), columns):
            members = groups[level][start:start + columns]
            left = 24 + (content_width - (len(members) * card + (len(members) - 1) * 18)) / 2
            for index, node in enumerate(members):
                positions[node['id']] = (left + index * 348, y, row)
            y += 202
            row += 1
    # Separate ports make fan-in/fan-out attributable instead of merging all
    # arrows at one card midpoint. No port position encodes execution order.
    outgoing, incoming = defaultdict(list), defaultdict(list)
    for index, edge in enumerate(graph['edges']):
        outgoing[edge['source']].append(index)
        incoming[edge['target']].append(index)
    ports = {}
    for direction, grouped, other in [('source', outgoing, 'target'), ('target', incoming, 'source')]:
        for key, indices in grouped.items():
            indices.sort(key=lambda i: (positions[graph['edges'][i][other]][0], positions[graph['edges'][i][other]][1], i))
            local, long = [], []
            for edge_index in indices:
                edge = graph['edges'][edge_index]
                (local if positions[edge['target']][2] == positions[edge['source']][2] + 1 else long).append(edge_index)
            for index, edge_index in enumerate(local):
                ports[edge_index, direction] = positions[key][0] + 80 + (card - 160) * (index + 1) / (len(local) + 1)
            for index, edge_index in enumerate(long):
                offset = card - 80 if direction == 'source' else 0
                ports[edge_index, direction] = positions[key][0] + offset + 80 * (index + 1) / (len(long) + 1)
    departure, arrival = defaultdict(list), defaultdict(list)
    for index, edge in enumerate(graph['edges']):
        departure[positions[edge['source']][2]].append(index)
        arrival[positions[edge['target']][2]].append(index)
    route_y = {}
    for direction, grouped in [('source', departure), ('target', arrival)]:
        for indices in grouped.values():
            for index, edge_index in enumerate(indices):
                route_y[edge_index, direction] = 12 + 24 * (index + 1) / (len(indices) + 1)
    routes, lanes = [], 0
    for index, edge in enumerate(graph['edges']):
        _, sy, sr = positions[edge['source']]
        _, ty, tr = positions[edge['target']]
        sx, tx = ports[index, 'source'], ports[index, 'target']
        sy += 128
        if tr == sr + 1:
            middle = sy + route_y[index, 'source']
            path = (f'M{sx} {sy} V{ty - 4}' if sx == tx else
                    f'M{sx} {sy} V{middle} H{tx} V{ty - 4}')
            lane = None
        else:
            lane = content_width + 54 + lanes * 12
            lanes += 1
            path = f'M{sx} {sy} V{sy + route_y[index, "source"]} H{lane} V{ty - route_y[index, "target"]} H{tx} V{ty - 4}'
        routes.append(dict(path=path, lane=lane, tip=(tx, ty - 4)))
    return max(content_width + 134, content_width + 78 + lanes * 12), y + 118, positions, routes


def svg(graph):
    """Standalone escaped SVG with accessible full node/edge descriptions."""
    esc = lambda value: html.escape(_text(value), quote=True)
    width, height, positions, routes = _layout(graph)
    card = 330
    rows = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" role="img" aria-labelledby="methods-flow-title methods-flow-desc">',
        '<title id="methods-flow-title">' + esc(graph['title']) + '</title>',
        '<desc id="methods-flow-desc">' + esc(graph['caption'] + ' ' + graph['coverage']['explanation']) + '</desc>',
        f'<rect width="{width}" height="{height}" fill="#fff"/>', '<g font-family="Arial, sans-serif" fill="#173c40">',
        '<text x="24" y="32" font-size="21" font-weight="bold">' + esc(graph['title']) + '</text>']
    for index, line in enumerate(textwrap.wrap(graph['caption'], 150)):
        rows.append(f'<text x="24" y="{55 + index * 17}" font-size="12">{esc(line)}</text>')
    for index, line in enumerate(textwrap.wrap(graph['coverage']['label'], 145)):
        rows.append(f'<text x="24" y="{105 + index * 17}" font-size="12" font-weight="bold">{esc(line)}</text>')
    rows.append('<text x="24" y="150" font-size="11">Dashed: configured dependency · Solid: controller-declared I/O · Status labels do not establish scientific validity.</text>')
    for index, edge in enumerate(graph['edges']):
        dash = ' stroke-dasharray="5 4"' if edge['kind'] != 'scheduler_declared' else ''
        tip_x, tip_y = routes[index]['tip']
        rows.append(f'<path d="{routes[index]["path"]}" fill="none" stroke="#76928d" stroke-width="1.4"{dash}><title>{esc(edge["label"])}</title></path>')
        rows.append(f'<path d="M{tip_x} {tip_y} l-4 -8 h8 Z" fill="#76928d"/>')
    for node in graph['nodes']:
        x, top, _ = positions[node['id']]
        if node['status'] in {'failed', 'rule_failed', 'blocked', 'reported_incomplete'}:
            bg, color = '#fff1ed', '#9a4a37'
        elif node['status'] in {'reported_completed', 'reported_executed', 'rule_completed', 'recorded'}:
            bg, color = '#edf6f3', '#286c60'
        elif node['status'] in {'unverified', 'skipped', 'reused'}:
            bg, color = '#fff8e9', '#8b672a'
        else:
            bg, color = '#f2f6f7', '#47626a'
        detail = '; '.join([node['label'], node['status_label'], node['what'], node['why'], node['evidence']])
        if node.get('version'):
            detail += '; version=' + node['version'] + '; ' + node.get('version_scope', '')
        rows += [f'<g id="methods-flow-node-{node["number"]}"><title>{esc(detail)}</title>',
            f'<rect x="{x}" y="{top}" width="{card}" height="128" rx="9" fill="{bg}" stroke="{color}"/>',
            f'<text x="{x + 12}" y="{top + 18}" font-size="10" fill="{color}">{node["number"]} · {esc(node["kind"].upper())}</text>']
        for index, line in enumerate(textwrap.wrap(node['label'], 43)[:2]):
            rows.append(f'<text x="{x + 12}" y="{top + 41 + index * 16}" font-size="13" font-weight="bold">{esc(line)}</text>')
        for index, line in enumerate(textwrap.wrap(node['status_label'], 48)[:2]):
            rows.append(f'<text x="{x + 12}" y="{top + 81 + index * 14}" font-size="10">{esc(line)}</text>')
        if node.get('version'):
            version = textwrap.shorten('Version: ' + node['version'], 52, placeholder='…')
            rows.append(f'<text x="{x + 12}" y="{top + 116}" font-size="10">{esc(version)}</text>')
        rows.append('</g>')
    footer = 'Run ' + _text(graph['run_id']) + ' · Source ' + _text(graph['source_commit'])[:12] + ' · Config ' + _text(graph['config_sha256'])[:12]
    rows.append(f'<text x="24" y="{height - 72}" font-size="11">{esc(footer)}</text>')
    rows.append(f'<text x="24" y="{height - 51}" font-size="11">{len(graph["warnings"])} review note(s). Full descriptions, file paths and evidence accompany this figure in JSON/Methods.</text>')
    rows.append('</g></svg>')
    return '\n'.join(rows).encode('utf-8')


