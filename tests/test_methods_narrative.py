"""Run prose preserves configuration, reuse and controller evidence classes."""
import copy
import pytest
from bactologix import methods_narrative as narrative


def fixture(pipeline='hybas', completed=True):
    run = dict(id='run-1', tool=pipeline, sample={'id':'isolate-1','mode':'hybrid'},
               commit='585abb34ed4401da64b37a9fd629ba8adc236396', cores=12, memory_mb=28672, inputs=[])
    doc = dict(pipeline={'hybas':'HybAs','basis':'BaSIS','micpscope':'GeneScope'}[pipeline],
        completed='2026-11-10' if completed else None, evidence_origin='Completion-time snapshot',
        evidence={'identity':{'checks':['sample identity']},'decisions':{},'records':[]}, software=[])
    cfg = dict(run_kraken=True,kraken_decision_mode='aware',polish_mode='tripolish',racon_rounds=3,
               run_polypolish=True,run_busco=True,run_prokka=True,run_multiqc=True)
    return run,doc,cfg


def prose(args, known=True):
    return '\n\n'.join(narrative.build(*args,known_source=known)['paragraphs'])


@pytest.fixture(autouse=True)
def parameters(monkeypatch):
    monkeypatch.setattr(narrative,'per_tool',lambda run,cfg,known: {
        'fastp':['quality threshold=23 (saved run)'],'racon':['requested rounds=3 (saved run)'],
        'unicycler':['minimum FASTA length=500 bp (reviewed argument)'],
        'diamond':['minimum identity=31 (saved run)'],'amrfinder':['identity_min=0.91 (saved run)'],
        'medaka':['model=requested-model (saved run)']} if known else {})


def test_order_and_inline_thresholds():
    text=prose(fixture())
    assert text.startswith('HybAs was used through Bactologix')
    parts=['quality of the raw short reads','classify the registered reads','filter and trim the selected short-read pairs',
           'quality threshold=23','construct the draft assembly','minimum FASTA length=500 bp',
           'align processed long reads','apply model-based consensus','align the processed short reads for Polypolish']
    assert [text.index(p) for p in parts] == sorted(text.index(p) for p in parts)
    assert 'final FASTA length filtering' not in text
    assert 'workflow allocation, not measured per-tool resource use' in text


def test_native_stages_models_and_final_producer():
    args=fixture()
    args[1]['evidence']['decisions']=dict(assembler='unicycler',executed_stages=['racon1','racon2','medaka'],
        skipped_stages={'polypolish':'no_mapped_reads'},medaka_model_effective='resolved-model',final_source_stage='medaka')
    text=prose(args)
    assert '2 executed Racon stages (racon1, racon2)' in text and 'requested rounds=3' in text
    assert 'effective Medaka model was resolved-model' in text and 'as skipped: no_mapped_reads' in text
    assert 'identifies medaka as the source of the final assembly' in text
    assert 'Polypolish (version not recorded) was used' not in text
    del args[1]['evidence']['decisions']['medaka_model_effective']
    assert 'effective Medaka model was Not recorded' in prose(args)


@pytest.mark.parametrize('change',['legacy','failed_latest','identity_missing'])
def test_unattributable_metadata_never_asserts_execution(change):
    args=fixture(); args[1]['evidence']['decisions']={'executed_stages':['racon1'],'assembler':'unicycler'}
    if change=='legacy': args[1]['evidence_origin']='Legacy output metadata read at export'
    elif change=='failed_latest': args[1]['completed']=None
    else: args[1]['evidence']['identity']['checks']=[]
    text=prose(args)
    assert 'reports that Racon' not in text and 'as the assembler used' not in text and 'executed Racon stages' not in text


def test_inventory_and_controller_outcomes_keep_scope():
    args=fixture(); args[1]['software']=[dict(tool='unicycler',version='0.5.1',scope='Recorded environment package; execution not established')]
    args[1]['runtime_evidence']={'versions':{'snakemake':'9.6.2'},'rule_trace':{'coverage':'partial','jobs':[
        {'rule':'fastp_trim','status':'completed'},{'rule':'nanoplot_filt','status':'completed'}]}}
    text=prose(args)
    assert 'version 0.5.1; environment inventory only; invoked version unverified' in text
    assert 'Snakemake 9.6.2 orchestrated this recorded invocation' in text and 'fastp_trim (completed)' in text
    assert 'Controller rule completion does not establish every internal tool invocation' in text
    assert 'The controller trace is partial' in text and 'reports that fastp' not in text
    assert 'nanoplot_filt' not in next(p for p in text.split('\n\n') if 'summarize raw long-read' in p)
    args[1]['runtime_evidence']['rule_trace']['jobs']=[]
    assert 'No rule events were recorded for this invocation' in prose(args)
    assert 'no earlier attempt is substituted' in prose(args)


