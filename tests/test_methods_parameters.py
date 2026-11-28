from bactologix.methods_parameters import per_tool

HYBAS = '585abb34ed4401da64b37a9fd629ba8adc236396'
DEV = '79c5b7f342e02bd5b60bf7847fc5f4fb441e1fd4'
GENESCOPE = 'af9502c3040258a69b1efc662a6f11b95e46b729'
BASIS = '7c293d36a633a726424d544c6ffc39824537ebe4'


def run(pipeline, source, **extra):
    return {'tool': pipeline, 'commit': source, 'sample': {'id': 'example'}, **extra}


def test_unknown_source_never_associates_configuration_with_tool_commands():
    assert per_tool(run('hybas', HYBAS), {'keep_percent': 95}, False) == {}
    # New template approval alone cannot extend the fixed-argument allowlist.
    rows = per_tool(run('hybas', 'new-source'), {'keep_percent': 94}, True)
    assert '1000' not in str(rows)
    assert '94' in str(rows)


def test_hybas_thresholds_preserve_saved_values_and_unknown_tool_defaults():
    rows = per_tool(run('hybas', HYBAS), {'keep_percent': 82, 'racon_rounds': 0,
        'min_fasta_length': 750, 'medaka_model': '', 'kraken_aware_delta_min_percent': 12}, True)
    assert '82' in str(rows['filtlong']) and '--min_length 1000' in str(rows['filtlong'])
    assert '0 requested Racon rounds' in str(rows['racon'])
    assert 'minimum assembly FASTA length of 750 bp' in str(rows['unicycler'])
    assert 'assembly' not in rows
    assert 'requested Medaka model=not set' in str(rows['medaka'])
    assert 'numeric tool defaults were not recorded' in str(rows['fastp'])
    assert '12' in str(rows['krakentools'])
    assert 'reviewed workflow argument' in str(rows['bwa'])


def test_dev_does_not_import_regular_settings_and_keeps_route_qualifiers():
    rows = per_tool(run('hybas', DEV), {'input_manifest': 'manifest.json', 'min_fasta_length': 650}, True)
    assert 'racon' not in rows and 'keep_percent' not in str(rows)
    assert 'Phred offset=33' in str(rows['spades'])
    assert 'workflow promotion threshold of 650 bp' in str(rows['spades'])
    assert 'workflow promotion threshold of 650 bp' in str(rows['flye'])
    assert 'declared library chemistry' in str(rows['flye'])
    assert 'for the Racon route' in str(rows['minimap2'])
    assert 'flye' not in per_tool(run('hybas', HYBAS), {}, True)


def test_genescope_coverage_is_bidirectional_and_search_is_optional():
    cfg = {'reference_database': '/db/application', 'reference_min_identity': 41,
           'reference_min_coverage': 83, 'reference_max_evalue': 1e-8}
    rows = per_tool(run('micpscope', GENESCOPE), cfg, True)
    assert 'coverage of at least 83% of both query and reference' in str(rows['diamond'])
    assert 'sequence identity of at least 41%' in str(rows['diamond'])
    assert '/db/application' not in str(rows['diamond'])
    assert '--max-target-seqs 0' in str(rows['diamond'])
    assert '0.95' in str(rows['diamond'])
    assert per_tool(run('micpscope', GENESCOPE), {}, True) == {}


def test_basis_thresholds_are_not_misrepresented_as_search_filters():
    cfg = {'modules': {'vfdb': {'enabled': True, 'min_identity': 87, 'min_coverage': 76,
        'min_query_coverage': 12, 'evalue': 1e-7, 'custom': {'threshold': 0}},
        'bacmet': {'enabled': False}, 'rgi': {'enabled': True, 'include_loose': False}},
        'deep': {'enabled': True, 'min_coverage': 61, 'aware': {'tools': {'vfdb': {'min_identity': 91}}}}}
    rows = per_tool(run('basis', BASIS), cfg, True)
    assert 'post-search evidence identity threshold (%)=87' in str(rows['vfdb'])
    assert 'subject coverage per HSP threshold (%)=76' in str(rows['vfdb'])
    assert 'not DIAMOND search filters' in str(rows['vfdb'])
    assert 'custom.threshold=0' in str(rows['vfdb'])
    assert 'include_loose=false' in str(rows['rgi'])
    assert 'bacmet' not in rows
    assert 'minimum query coverage (%)=61' in str(rows['deepsafe'])
    assert 'aware.tools.vfdb.min_identity=91' in str(rows['deepsafe'])


def test_basis_followup_does_not_restate_parent_screening_as_new_tools():
    cfg = {'modules': {'rgi': {'enabled': True}}, 'deep': {'enabled': False, 'maximum_candidates': 3}}
    rows = per_tool(run('basis', BASIS, run_type='followup'), cfg, True)
    assert set(rows) == {'deepsafe'}
    cfg['sample_module_overrides'] = {'example': {'rgi': {'enabled': False}}}
    assert not per_tool(run('basis', BASIS), cfg, True)


def test_auxiliary_mapping_arguments_are_source_bound_and_roles_are_separate():
    for source in ('780ee7086c15c49f2825800ec463327f009f14a6', HYBAS, DEV):
        rows = per_tool(run('hybas', source), {}, True)
        assert 'each short-read mate aligned separately for Polypolish' in str(rows['bwa'])
        assert 'separate paired-end mapping QC branch' in str(rows['bwa'])
        assert '-a -x intractg' in str(rows['bwa'])
        assert 'sort -n -O BAM' in str(rows['samtools'])
        assert 'view -c -F 4' in str(rows['samtools'])
        assert 'sum is zero' in str(rows['samtools'])
    assert 'samtools' not in per_tool(run('hybas', 'unknown-source'), {}, True)
