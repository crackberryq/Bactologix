"""Saved-run shortcut must stay tied to an editable, memory-blocked HybAs draft."""
from pathlib import Path
import shutil
import subprocess

import pytest

from bactologix import hardware, readiness
from bactologix.runs import create_run
from bactologix.ui_backend import dispatch
from test_compute_memory import host
from test_native import installation, sample, workspace


def test_shortcut_uses_current_saved_draft_and_rechecks_editability(workspace, installation, sample, monkeypatch):
    folder = workspace.home / 'resources/hybas/db/standard'
    folder.mkdir(parents=True)
    for filename in ('hash.k2d', 'taxo.k2d', 'opts.k2d'):
        (folder / filename).write_bytes(b'index')
    info = host(swap=4096)
    monkeypatch.setattr(hardware, 'inventory', lambda *args, **kwargs: info)
    run = create_run(workspace, 'hybas', sample,
                     'run_kraken: true\nbactologix_compute: {swap_mb: 3074}\n', 1, 4096)
    def budget_rows():
        return {row['id']: row for row in readiness.panel(workspace, run)['rows']}
    assert budget_rows()['resources']['recalculate_swap']
    payload = {'id': run['id'], 'recalculate_swap': True}
    editor = dispatch(workspace, 'edit_run_settings', payload)
    assert editor['edit_run']['recalculate_swap'] and editor['edit_run']['id'] == run['id']
    manual = dispatch(workspace, 'edit_run_settings', {'id': run['id']})
    assert 'recalculate_swap' not in manual['edit_run']
    assert workspace.get('runs', run['id']) == run
    info['memory']['swap_available_mb'] += 3
    assert not budget_rows()['resources'].get('recalculate_swap')
    info['memory']['swap_available_mb'] -= 3
    run['last_action'] = 'run'
    workspace.put('runs', run['id'], run)
    assert not budget_rows()['resources'].get('recalculate_swap')
    with pytest.raises(ValueError, match='edited in place'):
        dispatch(workspace, 'edit_run_settings', payload)


@pytest.mark.skipif(shutil.which('node') is None, reason='Node.js unavailable')
def test_shortcut_reuses_current_editor_and_preserves_conflicting_edits():
    source = (Path(__file__).parents[1] / 'src/bactologix/ui/workspace.js').read_text()
    handlers = source[source.index('function calculateSavedDraftSwap('):source.index('function beforeUnload(')]
    script = """
const assert=require('node:assert/strict');
const U={view:'run'}, requests=[], calculations=[], conflicts=[];
const wizardKind=()=>U.view==='draft'?U.wizard?.kind:null;
const editingCurrentDraft=()=>U.revision?.can_edit_draft===true&&!U.revision.variant;
const $=()=>null, openWizard=()=>{U.view='draft';}, draftInputs=()=>{}, persistWizard=()=>{};
const request=(kind,payload)=>requests.push([kind,payload]);
const modal=(kind,id,data)=>conflicts.push([kind,id,data]);
const calculateSwap=(scope,tool)=>calculations.push([scope,tool]);
""" + handlers + """
editSavedRun('saved',false,true);
assert.deepEqual(requests,[['edit_run_settings',{id:'saved',recalculate_swap:true}]]);
U.wizard={kind:'edit-run',id:'another'};
editSavedRun('saved',false,true);
assert.equal(requests.length,1);
assert.deepEqual(conflicts,[['editor-conflict','saved',{variant:false,recalculateSwap:true}]]);
U.wizard.id='saved'; U.runConfigTab='review';
U.revision={id:'saved',tool:'hybas',can_edit_draft:true};
U.revisionConfig={text:'unsaved',changes:{example:2}};
editSavedRun('saved',false,true);
assert.equal(U.view,'draft'); assert.equal(U.runConfigTab,'settings'); assert.equal(U.runComputeOpen,true);
assert.deepEqual(U.revisionConfig,{text:'unsaved',changes:{example:2}});
assert.deepEqual(calculations,[['revision','hybas']]);
calculateSavedDraftSwap('another');
U.view='run'; calculateSavedDraftSwap('saved'); assert.equal(calculations.length,1);
U.view='draft'; U.revision.can_edit_draft=false; calculateSavedDraftSwap('saved'); assert.equal(calculations.length,1);
"""
    subprocess.run(['node', '-e', script], check=True)
