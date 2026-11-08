import json
from pathlib import Path
import pytest
from bactologix.aware_modes import resolve
from bactologix.run_config import validate, editor
from bactologix.aware_outputs import enabled
from bactologix.identification import enabled as review_enabled, waiting

@pytest.mark.parametrize('mode', ['off','taxonomy','auto','split'])
def test_mode_contract(mode):
    cfg=resolve({'aware_mode':mode,'run_kraken':False,'kraken_decision_mode':'off'})
    validate('hybas',json.dumps(cfg))
    assert cfg['run_kraken']==(mode!='off')
    assert enabled(cfg)==(mode!='off')
    assert cfg['aware_preserve_groups']==(mode=='split')
    if mode=='split':assert cfg['aware_run_until']=='aware'
    assert resolve(cfg)==cfg


def test_reconstruct_is_unavailable():
    with pytest.raises(ValueError,match='not implemented'):
        validate('hybas','aware_mode: reconstruct')


def test_shared_mode_contract_matches_pipeline():
    other=Path(__file__).resolve().parents[2]/'hybas/workflow/lib/aware_modes.py'
    if not other.is_file():pytest.skip('paired source not checked out')
    import bactologix.aware_modes as modes
    assert Path(modes.__file__).read_text()==other.read_text()


def test_auto_only_captures_an_actual_pause(tmp_path):
    run={'tool':'hybas','config':'aware_mode: auto','output':str(tmp_path)}
    assert review_enabled(run)
    report=tmp_path/'reports/identification/review.json';report.parent.mkdir(parents=True)
    report.write_text(json.dumps({'awaiting_review':False}))
    assert not waiting(run)
    report.write_text(json.dumps({'awaiting_review':True}))
    assert waiting(run)


def test_mode_exposed_without_changing_legacy_defaults():
    fields=editor('hybas','kraken_decision_mode: manual')['fields']
    mode=next(f for f in fields if f['name']=='aware_mode')
    assert mode['value']=='legacy'
    assert mode['options']==['legacy','off','taxonomy','auto','split']


from test_native import workspace, installation, sample

def test_auto_prepares_downstream_environments(workspace, installation, sample, monkeypatch):
    from bactologix.runs import create_run, command, template
    run=create_run(workspace,'hybas',sample,template('hybas'))
    run['config']+='\naware_mode: auto\n'
    monkeypatch.setattr('bactologix.engine.find_conda',lambda _: '/fixture/conda')
    for action in ('prepare','check-environments'):
        argv=command(workspace,run,action)
        assert argv[-2:]==['all','setup_envs']
    assert 'setup_envs' not in command(workspace,run,'run')
