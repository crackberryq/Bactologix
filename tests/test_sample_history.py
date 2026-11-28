"""History retains evidence boundaries, distinct attempts and valid artifacts."""
import copy
import json
import re
import xml.etree.ElementTree as ET
import pytest
from bactologix import isolate_inventory, sample_history as history
from bactologix.store import Store

def run(rid='run-one', **extra):
    return dict(id=rid, tool='hybas', label=rid, sample={'id':'isolate-A','path':'/data/isolate-A','mode':'hybrid'},
                folder='/workspace/runs/'+rid, output='/workspace/runs/'+rid+'/outputs/isolate-A',
                created='2026-11-01T10:00:00+00:00', commit='a'*40, config_sha256='b'*64,
                workflow_sha256='c'*64, config='polish_mode: medaka\nracon_rounds: 9\n', inputs=[], **extra)

def job(jid, status='succeeded', action='run'):
    return dict(id=jid,kind='workflow',status=status,payload={'run_id':'run-one','action':action},
                created='2026-11-02T10:00:00+00:00',started='2026-11-02T10:00:01+00:00',finished='2026-11-02T10:01:00+00:00')

def receipt(jid, kind='methods_attempt_evidence', **evidence):
    return {'kind':kind,'id':'run-one' if kind=='methods_evidence' else jid,
            'body':dict(schema=1,run_id='run-one',job_id=jid,commit='a'*40,config_sha256='b'*64,workflow_sha256='c'*64,
                        captured_at='2026-11-02T10:01:00+00:00',completed='2026-11-02T10:01:00+00:00',
                        evidence={'decisions':{},'versions':{},'records':[],'files':[],**evidence})}

@pytest.fixture
def inventory(monkeypatch):
    doc={'sample':{'id':'isolate-A','path':'/data/isolate-A'},'runs':[run()],'edges':[],'jobs':[],'records':[],
         'roots':[],'warnings':[],'missing':[],'signature':'d'*64}
    monkeypatch.setattr(isolate_inventory,'build',lambda store,sid:copy.deepcopy(doc))
    monkeypatch.setattr(history,'now',lambda:'2026-11-09T10:00:00+00:00')
    return doc

def nodes(graph,kind):
    return [n for n in graph['nodes'] if n['kind']==kind]

def dag(graph):
    ids={n['id']:n for n in graph['nodes']}
    assert len(ids)==len(graph['nodes'])
    for edge in graph['edges']:
        assert ids[edge['source']]['level'] < ids[edge['target']]['level']

def test_plan_cannot_become_execution(inventory):
    g=history.build(None,'isolate-A')
    assert nodes(g,'run')[0]['status']=='draft'
    assert not nodes(g,'stage') and not nodes(g,'attempt')
    assert '9 rounds' not in json.dumps(g) and g['coverage']['state']=='partial'
    dag(g)

@pytest.mark.parametrize('status',['failed','cancelled','interrupted','running','queued'])
def test_old_success_never_masks_latest_attempt(inventory,status):
    inventory['runs'][0]['completed']='2026-11-02T10:01:00+00:00'
    inventory['jobs']=[job('check',action='dry-run'),job('new',status),job('old')]
    inventory['records']=[receipt('old','methods_evidence',decisions={'executed_stages':['racon1']})]
    g=history.build(None,'isolate-A')
    assert nodes(g,'run')[0]['status']==status
    latest=next(n for n in nodes(g,'attempt') if n['details']['job_id']=='new')
    assert latest['status']==status and latest['details']['step_coverage']=='not_recorded'
    assert nodes(g,'stage')[0]['details']['job_id']=='old'
    dag(g)

def test_explicit_executed_skipped_incomplete_and_failed_stages(inventory):
    inventory['jobs']=[job('one')]
    inventory['runs'][0]['completed']='2026-11-02T10:01:00+00:00'
    inventory['records']=[receipt('one',decisions={'executed_stages':['racon1','medaka'],'skipped_stages':{'polypolish':'no_reads'}},
        records=[{'module':'a','execution_status':'completed','complete':False},
                 {'module':'b','execution_status':'failed'},{'module':'c','execution_status':'not_applicable'}])]
    g=history.build(None,'isolate-A')
    assert {n['label']:n['status'] for n in nodes(g,'stage')}=={'racon1':'executed','medaka':'executed','polypolish':'skipped','a':'incomplete','b':'failed','c':'not_applicable'}
    assert nodes(g,'run')[0]['status']=='completed'
    dag(g)