@pytest.mark.parametrize('mode,absent',[('illumina_only',['Filtlong','Racon','Medaka']),('ont_only',['fastp','FastQC','Polypolish'])])
def test_incompatible_and_disabled_stages_omitted(mode,absent):
    args=fixture(); args[0]['sample']['mode']=mode
    assert all(word not in prose(args) for word in absent)
    args[2]['polish_mode']='none'
    assert all(word not in prose(args) for word in ['Racon','Medaka','Polypolish'])


def test_unknown_and_experimental_sources_no_borrowed_topology():
    args=fixture()
    text=prose(args,False)
    assert 'source-matched workflow description is unavailable' in text and 'quality threshold=23' not in text
    assert 'Unicycler' not in text
    args[0]['commit']='79c5b7f342e02bd5b60bf7847fc5f4fb441e1fd4'; args[1]['evidence']['decisions']={'assembler':'flye'}
    text=prose(args)
    assert 'Flye' in text and 'Unicycler' not in text and 'Filtlong' not in text
    assert 'specified the saved input manifest' in text


@pytest.mark.parametrize('condition,expected',[('reused','were reused'),('incomplete','as incomplete'),('failed','as failed'),('blocked','as blocked'),
                                              ('not_started','as not started'),('not_assessed','as not assessed')])
def test_basis_status_scope_and_followup(condition,expected):
    args=fixture('basis'); args[2].update(modules={'amrfinder':{'enabled':True},'rgi':{'enabled':False}},deep={'enabled':False})
    row=dict(module='amrfinder',sample_id='isolate-1',execution_status='completed')
    if condition=='reused': row['cache_hit']=True
    elif condition=='incomplete': row['completion_summary']={'deferred_candidates':1}
    else: row.update(execution_status=condition,reason='recorded reason',complete=False)
    args[1]['evidence']['records']=[row]
    text=prose(args)
    assert expected in text and 'identity_min=0.91' in text and 'CARD / RGI' not in text
    assert 'AMRFinderPlus (version not recorded) was used' not in text
    args[0].update(run_type='followup',parent='run-parent')
    text=prose(args)
    assert 'parent run run-parent' in text and 'screening tools are not attributed as newly executed' in text
    assert 'AMRFinderPlus' not in text


def test_genescope_scope_and_sidecars():
    args=fixture('micpscope',False); args[0]['inputs']=[{'path':'/saved/isolate.gff'}]; args[2]['reference_database']='/db'
    text=prose(args)
    assert text.startswith('GeneScope was selected') and 'minimum identity=31' in text and 'isolate.gff' in text
    assert text.index('DIAMOND') < text.index('Biopython') < text.index('render the source-labelled')
    assert 'separate GeneScope calculation for the aliphatic index' in text and 'calculated the aliphatic index' not in text
    assert 'do not determine pH or temperature optima' in text and 'Prokka' not in text


@pytest.mark.parametrize('source', sorted(narrative.GENESCOPE))
def test_reviewed_genescope_descriptors_and_normalization_are_source_policy(source):
    args = fixture('micpscope', False)
    args[0]['commit'] = source
    text = prose(args)
    assert all(name in text for name in ('molecular weight', 'isoelectric point (pI)', 'GRAVY',
                                        'instability index', 'aromaticity', 'amino-acid counts', 'aliphatic index'))
    assert 'normalization policy removes one terminal stop character' in text
    assert 'unsupported or ambiguous residues' in text and 'not-calculated status' in text
    assert 'reports that' not in text
    args[0]['commit'] = 'another-reviewed-source'
    assert 'normalization policy' not in prose(args)
    assert 'full export settings supplement' in text


def test_no_mutation():
    args=fixture(); before=copy.deepcopy(args); prose(args)
    assert args==before


def test_native_input_mode_difference_is_not_silently_attributed_to_saved_route():
    args = fixture()
    args[1]['evidence']['decisions'] = {'assembler':'unicycler', 'run_mode':'ont_only'}
    text = prose(args)
    assert 'native summary records input mode ont_only' in text
    assert 'saved input route was hybrid' in text and 'actual input consumption is unresolved' in text
    assert 'as the assembler. Its configured role' in text


def test_reused_incomplete_module_keeps_both_qualifications():
    args = fixture('basis')
    args[2]['modules'] = {'amrfinder': {'enabled':True}}
    args[1]['evidence']['records'] = [{'module':'amrfinder', 'execution_status':'completed', 'cache_hit':True, 'complete':False}]
    assert 'were reused' in prose(args) and 'reused result has incomplete recorded scope' in prose(args)


@pytest.mark.parametrize('stage', ['identification', 'aware'])
def test_verified_dev_stop_stage_does_not_acquire_downstream_processing(stage):
    args = fixture(completed=False)
    args[1].update(completed_stage=stage, state='Recorded stage', stage_narrative=['Verified stage-only receipt prose.'])
    text = prose(args)
    assert text.startswith('HybAs was used') and 'Verified stage-only receipt prose.' in text
    assert all(tool not in text for tool in ('Unicycler', 'Racon', 'Medaka', 'Polypolish'))
    assert 'execution incomplete' not in narrative.build(*args, known_source=True)['status']
