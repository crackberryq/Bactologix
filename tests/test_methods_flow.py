"""Dataflow exports distinguish declarations, native status and scientific validity."""
import copy
import json
import xml.etree.ElementTree as ET

import pytest

from bactologix import methods_flow as flow


def fixture(tool='micpscope', **config):
    run = {'id':'run-example', 'tool':tool, 'sample':{'id':'isolate-1', 'mode':'hybrid'},
           'commit':'a99e00b041a184f36cfdf8a2ab5b61f0f255a02d',
           'config_sha256':'c'*64, 'workflow_sha256':'d'*64, 'inputs':[]}
    doc = {'pipeline':tool, 'evidence_origin':'Completion-time snapshot',
           'evidence':{'identity':{'checks':['sample identity']}, 'records':[], 'decisions':{}},
           'software':[{'tool':'snakemake', 'version':'9.6.2', 'scope':'Recorded controller runtime'}]}
    return run, doc, config


def graph(args, known=True):
    return flow.build(*args, known_source=known)


def nodes(g):
    return {node['id']:node for node in g['nodes']}


def links(g):
    return {(edge['source'], edge['target']) for edge in g['edges']}


def test_reference_search_feeds_properties_and_optional_inputs_are_historical():
    args = fixture(reference_database='/saved/reference')
    args[0]['inputs'] = [{'path':'/saved/proteins.faa'}, {'path':'/saved/proteins.gff'}]
    g = graph(args)
    assert ('reference-search-output', 'protein-properties') in links(g)
    assert ('protein-properties-output', 'genescope-report') in links(g)
    assert ('sidecar-1', 'protein-properties') in links(g)
    assert not any(n.get('tool') == 'prokka' for n in g['nodes'])
    assert not g['coverage']['observed_file_transfers']
    assert 'not scientifically validated' in g['caption']
    assert all(edge['kind'] == 'configured_dependency' for edge in g['edges'])
    assert all(n['status'] == 'configured' for n in g['tool_steps'])
    without = graph(fixture())
    assert not any(n.get('tool') == 'diamond' or n['id'].startswith('sidecar-') for n in without['nodes'])


def test_hybas_qc_branches_and_independent_final_assembly_assessments():
    g = graph(fixture('hybas', run_kraken=True, kraken_decision_mode='aware', polish_mode='tripolish',
                      racon_rounds=2, run_polypolish=True, run_prokka=True, run_busco=True, run_multiqc=True))
    expected = {('short-reads', 'short-qc'), ('short-reads', 'taxonomy'),
                ('selected-short-reads', 'short-preprocessing'), ('selected-long-reads', 'long-preprocessing'),
                ('short-preprocessing-output', 'assembly'), ('long-preprocessing-output', 'assembly'),
                ('final-assembly', 'quast'), ('final-assembly', 'busco'), ('final-assembly', 'prokka')}
    assert expected <= links(g)
    assert ('short-qc-output', 'assembly') not in links(g)
    assert ('prokka-output', 'multiqc') not in links(g)
    assert ('racon-output', 'medaka') in links(g)
    assert ('medaka-output', 'short-alignments') in links(g)
    assert ('medaka-output', 'paired-mapping-qc') in links(g)
    assert ('paired-mapping-qc-output', 'alignment-qc') in links(g)
    assert ('alignment-qc-output', 'polypolish') not in links(g)
    assert ('final-assembly', 'sequence-statistics') in links(g)


@pytest.mark.parametrize('mode,absent', [('illumina_only', {'filtlong','medaka','racon'}), ('ont_only', {'fastp','polypolish','bwa'})])
def test_input_platform_selects_compatible_configured_route(mode, absent):
    args = fixture('hybas', polish_mode='tripolish', racon_rounds=2, run_polypolish=True)
    args[0]['sample']['mode'] = mode
    assert not absent.intersection(n.get('tool') for n in graph(args)['nodes'])


def test_historical_source_has_no_later_branded_renderer_and_zero_racon_is_not_invented():
    args = fixture('hybas', polish_mode='medaka', racon_rounds=0)
    args[0]['commit'] = '780ee7086c15c49f2825800ec463327f009f14a6'
    g = graph(args)
    assert not any(n.get('tool') == 'racon' or n['id'] == 'hybas-report' for n in g['nodes'])
    assert ('assembly-output', 'medaka') in links(g)


