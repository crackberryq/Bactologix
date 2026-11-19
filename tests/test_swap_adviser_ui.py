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
    apply = source[source.index("else if(action==='use-swap-recommendation'){") + 5:
                   source.index("else if(action==='pipeline-power-preset'){")]
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
let saved=0,requests=0,renders=0,toasts=[];
const setDefaultSwapDraft=(tool,value)=>{U.drafts['compute-swap-'+tool]=value;};
const setSwapDraft=(scope,value)=>{U.drafts[scope+'-swap']=value;};
const invalidateSetupAdvice=()=>{};
const render=()=>{renders++;};
const request=()=>{requests++;};
const toast=(message,error)=>{toasts.push({message,error});};
const result={available:true,can_apply:true,message:'Capacity checked',proposal:{memory_mb:46080,swap_mb:68608},
 inputs:{estimated_kraken_mb:113868,ram_budget_mb:46080,calculated_swap_mb:68608,usable_swap_mb:72704},
 notice:'No OS change',warnings:[],problems:[]};
''' + helpers + '\nfunction use(scope,tool){const action="use-swap-recommendation",id=scope+":"+tool;\n' + apply + '\n}\n' + r'''
function remember(scope='defaults'){
 U.swapRecommendations[swapRecommendationKey(scope,'hybas')]={result:structuredClone(result),context:swapRecommendationContext(scope,'hybas'),receivedAt:Date.now()};
}
''' + setup + '\nconsole.log(JSON.stringify(' + expression + '));'
    completed = subprocess.run([NODE, '-e', script], capture_output=True, text=True, check=True)
    return json.loads(completed.stdout)


@pytest.mark.parametrize('scope', ['defaults', 'run', 'revision'])
def test_use_only_fills_local_form_and_never_sends_save_or_run(scope):
    value = evaluate('({drafts:U.drafts,requests,renders,toasts})', setup=f"remember('{scope}');use('{scope}','hybas');")
    field = 'compute-swap-hybas' if scope == 'defaults' else scope + '-swap'
    assert value['drafts'][field] == 68608
    assert value['requests'] == 0 and value['renders'] == 1
    assert 'Save' in value['toasts'][0]['message']


@pytest.mark.parametrize('change', [
    "U.drafts['compute-memory-hybas']=45056;",
    r"U.drafts[dbPath('hybas','kraken')]=String.raw`C:\Shared data\Kraken`;",
    "U.databaseStates.hybas.resets.kraken=true;",
    "S.databases.hybas.revision='two';",
    "S.active.hybas='different-source';",
    "U.defaultEditors.hybas.changes.database='different';",
    "U.epoch++;",
    "U.swapRecommendations['defaults:hybas'].receivedAt-=300001;",
])
def test_changed_or_expired_defaults_reject_apply(change):
    value = evaluate("({fresh:freshSwapRecommendation('defaults','hybas'),drafts:U.drafts,requests,toasts})",
                     setup="remember();" + change + "use('defaults','hybas');")
    assert not value['fresh']
    assert 'compute-swap-hybas' not in value['drafts']
    assert value['requests'] == 0 and value['toasts'][0]['error']


@pytest.mark.parametrize('change', [
    "U.runConfigs.hybas.changes['[\"db_root\"]']='/different';",
    "U.runConfigs.hybas.text='db_root: /different';",
    "U.drafts['run-memory']=40960;",
])
def test_unsaved_run_database_edits_invalidate_prior_recommendation(change):
    value = evaluate("({fresh:freshSwapRecommendation('run','hybas'),drafts:U.drafts})",
                     setup="remember('run');" + change + "use('run','hybas');")
    assert not value['fresh'] and 'run-swap' not in value['drafts']


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
remember();U.swapRecommendations['defaults:hybas'].result.can_apply=false;
U.swapRecommendations['defaults:hybas'].result.proposal=null;
U.swapRecommendations['defaults:hybas'].result.problems=['Swap capacity unavailable <review>'];
use('defaults','hybas');
""")
    assert '<button data-action="use-swap-recommendation" disabled>' in value['html']
    assert 'Swap capacity unavailable &lt;review&gt;' in value['html']
    assert 'compute-swap-hybas' not in value['drafts'] and value['requests'] == 0
