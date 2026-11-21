"""Swap advice stays bound to the form it reviewed and only fills local fields."""
import json
from pathlib import Path
import shutil
import subprocess

import pytest

NODE = shutil.which('node')
pytestmark = pytest.mark.skipif(not NODE, reason='Node is required for UI helper checks')
SOURCE = Path(__file__).parents[1] / 'src/bactologix/ui/workspace.js'


def evaluate(expression, *, setup=''):
    source = SOURCE.read_text()
    helpers = source[source.index('function swapRecommendationKey('):source.index('function syncDefaultCompute(')]
    helpers += source[source.index('function dbPath('):source.index('function dbState(')]
    script = r'''
const U={view:'configure',drafts:{'compute-memory-hybas':46080,'run-memory':46080,'revision-memory':46080},
 runConfigs:{hybas:{tool:'hybas',text:'db_root: /selected\n',changes:{}}},
 revision:{id:'saved-run',tool:'hybas',memory_mb:46080},
 revisionConfig:{tool:'hybas',text:'db_root: /saved\n',changes:{}},
 defaultEditors:{hybas:{tool:'hybas',text:'saved',savedText:'saved',changes:{}}},
 databaseStates:{hybas:{revision:'one',checks:{},resets:{}}},asyncTasks:{},swapRecommendations:{},epoch:0};
const S={resource_defaults:{hybas:{memory_mb:46080}},
 databases:{hybas:{revision:'one',rows:[{id:'kraken',path:'/saved/kraken'}]}},active:{hybas:'source-one'}};
const val=(key,fallback)=>U.drafts[key]??fallback;
const defaultEditor=tool=>U.defaultEditors[tool];
const defaultsDirty=c=>!!c&&(Object.keys(c.changes).length>0||c.text!==c.savedText);
const draftBudget=tool=>S.resource_defaults[tool];
const asyncContext=()=>JSON.stringify([U.view,U.epoch]);
const asyncCurrent=()=>true;
const e=x=>String(x??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const btn=(action,label,primary,id,icon,disabled)=>`<button data-action="${action}"${disabled?' disabled':''}>${e(label)}</button>`;
const $$=()=>[];
let requests=[],renders=0,toasts=[];
const persistWizard=()=>{};
const autoSwapSelected=()=>U.autoSwap===true;
const setDefaultSwapDraft=(tool,value)=>{U.drafts['compute-swap-'+tool]=value;U.drafts['compute-swap-enabled-'+tool]=value>0;};
const setSwapDraft=(scope,value)=>{U.drafts[scope+'-swap']=value;U.drafts[scope+'-swap-enabled']=value>0;};
const invalidateSetupAdvice=()=>{};
const render=()=>{renders++;};
const request=(kind,payload,background,swap)=>requests.push({kind,payload,swap});
const toast=(message,error)=>{toasts.push({message,error});};
const result={available:true,can_apply:true,message:'Capacity checked',proposal:{memory_mb:46080,swap_mb:71766},
 inputs:{estimated_kraken_mb:113868,ram_budget_mb:46080,calculated_swap_mb:71766,usable_swap_mb:72704},
 notice:'No OS change',warnings:[],problems:[]};
''' + helpers + r'''
function finish(resultOverride=result){receiveSwapRecommendation(requests.at(-1)?.swap,resultOverride);}
''' + setup + '\nconsole.log(JSON.stringify(' + expression + '));'
    completed = subprocess.run([NODE, '-e', script], capture_output=True, text=True, check=True)
    return json.loads(completed.stdout)


@pytest.mark.parametrize('scope', ['defaults', 'run', 'revision'])
def test_single_calculation_fills_only_local_form(scope):
    value = evaluate(f"({{drafts:U.drafts,requests,renders,html:swapRecommendationPanel('{scope}','hybas')}})",
                     setup=f"calculateSwap('{scope}','hybas');finish();")
    field = 'compute-swap-hybas' if scope == 'defaults' else scope + '-swap'
    assert value['drafts'][field] == 71766
    assert [r['kind'] for r in value['requests']] == ['recommend_swap']
    assert value['renders'] == 1 and 'Allowance filled. Save' in value['html']