def test_baSIS_parallel_modules_and_separate_followup_report_dependencies():
    g = graph(fixture('basis', modules={'amrfinder':{'enabled':True}, 'rgi':{'enabled':True}}, deep={'enabled':True}))
    assert {('annotated-inputs','amrfinder'), ('annotated-inputs','rgi')} <= links(g)
    assert ('amrfinder-output','rgi') not in links(g)
    assert {('amrfinder-normalization-output','deepsafe'), ('rgi-normalization-output','deepsafe'),
            ('amrfinder-normalization-output','followup-report'), ('deepsafe-output','followup-report')} <= links(g)
    assert ('deepsafe-output','screening-report') not in links(g)
    args = fixture('basis', modules={'amrfinder':{'enabled':True}})
    args[0].update(run_type='followup', parent='run-screening')
    saved = graph(args)
    assert not any(n.get('tool') == 'amrfinder' for n in saved['nodes'])
    assert nodes(saved)['screening-evidence']['status'] == 'reused'
    assert ('screening-evidence','followup-report') in links(saved)


@pytest.mark.parametrize('gap', ['complete','deferred','native_module','native_sample_module'])
def test_native_completed_rule_can_still_have_incomplete_scope(gap):
    args = fixture('basis', modules={'amrfinder':{'enabled':True}})
    row = {'sample_id':'isolate-1', 'module':'amrfinder', 'execution_status':'completed'}
    args[1]['evidence']['records'] = [row]
    if gap == 'complete': row['complete'] = False
    elif gap == 'deferred': row['completion_summary'] = {'deferred_candidates':2}
    else: args[1]['evidence']['native_run'] = {'incomplete_modules':['amrfinder' if gap == 'native_module' else 'isolate-1:amrfinder']}
    assert nodes(graph(args))['amrfinder']['status'] == 'reported_incomplete'


def test_retrospective_versions_do_not_become_execution_and_reuse_is_explicit():
    args = fixture('basis', modules={'amrfinder':{'enabled':True}})
    args[1]['evidence']['records'] = [{'module':'amrfinder','execution_status':'completed','reused':True}]
    assert nodes(graph(args))['amrfinder']['status'] == 'reused'
    args[1]['evidence_origin'] = 'Legacy output metadata read at export'
    args[1]['software'].append({'tool':'amrfinder','version':'99.1','scope':'Current output metadata; execution attribution unverified'})
    node = nodes(graph(args))['amrfinder']
    assert node['status'] == 'configured'
    assert node['version'] == '99.1' and 'unverified' in node['version_scope']


def test_native_racon_round_identifiers_are_retained_without_claiming_requested_rounds():
    args = fixture('hybas', polish_mode='racon', racon_rounds=4)
    args[1]['evidence']['decisions']['executed_stages'] = ['racon1', 'racon2', 'not-racon3', 'racon0']
    node = nodes(graph(args))['racon']
    assert node['status'] == 'reported_executed'
    assert 'recorded stages: racon1, racon2' in node['evidence']
    assert 'not-racon3' not in node['evidence'] and 'racon0' not in node['evidence']
    args[1]['evidence_origin'] = 'Legacy output metadata read at export'
    assert nodes(graph(args))['racon']['status'] == 'configured'


@pytest.mark.parametrize('status', ['failed', 'blocked'])
def test_native_failure_is_not_hidden_by_completed_wrapper_rule(status):
    args = fixture('basis', modules={'amrfinder':{'enabled':True}})
    args[1]['evidence']['records'] = [{'module':'amrfinder', 'execution_status':status}]
    trace_doc(args, [job('a', 'amrfinder', ['/input'], ['/output'])])
    assert graph(args)['tool_steps'][0]['status'] == status


def test_unknown_or_experimental_source_cannot_borrow_regular_hybas_route():
    args = fixture('hybas', polish_mode='tripolish', racon_rounds=2)
    assert not graph(args, False)['tool_steps']
    args[0]['commit'] = '08c315479f5493ce0b1f44ba26da3c7f2e0d748b'
    g = graph(args)
    assert not any(n.get('tool') == 'unicycler' for n in g['nodes'])
    assert any('Experimental library topology' in w for w in g['warnings'])

def trace_doc(args, jobs):
    args[1]['runtime_evidence'] = {'rule_trace':dict(schema=1, observer='snakemake-structured-log',
        coverage='captured', truncated=False, jobs=jobs, warnings=[])}
    return args

def job(jid, rule, inputs, outputs, status='completed'):
    return dict(id=jid, rule=rule, inputs=inputs, outputs=outputs, status=status,
                reported_at='2026-11-10T10:00:00+00:00')