def test_per_attempt_snapshot_precedence_and_history(inventory):
    inventory['jobs']=[job('new'),job('old')]
    inventory['records']=[receipt('new','methods_evidence',decisions={'executed_stages':['wrong-copy']}),
                          receipt('old',decisions={'executed_stages':['racon1']}),receipt('new',decisions={'executed_stages':['medaka']})]
    g=history.build(None,'isolate-A')
    assert {(n['label'],n['details']['job_id']) for n in nodes(g,'stage')}=={('racon1','old'),('medaka','new')}

def test_retained_plan_does_not_imply_stage_execution(inventory):
    inventory['jobs']=[job('one','failed')]
    inventory['records']=[receipt('one',decisions={'planned_stages':['racon1','medaka'],'executed_stages':['racon1']})]
    g=history.build(None,'isolate-A')
    assert {n['label']:n['status'] for n in nodes(g,'stage')}=={'racon1':'executed','medaka':'planned'}
    assert nodes(g,'attempt')[0]['status']=='failed'

@pytest.mark.parametrize('field,value',[('commit','e'*40),('config_sha256','e'*64),('workflow_sha256','e'*64),('job_id','missing')])
def test_mismatched_receipt_has_no_execution_evidence(inventory,field,value):
    inventory['jobs']=[job('one')]
    row=receipt('one',decisions={'executed_stages':['medaka']})
    row['body'][field]=value
    inventory['records']=[row]
    g=history.build(None,'isolate-A')
    assert not nodes(g,'stage')
    assert any('matching run/attempt/source identity' in w for w in g['warnings'])

def test_conflicting_record_becomes_unknown(inventory):
    inventory['jobs']=[job('one')]
    inventory['records']=[receipt('one',decisions={'executed_stages':['medaka'],'skipped_stages':{'medaka':'disabled'}})]
    g=history.build(None,'isolate-A')
    assert nodes(g,'stage')[0]['status']=='unknown'
    assert any('conflicting' in w.lower() for w in g['warnings'])

def test_invalid_cycles_do_not_create_guessed_lineage(inventory):
    inventory['runs'] += [run('run-two'),run('run-three')]
    inventory['edges']=[{'source':'run-one','target':'run-two','relation':'parent'},
                        {'source':'run-two','target':'run-one','relation':'parent'},
                        {'source':'missing','target':'run-three','relation':'parent'}]
    g=history.build(None,'isolate-A')
    assert [e for e in g['edges'] if e['source'].startswith('run:')]==[{'source':'run:run-one','target':'run:run-two','relation':'parent'}]
    assert any('cyclic' in w for w in g['warnings']) and any('missing record' in w for w in g['warnings'])
    dag(g)

def test_legacy_completion_and_report_rendering_are_not_analyses(inventory):
    inventory['runs'][0]['completed']='2026-11-02T10:01:00+00:00'
    item=job('render');item['kind']='report_refresh';inventory['jobs']=[item]
    g=history.build(None,'isolate-A')
    assert nodes(g,'run')[0]['status']=='unknown'
    assert len(nodes(g,'rendering'))==1 and not nodes(g,'stage')

def test_identification_stage_is_not_downstream_completion(inventory):
    inventory['jobs']=[job('identify')]
    inventory['runs'][0]['identification']={'state':'awaiting_review','job':'identify','completed':'2026-11-02T10:01:00+00:00','evidence_sha256':'d'*64}
    g=history.build(None,'isolate-A')
    assert nodes(g,'run')[0]['status']=='awaiting_review'
    assert [(n['label'],n['status']) for n in nodes(g,'stage')]==[('Identification stage','completed')]