@pytest.mark.parametrize('change', [
    "U.drafts['compute-memory-hybas']=45056;",
    r"U.drafts[dbPath('hybas','kraken')]=String.raw`C:\Shared data\Kraken`;",
    "U.databaseStates.hybas.resets.kraken=true;",
    "S.databases.hybas.revision='two';",
    "S.active.hybas='different-source';",
    "U.defaultEditors.hybas.changes.database='different';",
    "U.epoch++;",
    "U.drafts['compute-swap-enabled-hybas']=true;U.drafts['compute-swap-hybas']=8192;",
    "U.autoSwap=true;",
    "requests.at(-1).swap.requestedAt-=300001;",
])
def test_changed_defaults_reject_pending_response(change):
    value = evaluate("({drafts:U.drafts,rows:U.swapRecommendations})",
                     setup="calculateSwap('defaults','hybas');" + change + "finish();")
    assert value['rows'] == {}
    assert value['drafts'].get('compute-swap-hybas') != 71766


@pytest.mark.parametrize('change', [
    "U.runConfigs.hybas.changes['[\"db_root\"]']='/different';",
    "U.runConfigs.hybas.text='db_root: /different';",
    "U.drafts['run-memory']=40960;",
    "U.drafts['run-swap-enabled']=true;U.drafts['run-swap']=512;",
])
def test_unsaved_run_database_edits_invalidate_prior_recommendation(change):
    value = evaluate("({rows:U.swapRecommendations,drafts:U.drafts})",
                     setup="calculateSwap('run','hybas');" + change + "finish();")
    assert value['rows'] == {} and value['drafts'].get('run-swap') != 71766


def test_result_cannot_cross_saved_runs():
    value = evaluate("({rows:U.swapRecommendations,drafts:U.drafts})", setup="""
calculateSwap('revision','hybas');U.revision.id='another-saved-run';finish();
""")
    assert value['rows'] == {} and 'revision-swap' not in value['drafts']


def test_manual_override_after_success_is_visible_and_stays_in_form():
    value = evaluate("({drafts:U.drafts,fresh:freshSwapRecommendation('revision','hybas')})", setup="""
calculateSwap('revision','hybas');finish();U.drafts['revision-swap']=25000;
""")
    assert value['drafts']['revision-swap'] == 25000 and not value['fresh']


def test_picker_override_applies_only_to_default_preview_and_distinguishes_reset():
    value = evaluate('({savedInputs, selected, reset, run})', setup=r"""
const savedInputs=swapRecommendationInputs('defaults','hybas');
U.drafts[dbPath('hybas','kraken')]=String.raw`C:\Shared data\Kraken`;
const selected=swapRecommendationInputs('defaults','hybas');
const run=swapRecommendationInputs('run','hybas');
U.databaseStates.hybas.resets.kraken=true;
const reset=swapRecommendationInputs('defaults','hybas');
""")
    assert 'kraken_path' not in value['savedInputs'] and 'kraken_path' not in value['run']
    assert value['selected']['kraken_path'] == r'C:\Shared data\Kraken'
    assert value['reset']['kraken_path'] is None


def test_run_request_contains_current_editor_fields_and_saved_revision_id():
    value = evaluate("swapRecommendationInputs('revision','hybas')", setup="U.revisionConfig.changes['[\"db_root\"]']='/new';")
    assert value['run_id'] == 'saved-run'
    assert value['config']['text'] == 'db_root: /saved\n'
    assert value['config']['changes'] == {'["db_root"]': '/new'}


def test_capacity_failure_is_visible_and_cannot_fill_values():
    value = evaluate("({html:swapRecommendationPanel('defaults','hybas'),drafts:U.drafts,requests})", setup="""
calculateSwap('defaults','hybas');
finish({...result,can_apply:false,proposal:null,problems:['Swap capacity unavailable <review>']});
""")
    assert 'Calculation could not fill an allowance' in value['html']
    assert 'Swap capacity unavailable &lt;review&gt;' in value['html']
    assert 'compute-swap-hybas' not in value['drafts'] and len(value['requests']) == 1
