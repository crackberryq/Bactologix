"""Describe saved settings and reviewed source arguments without executing a tool.

These clauses describe configuration, never successful execution. ``cfg`` must be
methods._configuration's effective mapping; ``known_source`` also requires the
frozen workflow digest to match. No executable's implicit defaults are invented.
The fixed arguments below were checked in Hybas.smk, library_inputs.py,
micpscope/reference.py and BaSIS native.py/adapters/amr.py at these exact commits.
"""
import json

HYBAS_REGULAR = frozenset({
    '780ee7086c15c49f2825800ec463327f009f14a6',
    'a99e00b041a184f36cfdf8a2ab5b61f0f255a02d',
    '87c1ab81c8652bd10db47954193e27ad802cfc86',
    '585abb34ed4401da64b37a9fd629ba8adc236396',
    '5db662262962bc822f2fe501234d15b882d5b042',
})
HYBAS_DEV = frozenset({
    '08c315479f5493ce0b1f44ba26da3c7f2e0d748b',
    '79c5b7f342e02bd5b60bf7847fc5f4fb441e1fd4',
})
GENESCOPE = frozenset({
    'af9502c3040258a69b1efc662a6f11b95e46b729',
    'b47b16c286051ec841e7cdb593350004eb028475',
    'f46cc9feeb30cd89b36876b7d341ed6750e19ce3',
    '92ea05df6e9c156ecba13315993d4cd12142fcfc',
    '28bf38c44eb07fe8e4865f67d59ef6b4028f51dc',
})
BASIS = frozenset({
    '14f161a1f252a712053892961edf2275469c8dab',
    '1d2d96b266e98d956254d1a518ff5b437ab7a10a',
    '6128b06279fec2f51015a76f798d0364a4b81602',
    '749e178cc8c1f74b1997e11eca0a624822f92aee',
    '7c293d36a633a726424d544c6ffc39824537ebe4',
})
CONFIG = 'effective saved/source-matched configuration'
FIXED = 'reviewed workflow argument'


def _value(value):
    if isinstance(value, bool):
        return 'true' if value else 'false'
    if value is None:
        return 'not set'
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return str(value) if value != '' else 'not set'


def _flatten(value, prefix=''):
    for key, item in value.items():
        name = prefix + str(key)
        if isinstance(item, dict) and item:
            yield from _flatten(item, name + '.')
        else:
            yield name, item


def _enabled(value):
    return value is True or isinstance(value, str) and value.lower() in {'true', 'yes', '1', 'on'}