def test_followup_retains_history_after_output_ownership_changes(inventory):
    inventory['runs'][0].update(run_type='followup',parent='parent',completed='2026-11-02T10:01:00+00:00')
    inventory['jobs']=[job('one')]
    inventory['records']=[{'kind':'followup_output_owner','id':'parent','body':'other-followup'}]
    g=history.build(None,'isolate-A')
    n=nodes(g,'run')[0]
    assert n['status']=='completed' and n['details']['shared_output_available'] is False
    assert any('shared follow-up outputs' in w for w in g['warnings'])

def test_artifacts_escape_content_and_pdf_cross_references_are_valid(inventory):
    inventory['sample']['id']='Sample <script>alert(1)</script> & μ'
    inventory['runs'][0]['label']='<img src=x onerror=alert(1)> (\\)'
    inventory['jobs']=[job('attempt-'+str(i)) for i in range(12)]
    files=history.bundle_files(None,'isolate-A')
    assert set(files)=={'history.json','history.svg','history.pdf','history.html'}
    assert files==history.bundle_files(None,'isolate-A')
    assert json.loads(files['history.json'])['sample']['id']==inventory['sample']['id']
    drawing=ET.fromstring(files['history.svg'])
    assert drawing.tag=='{http://www.w3.org/2000/svg}svg'
    assert not any(e.tag.endswith('script') or e.tag.endswith('image') for e in drawing.iter())
    page=files['history.html'].decode()
    assert '<script>' not in page and '<img src=x' not in page and '&lt;script&gt;' in page
    pdf=files['history.pdf']
    assert pdf.startswith(b'%PDF-1.4') and pdf.endswith(b'%%EOF\n')
    start=int(re.search(rb'startxref\n(\d+)\n',pdf).group(1))
    assert pdf[start:start+4]==b'xref'
    for index,entry in enumerate(pdf[start:].splitlines()[3:],1):
        if not re.fullmatch(rb'\d{10} 00000 n ',entry):break
        assert pdf[int(entry[:10]):].startswith(f'{index} 0 obj\n'.encode())
    assert b'/Count 2 ' in pdf and b'[U+03BC]' in pdf

def test_real_inventory_has_no_mutations(tmp_path):
    store=Store(tmp_path/'workspace')
    original=tmp_path/'isolate-A';original.mkdir()
    sample={'id':'isolate-A','path':str(original),'mode':'hybrid'}
    store.put('samples',sample['id'],sample)
    saved=run();saved.update(sample=sample,folder=str(store.home/'runs/run-one'),output=str(store.home/'runs/run-one/outputs/isolate-A'))
    store.put('runs',saved['id'],saved)
    jid=store.submit('workflow',{'run_id':saved['id'],'action':'run'})
    store.update_job(jid,status='failed',finished='2026-11-02T10:01:00+00:00')
    before=(store.all('runs'),store.jobs())
    g=history.build(store,'isolate-A')
    assert nodes(g,'run')[0]['status']=='failed' and not nodes(g,'stage')
    assert (store.all('runs'),store.jobs())==before
    dag(g)

def test_svg_long_edges_do_not_cross_unrelated_cards(inventory):
    inventory['jobs']=[job('attempt-'+str(i)) for i in range(15)]
    graph=history.build(None,'isolate-A')
    drawing=ET.fromstring(history.svg(graph))
    boxes=[]
    for rect in drawing.iter('{http://www.w3.org/2000/svg}rect'):
        if rect.get('width')=='320':
            x,y,w,h=(float(rect.get(k)) for k in ('x','y','width','height'))
            boxes.append((x,y,x+w,y+h))
    for path in drawing.iter('{http://www.w3.org/2000/svg}path'):
        if path.get('marker-end') is None:continue
        coords=re.findall(r'([MVH])([0-9.]+)(?: ([0-9.]+))?',path.get('d'))
        position=None
        for command,a,b in coords:
            if command=='M':target=(float(a),float(b))
            elif command=='V':target=(position[0],float(a))
            else:target=(float(a),position[1])
            if position is not None:
                x1,y1=position;x2,y2=target
                for left,top,right,bottom in boxes:
                    if x1==x2:
                        assert not (left<x1<right and max(min(y1,y2),top)<min(max(y1,y2),bottom))
                    else:
                        assert not (top<y1<bottom and max(min(x1,x2),left)<min(max(x1,x2),right))
            position=target