def test_trace_matches_file_paths_without_inventing_a_serial_chain():
    args = trace_doc(fixture(), [job('a','reference_search',['/p.faa'],['/reference.json']),
        job('b','protein_properties',['/p.faa','/reference.json'],['/results.json']),
        job('c','independent_check',['/other'],['/unrelated']), job('d','report',['/results.json'],['/report.html'])])
    before = copy.deepcopy(args)
    g = graph(args)
    r = {n['rule']:n for n in g['tool_steps']}
    assert g['coverage']['label'].startswith('Recorded controller')
    assert all(e['kind'] == 'scheduler_declared' for e in g['edges'])
    assert (r['reference_search']['id'],r['protein_properties']['id']) in links(g)
    assert (r['protein_properties']['id'],r['report']['id']) in links(g)
    assert (r['independent_check']['id'],r['report']['id']) not in links(g)
    assert r['independent_check']['tool'] == ''
    assert r['protein_properties']['status'] == 'rule_completed'
    assert 'does not prove every internal tool' in r['protein_properties']['evidence']
    assert not g['coverage']['observed_file_transfers'] and args == before

def test_empty_trace_does_not_invent_steps_or_prove_no_work():
    g = graph(trace_doc(fixture(reference_database='/db'), []))
    assert not g['tool_steps'] and not g['edges']
    assert 'No rule events recorded' in nodes(g)['no-reported-jobs']['label']
    assert 'absence of analytical work is not established' in nodes(g)['no-reported-jobs']['evidence']

def test_unknown_source_trace_retains_rule_names_without_guessing_tools():
    g = graph(trace_doc(fixture(), [job('1','protein_properties',['/a'],['/b'])]), False)
    assert g['tool_steps'][0]['label'] == 'Rule: protein_properties'
    assert g['tool_steps'][0]['tool'] == ''
    assert g['tool_steps'][0]['version'] == 'Not recorded'

def test_ambiguous_producers_do_not_create_false_or_cyclic_dependencies():
    g = graph(trace_doc(fixture(), [job('1','one',['/b'],['/a']),
        job('2','two',['/a'],['/b']), job('3','three',[],['/a'])]))
    assert any('ambiguous producers' in w for w in g['warnings'])
    assert g['coverage']['label'].startswith('Partial')
    tool_ids = {n['id'] for n in g['tool_steps']}
    assert not any(e['source'] in tool_ids and e['target'] in tool_ids for e in g['edges'])
    flow._levels(g['nodes'],g['edges'])

def test_failed_and_partial_trace_remain_visible():
    args = trace_doc(fixture(), [job('1','protein_properties',['/a'],['/b'],status='failed')])
    args[1]['runtime_evidence']['rule_trace'].update(coverage='partial', truncated=True, warnings=['Observer reached a bound'])
    g = graph(args)
    assert g['tool_steps'][0]['status'] == 'rule_failed'
    assert 'Observer reached a bound' in g['warnings']
    assert g['coverage']['label'].startswith('Partial')

def test_followup_rendering_and_disabled_extended_analysis_stay_distinct():
    g = graph(trace_doc(fixture('basis'), [job('1','safety_deep_report',['/evidence.json'],['/report.html'])]))
    assert g['tool_steps'][0]['tool'] == 'report'
    g = graph(fixture('basis', extend=False, modules={'pathogenfinder2':{'enabled':True}}))
    assert not any(n.get('tool') == 'pathogenfinder2' for n in g['nodes'])

def test_svg_escapes_untrusted_metadata_and_keeps_accessible_descriptions():
    args = fixture()
    args[0]['id'] = '<script>alert("x")</script>'
    args = trace_doc(args, [job('1','<svg onload="x">',['/in/<script>.faa'],['/out/a&b.json'])])
    g = graph(args)
    drawing = flow.svg(g)
    parsed = ET.fromstring(drawing)
    assert parsed.tag.endswith('svg') and b'<script>' not in drawing
    assert not any('onload' in el.attrib for el in parsed.iter())
    assert b'&lt;script&gt;' in drawing and b'&amp;' in drawing
    assert '<script>' in json.dumps(g)
    assert all(n['what'] and n['why'] and n['version_scope'] for n in g['tool_steps'])


def test_svg_routes_adjacent_dependencies_locally_and_long_spans_without_reused_lanes():
    simple = graph(fixture())
    _, _, _, routes = flow._layout(simple)
    assert all(route['lane'] is None for route in routes)
    assert all('H' not in route['path'] for route in routes[1:])
    complex_graph = graph(fixture('hybas', run_kraken=True, kraken_decision_mode='aware',
        polish_mode='tripolish', racon_rounds=2, run_polypolish=True,
        run_prokka=True, run_busco=True, run_multiqc=True))
    width, _, positions, routes = flow._layout(complex_graph)
    lanes = [route['lane'] for route in routes if route['lane'] is not None]
    assert len(lanes) > 10 and len(lanes) == len(set(lanes))
    assert max(lanes) < width
    for edge, route in zip(complex_graph['edges'], routes):
        source_row = positions[edge['source']][2]
        target_row = positions[edge['target']][2]
        assert (route['lane'] is None) == (target_row == source_row + 1)