def per_tool(run, cfg, known_source):
    """Return tool -> parameter clauses, source-qualified and deterministic.

    Unknown/mutated workflow sources get no tool/setting association. Their
    explicit saved settings remain in the export's independent supplement.
    Fixed flags are never applied to an unreviewed commit, even if a caller
    considers its configuration source known. A configured threshold is not
    evidence that a command ran or that its resulting data passed that threshold.
    """
    if not known_source:
        return {}
    result = {}
    source = run.get('commit', '')
    pipeline = run.get('tool')

    def add(tool, clause, provenance=CONFIG):
        result.setdefault(tool, []).append(f'{clause} ({provenance})')

    def settings(tool, values, labels=None):
        labels = labels or {}
        clauses = [f'{labels.get(key, key)}={_value(value)}' for key, value in values]
        if clauses:
            add(tool, '; '.join(clauses))

    def selected(tool, keys, labels=None):
        settings(tool, ((key, cfg[key]) for key in keys if key in cfg), labels)

    def natural(tool, key, template):
        if key in cfg:
            add(tool, template.format(value=_value(cfg[key])))

    if pipeline == 'hybas':
        natural('filtlong', 'keep_percent', 'retention of {value}% of long-read bases')
        selected('kraken2', ['kraken_db_name', 'kraken_mem_mb'])
        selection_keys = [key for key in cfg if key.startswith(('kraken_auto_', 'kraken_aware_'))]
        selected('krakentools', ['kraken_decision_mode', 'target_taxid', 'kraken_include_children',
            'aware_mode', 'aware_run_until', 'kraken_review_before_filtering', *sorted(selection_keys)])
        selected('unicycler', ['spades_memory_gb', 'assembly_mem_mb'])
        natural('unicycler', 'min_fasta_length', 'minimum assembly FASTA length of {value} bp')
        natural('racon', 'racon_rounds', '{value} requested Racon rounds')
        selected('medaka', ['medaka_model'], {'medaka_model': 'requested Medaka model'})
        selected('polypolish', ['run_polypolish'])
        selected('busco', ['busco_lineage', 'busco_mem_mb'])
        selected('prokka', ['prokka_mem_mb'])
        if source in HYBAS_REGULAR | HYBAS_DEV:
            add('filtlong', 'minimum read length of 1000 bp (--min_length 1000)', FIXED)
            add('minimap2', 'ONT alignment preset=map-ont (-x map-ont), for the Racon route', FIXED)
            add('bwa', 'BWA-MEM reports all alignments (-a), with each short-read mate aligned separately for Polypolish', FIXED)
            add('bwa', 'the separate paired-end mapping QC branch uses all alignments and the intractg preset (-a -x intractg)', FIXED)
            add('samtools', 'FASTA indexing with faidx; coordinate-sorted BAM output (sort -O BAM), BAM indexing, and name sorting (sort -n -O BAM)', FIXED)
            add('samtools', 'paired-end mapping QC records an alignment-record count (view -c); the separate Polypolish input guard counts mapped alignment records (view -c -F 4) and skips polishing when their sum is zero', FIXED)
            add('busco', 'mode=genome; offline lineage data (-m genome --offline)', FIXED)
            add('fastp', 'no explicit quality, length or adapter threshold override; numeric tool defaults were not recorded', FIXED)
            add('kraken2', 'no explicit confidence threshold override; its numeric tool default was not recorded', FIXED)
        if source in HYBAS_DEV:
            for assembler in ('spades', 'flye'):
                natural(assembler, 'min_fasta_length', 'workflow promotion threshold of {value} bp for assembler-output contigs')
            selected('spades', ['spades_memory_gb', 'assembly_mem_mb'])
            add('spades', 'library dataset input; Phred offset=33 (--dataset, --phred-offset 33)', FIXED)
            add('flye', 'iterations=1; PacBio chemistry flag selected from the declared library chemistry', FIXED)
        return result

    if pipeline == 'micpscope':
        if cfg.get('reference_database'):
            natural('diamond', 'reference_min_identity', 'sequence identity of at least {value}%')
            natural('diamond', 'reference_min_coverage', 'coverage of at least {value}% of both query and reference')
            natural('diamond', 'reference_max_evalue', 'maximum E-value of {value}')
            if source in GENESCOPE:
                add('diamond', 'blastp; --very-sensitive; --max-target-seqs 0; --max-hsps 1', FIXED)
                add('diamond', 'near-best candidates use a bit-score fraction of 0.95; differing labels among these candidates are marked ambiguous', FIXED)
        return result

    if pipeline == 'basis':
        sample_id = run.get('sample', {}).get('id')
        overrides = cfg.get('sample_module_overrides', {}).get(sample_id, {})
        if run.get('run_type') != 'followup':
            for tool, options in cfg.get('modules', {}).items():
                if not isinstance(options, dict) or not _enabled(options.get('enabled')) or tool in overrides:
                    continue
                if tool == 'pathogenfinder2' and not _enabled(cfg.get('extend', True)):
                    continue
                labels = {}
                if tool in {'vfdb', 'bacmet'}:
                    labels = {
                        'min_identity': 'post-search evidence identity threshold (%)',
                        'min_coverage': 'post-search evidence subject coverage per HSP threshold (%)',
                        'min_query_coverage': 'post-search evidence query coverage threshold (%)',
                        'evalue': 'search E-value cutoff',
                    }
                settings(tool, ((key, value) for key, value in _flatten(options) if key not in {'enabled', 'database'}), labels)
                if source in BASIS:
                    if tool in {'vfdb', 'bacmet'}:
                        add(tool, 'DIAMOND blastp; --max-hsps 1; identity/coverage classify retained evidence and are not DIAMOND search filters', FIXED)
                        if tool == 'bacmet':
                            add(tool, 'reference identifiers retained verbatim (--no-parse-seqids)', FIXED)
                    elif tool == 'amrfinder':
                        add(tool, 'combined nucleotide/protein/GFF inputs; --plus', FIXED)
                    elif tool == 'rgi':
                        add(tool, 'run-local CARD database (--local); no nudge flag', FIXED)
                    elif tool == 'antismash':
                        add(tool, 'taxon=bacteria; minimum length=1 bp; abort on invalid records', FIXED)
        deep = cfg.get('deep') or {}
        if isinstance(deep, dict) and (run.get('run_type') == 'followup' or _enabled(deep.get('enabled'))):
            settings('deepsafe', ((key, value) for key, value in _flatten(deep) if key != 'enabled'), {
                'min_identity': 'reference comparison minimum identity (%)',
                'min_coverage': 'reference comparison minimum query coverage (%)'})
        return result
    return result
