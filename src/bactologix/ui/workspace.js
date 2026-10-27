export default function(component) {
"use strict";
const {parentElement, data, setTriggerValue} = component;
const root = parentElement.querySelector(".bacto-root");
root._dispose?.();
const icons={home:'M3 10 12 3l9 7M5 9v12h14V9M9 21v-7h6v7',samples:'M5 3h14v18H5zM9 8h6M9 12h6M9 16h4',install:'M12 3v12m-5-5 5 5 5-5M4 16v5h16v-5',resources:'M4 6c0-4 16-4 16 0s-16 4-16 0m0 0v12c0 4 16 4 16 0V6M4 12c0 4 16 4 16 0',config:'M4 7h16M4 17h16M8 4v6m8 4v6',jobs:'M8 5h12v16H4V5h4m0-2h8v4H8zM8 12h8m-8 4h5',compare:'M5 20V10m7 10V4m7 16v-7M3 21h18',plus:'M12 5v14M5 12h14',arrow:'M5 12h14m-5-5 5 5-5 5',clone:'M8 8h12v12H8zM4 16H3V3h13v1',check:'m5 12 4 4 10-10',clock:'M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18m0 4v5l3 2',close:'m6 6 12 12M6 18 18 6',external:'M14 3h7v7m0-7-11 11M10 5H4v15h15v-6',play:'m8 4 12 8-12 8z',stop:'M6 6h12v12H6z',info:'M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18m0 7v6m0-9v.01',chevron:'m9 5 7 7-7 7',folder:'M3 7h7l2-3h9v16H3z',gear:'M12 8a4 4 0 1 0 0 8 4 4 0 0 0 0-8M12 2v3m0 14v3M2 12h3m14 0h3M5 5l2 2m10 10 2 2M5 19l2-2M17 7l2-2',file:'M5 3h9l5 5v13H5zM14 3v6h5M9 13h6m-6 4h6',cpu:'M7 7h10v10H7zM9 2v3m6-3v3M9 19v3m6-3v3M2 9h3m-3 6h3m14-6h3m-3 6h3',branch:'M6 3v14a4 4 0 0 0 4 4m-4-9h8a4 4 0 0 0 4-4V3',trash:'M3 6h18M9 6V3h6v3M5 6l1 15h12l1-15M10 10v7m4-7v7',download:'M12 3v12m-4-4 4 4 4-4M4 18v3h16v-3'};
const icon=n=>`<svg class="icon" viewBox="0 0 24 24" aria-hidden="true"><path d="${icons[n]||icons.file}"/></svg>`;

const e=x=>String(x??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const $=s=>root.querySelector(s), $$=s=>[...root.querySelectorAll(s)];
const U=root._workspaceState ||= {view:'overview',modal:null,drafts:{},selected:[],tool:'hybas',filter:'',handled:null,pending:null,fields:{}};
const S=data.workspace;
const wizardKey='bactologix:run-editor:v1:'+S.workspace;
const wizardKind=()=>U.view==='draft'?U.wizard?.kind:null;
const wizardKeys=['wizard','runConfigs','revision','revisionConfig','runConfigTab','runSettingTab','runConfigGroup','runBudgets','runBudgetTool','budgetDefaults'];
function persistWizard(){
 if(!U.wizard)return;
 try{
  const saved=Object.fromEntries(wizardKeys.map(k=>[k,U[k]]));
  saved.drafts=Object.fromEntries(Object.entries(U.drafts).filter(([k])=>/^(run-|revision-)/.test(k)));
  sessionStorage.setItem(wizardKey,JSON.stringify({schema:1,workspace:S.workspace,version:S.version,saved}));U.wizardStorageError=false;
 }catch{U.wizardStorageError=true;}
 const status=$('#wizard-save-status');if(status)status.textContent=wizardSaveStatus();
}
function wizardSaveStatus(){return U.wizardStorageError?'Browser recovery unavailable — keep this page open.':'Edits saved in this browser tab. No run created yet.';}
function restoreWizard(){
 if(U.wizardLoaded)return;U.wizardLoaded=true;
 try{const text=sessionStorage.getItem(wizardKey);if(!text)return;const record=JSON.parse(text),saved=record.saved;
  if(record.schema!==1||record.workspace!==S.workspace||!['new-run','edit-run'].includes(saved?.wizard?.kind))return;
  for(const k of wizardKeys)U[k]=saved[k];Object.assign(U.drafts,saved.drafts||{});U.wizardRecovered=true;
 }catch{U.wizardStorageError=true;}
}
function clearWizard(){
 $('.run-wizard')?.remove();
 U.wizard=null;U.wizardRecovered=false;U.revision=null;U.revisionConfig=null;U.runConfigs={};
 for(const key of Object.keys(U.drafts))if(/^(run-|revision-)/.test(key))delete U.drafts[key];
 try{sessionStorage.removeItem(wizardKey);}catch{/* No stored recovery data is available. */}
}
function openWizard(){U.view='draft';U.modal=null;U.error='';writeRoute();render();$('#main').focus({preventScroll:true});$('.workspace-shell').scrollIntoView({block:'start'});}
function wizardBanner(){return U.wizard&&U.view!=='draft'?`<section class="draft-recovery" aria-label="Unfinished draft"><div><strong>Unfinished ${U.wizard.kind==='edit-run'?'run revision':'run draft'}</strong><p>${e(wizardSaveStatus())}</p></div><div class="button-row">${btn('resume-wizard','Resume editing',true)}${btn('discard-wizard','Discard edits')}</div></section>`:'';}
function beforeUnload(event){draftInputs();persistWizard();if(U.wizardStorageError&&U.wizard){event.preventDefault();event.returnValue='';}}
restoreWizard();
let pollTimer, toastTimer;
const navs=[['overview','Overview','home'],['samples','Samples','samples'],['runs','Runs','play'],['comparisons','Comparisons','compare'],['jobs','Jobs & progress','jobs'],['reports','Results & reports','file'],['install','Install & update','install'],['resources','Databases & models','resources'],['configure','Configuration','config']];
const btn=(action,label,primary=false,id='',ico='',disabled=false)=>`<button type="button" class="btn${primary?' primary':''}" data-action="${action}" data-id="${e(id)}" ${disabled?'disabled':''}>${ico?icon(ico):''}${e(label)}</button>`;
const quiet=(action,label,id='')=>`<button type="button" class="quiet" data-action="${action}" data-id="${e(id)}">${e(label)} ${icon('arrow')}</button>`;
const badge=(label,color='')=>`<span class="pill ${color}">${e(label)}</span>`;
const statusBadge=s=>badge(s,['completed','succeeded','checked','ready'].includes(s)?'green':['running','queued'].includes(s)?'blue':['failed','interrupted'].includes(s)?'red':'');
const heading=(eyebrow,title,desc,actions='')=>`<div class="page-heading"><div><div class="eyebrow">${eyebrow}</div><h1>${title}</h1><p class="subtitle">${desc}</p></div><div class="button-row">${actions}</div></div>`;
const empty=(title,desc,action='')=>`<div class="empty">${icon('folder')}<h3>${title}</h3><p>${desc}</p>${action}</div>`;
const notice=(title,text,amber=false)=>`<div class="notice${amber?' amber':''}">${icon('info')}<div><strong>${title}</strong><p>${text}</p></div></div>`;
const activeInstall=t=>S.installs.find(i=>i.id===S.active[t]);
const runBy=id=>S.runs.find(r=>r.id===id);
const visibleRuns=()=>S.runs.filter(r=>!r.archived);
U.selected=U.selected.filter(id=>visibleRuns().some(r=>r.id===id));
const val=(id,fallback='')=>U.drafts[id]??fallback;
const field=(id,label,value='',type='text',extra='')=>`<div class="field"><label for="${id}">${label}</label><input id="${id}" type="${type}" value="${e(val(id,value))}" ${extra}></div>`;
const area=(id,label,value='',extra='')=>`<div class="field wide"><label for="${id}">${label}</label><textarea id="${id}" ${extra}>${e(val(id,value))}</textarea></div>`;
const select=(id,label,options,selected='')=>`<div class="field"><label for="${id}">${label}</label><select id="${id}">${options.map(([v,t])=>`<option value="${e(v)}" ${String(val(id,selected))===String(v)?'selected':''}>${e(t)}</option>`).join('')}</select></div>`;
function renderToast(){const t=$('#toast'),saved=U.toast;clearTimeout(toastTimer);if(!saved||saved.until<=Date.now()){t.classList.remove('show');U.toast=null;return;}t.textContent=saved.message;t.classList.add('show');t.classList.toggle('toast-error',saved.error);toastTimer=setTimeout(()=>{t.classList.remove('show');U.toast=null;},saved.until-Date.now());}
function toast(message,error=false){U.toast={message,error,until:Date.now()+6500};renderToast();}
function download(name,content,type='text/plain'){const url=URL.createObjectURL(new Blob([content],{type}));const a=document.createElement('a');a.href=url;a.download=name;root.append(a);a.click();a.remove();setTimeout(()=>URL.revokeObjectURL(url),10000);}
function request(kind,payload={},background=false){if(U.pending){if(!background&&U.background){U.queuedRequest={kind,payload:structuredClone(payload)};renderBusy();}return;}U.background=background;U.requestOrigin={kind,view:U.view,runId:U.runId};U.pending=Array.from(crypto.getRandomValues(new Uint32Array(4)),x=>x.toString(16)).join('-');setTriggerValue('action',{id:U.pending,kind,payload});renderBusy();}
function renderBusy(){const locked=!!U.queuedRequest||!!U.pending&&!U.background;root.classList.toggle('is-busy',locked);const status=$('#connection-status');if(status)status.textContent=U.pending?'Working…':'Workspace synced';for(const el of $$('input,textarea,select,button[data-action]:not([data-action="close"])')){if(locked&&!el.disabled){el.disabled=true;el.dataset.pendingDisabled='true';}else if(!locked&&el.dataset.pendingDisabled){el.disabled=false;delete el.dataset.pendingDisabled;}}}
function routeHash(){return U.view==='draft'?'#drafts/'+(U.wizard?.kind==='edit-run'?'revise/'+encodeURIComponent(U.wizard.id):'new'):U.view==='run'?'#runs/'+encodeURIComponent(U.runId)+'/'+(U.runDetailTab||'overview'):U.view==='comparisons'?'#comparisons/'+(U.comparisonTab||'review'):'#'+U.view;}
function writeRoute(replace=false){const hash=routeHash();U.routeApplied=hash;if(window.location.hash!==hash)window.history[replace?'replaceState':'pushState'](null,'',hash);}
function readRoute(){
 const hash=window.location.hash;if(hash===U.routeApplied)return false;
 let parts;try{parts=hash.replace(/^#/,'').split('/').map(decodeURIComponent);}catch{parts=['overview'];}
 const key=parts[0]||'overview';
 if(key==='drafts'){U.view='draft';}
 else if(key==='runs'&&parts[1]){U.view='run';U.runId=parts[1];U.detail=null;U.runDetailTab=['overview','kraken','assembly','quality','databases','advanced','modules','aware','deep','resources','activity','results','methods'].includes(parts[2])?parts[2]:'overview';}
 else if(['compare','experiments','comparisons'].includes(key)){U.view='comparisons';U.comparisonTab=key==='experiments'||parts[1]==='create'?'create':'review';}
 else if(navs.some(n=>n[0]===key))U.view=key;
 else return false;
 U.routeApplied=hash;U.error='';return true;
}
function routeChanged(){draftInputs();if(readRoute()){if(!['new-run','edit-run'].includes(U.modal?.kind))U.modal=null;render();$('#main').focus({preventScroll:true});}}
function navigate(view){draftInputs();U.view=view;U.modal=null;U.error='';if(['compare','experiments'].includes(view)){U.view='comparisons';U.comparisonTab=view==='experiments'?'create':'review';}writeRoute();render();$('#main').focus({preventScroll:true});$('.workspace-shell').scrollIntoView({block:'start'});}
function openRun(id,tab='overview',replace=false){draftInputs();U.view='run';U.runId=id;U.runDetailTab=tab;U.modal=null;U.error='';U.detail=null;writeRoute(replace);render();$('#main').focus({preventScroll:true});$('.workspace-shell').scrollIntoView({block:'start'});}

function modal(kind,id='',options={}){U.modal={kind,id,...options};renderModal();$('.modal input,.modal select,.modal button')?.focus();}
function closeModal(){if(U.modal?.kind==='folders'&&U.browserReturn){U.modal=U.browserReturn;U.browserReturn=null;U.error='';renderModal();return;}U.modal=null;U.browserReturn=null;$('#modal-root').innerHTML='';$('.app-shell').removeAttribute('inert');render();}
function setDraft(id,value){U.drafts[id]=value;const el=$('#'+id);if(el)el.value=value;}
function browse(target,path='',nearest=false){U.browserReturn=U.modal?{...U.modal}:null;U.browser=null;U.drafts['folder-query']='';U.drafts['folder-hidden']=false;modal('folders',target);request('browse_folders',{path,nearest});}
function draftInputs(){for(const el of $$('input[id],textarea[id],select[id]')){if(el.type==='file')continue;U.drafts[el.id]=el.type==='checkbox'?el.checked:el.value;}if(wizardKind()==='new-run'&&U.runBudgetTool&&$('#run-cores')){U.runBudgets||={};U.runBudgets[U.runBudgetTool]={cores:val('run-cores'),memory_mb:val('run-memory')};}}
function draftBudget(tool){U.runBudgets||={};U.budgetDefaults||={};const defaults=S.resource_defaults?.[tool]||{cores:4,memory_mb:16000},stamp=JSON.stringify(defaults);if(U.budgetDefaults[tool]!==stamp){delete U.runBudgets[tool];U.budgetDefaults[tool]=stamp;}return U.runBudgets[tool]||defaults;}
function krakenLabel(r){return r.tool==='hybas'?({off:'Off',manual:'Manual',auto:'Auto',aware:'AWARE'}[r.kraken_mode]||r.kraken_mode||'Pipeline default'):'—';}
function readMode(mode){return ({hybrid:'Hybrid',illumina_only:'Illumina only',ont_only:'ONT only'}[mode]||String(mode).replaceAll('_',' '));}
function runTable(runs,checks=false,archived=false,context='runs'){
 if(!runs.length)return empty(archived?'No archived runs.':'No runs in this list.',archived?'Archived runs retain their saved configuration and outputs.':'Change the filters or create a run draft.',!archived?btn('new-run','Create run drafts',true):'');
 return `<div class="table-wrap"><table><thead><tr>${checks?'<th>Select</th>':''}<th>Saved run</th><th>Kraken mode</th><th>Polishing</th><th>Status</th><th>Actions</th></tr></thead><tbody>${runs.map(r=>`<tr data-run-row="${e(r.id)}">${checks?`<td><input type="checkbox" data-select="${e(r.id)}" aria-label="Select ${e(r.label)}" ${U.selected.includes(r.id)?'checked':''}></td>`:''}<td><a class="sample-name" href="#runs/${encodeURIComponent(r.id)}/overview" data-action="run-detail" data-id="${e(r.id)}">${e(r.label)}</a><span class="secondary-line">${e(r.sample.id)} · ${e(S.pipelines[r.tool].name)}</span><span class="secondary-line run-identity">${e(r.id)}</span></td><td>${badge(krakenLabel(r),r.kraken_mode==='off'?'off':'')}</td><td>${e(r.strategy)}</td><td>${r.archived?badge('Archived'):statusBadge(r.status)}</td><td><div class="table-actions">${context==='parent'?btn('polish','Use as parent',false,r.id,'branch',r.busy):quiet('run-detail','Open run',r.id)}${context==='runs'?(r.archived?btn('restore-run','Restore',false,r.id,'arrow'):btn('archive-run','Archive',false,r.id,'trash',r.busy)):''}</div></td></tr>`).join('')}</tbody></table></div>`;
}

function pipelineRows(){return ['hybas','basis'].map(t=>{const i=activeInstall(t)||S.installs.find(x=>x.tool===t);return `<div class="pipeline-row"><div class="pipeline-logo ${t}">${t==='hybas'?'H':'B'}</div><div class="pipeline-info"><strong>${S.pipelines[t].name}</strong><small>${e(i?.version.ref||'Choose a version to get started')}</small></div>${badge(i?.setup_state||'Not installed',i?.ready?'green':'amber')}</div>`;}).join('');}
function overview(){
 const active=S.jobs.filter(j=>['queued','running'].includes(j.status)),recent=visibleRuns().slice(0,5);
 const failures=['failed','interrupted'];
 const attention=visibleRuns().filter(r=>failures.includes(r.status)).map(r=>{
  const jobs=S.jobs.filter(j=>j.payload.run_id===r.id),execution=jobs.find(j=>j.payload.action==='run');
  return execution&&failures.includes(execution.status)?execution:jobs.find(j=>failures.includes(j.status));
 }).filter(Boolean).sort((a,b)=>b.created.localeCompare(a.created)).slice(0,3);
 const next=!S.installs.some(i=>i.ready)?['Set up a pipeline','Choose a pipeline and prepare its runner.','go-install','Set up pipelines']:!S.samples.length?['Add your input folders','Register a sample folder to make it available for new drafts.','new-sample','Add sample']:!visibleRuns().length?['Create your first run','Choose samples, review settings and save a draft before launching.','new-run','Create run drafts']:null;
 return heading('WORKSPACE OVERVIEW','Pick up where you left off.','Your saved runs, active work and next steps.',S.samples.length?btn('new-run','Create run drafts',true,'','plus'):btn('new-sample','Add samples',true,'','plus'))+
 `<div class="stats-grid">${[['Samples',S.samples.length,'Registered input folders','samples'],['Runs',visibleRuns().length,'Current drafts and history','play'],['Active jobs',active.length,'Queued or running','jobs'],['Reports',S.reports.length,'Native outputs and added reports','file']].map(([label,value,note,ico])=>`<div class="stat"><div class="stat-label">${label}${icon(ico)}</div><div class="stat-value">${value}</div><div class="stat-foot">${note}</div></div>`).join('')}</div>
 ${next?`<section class="card next-step"><div><span class="eyebrow">NEXT STEP</span><h2>${next[0]}</h2><p>${next[1]}</p></div>${btn(next[2],next[3],true,'','arrow')}</section>`:''}
 <div class="overview-grid"><div><section class="card"><div class="card-head"><div><h2>${active.length?'Active work':'Job queue'}</h2><p>${active.length?'Jobs continue when this browser closes.':'No jobs are queued or running.'}</p></div>${quiet('go-jobs','All jobs')}</div>${active.slice(0,3).map(j=>`<div class="overview-job"><div><strong>${e(runBy(j.payload.run_id)?.label||j.kind.replaceAll('_',' '))}</strong><span class="secondary-line">${e(j.payload.action||j.kind)}</span></div>${statusBadge(j.status)}${btn('job-log','View log',false,j.id)}</div>`).join('')}</section>
 ${attention.length?`<section class="card attention-card"><div class="card-head"><div><h2>Needs attention</h2><p>Saved runs with failed or interrupted work.</p></div>${badge(attention.length+' runs')}</div>${attention.map(j=>`<div class="overview-attention"><strong>${e(j.guidance?.title||'Job needs attention')}</strong><p>${e(runBy(j.payload.run_id)?.label||j.kind.replaceAll('_',' '))}</p><div class="button-row">${runBy(j.payload.run_id)?btn('run-detail','Review run',false,j.payload.run_id):''}${quiet('job-log','View log',j.id)}</div></div>`).join('')}</section>`:''}</div>
 <section class="card overview-pipelines"><div class="card-head"><h2>Pipeline setup</h2>${quiet('go-install','Manage')}</div><div class="card-body">${pipelineRows()}<p class="settings-note">Runner setup is separate from each run’s tool and database checks.</p></div></section></div>
 <section class="card recent-runs"><div class="card-head"><div><h2>Recent runs</h2><p>Open a run to review settings, checks, activity and results.</p></div>${quiet('go-runs','All runs')}</div>${runTable(recent,false,false,'recent')}</section>`;
}
function samples(){
 const q=val('registered-sample-search').toLowerCase(),rows=S.samples.filter(s=>`${s.id} ${s.path}`.toLowerCase().includes(q));
 return heading('SAMPLES','Your input folders.','Register folders once, then use them in independent run drafts.',btn('import','Import table',false,'','file')+btn('new-sample','Add sample',true,'','plus'))+
 `<section class="card registered-samples"><div class="card-head"><div><h2>Registered samples</h2><p>${S.samples.length} folder${S.samples.length===1?'':'s'} · Original files stay in their current location.</p></div><input class="search" id="registered-sample-search" aria-label="Search registered samples" placeholder="Find a sample or folder…" value="${e(val('registered-sample-search'))}"></div>${rows.length?`<div class="table-wrap"><table><thead><tr><th>Sample</th><th>Read mode</th><th>Input folder</th><th>Actions</th></tr></thead><tbody>${rows.map(s=>`<tr data-sample-row="${e(s.id)}"><td><strong>${e(s.id)}</strong><span class="secondary-line">${S.runs.filter(r=>r.sample.id===s.id&&!r.archived).length} current runs</span></td><td>${e(readMode(s.mode))}</td><td class="path-cell">${e(s.path)}</td><td><div class="table-actions">${btn('sample-draft','Create draft',false,s.id,'plus')}${quiet('sample-runs','View runs',s.id)}${btn('remove-sample','Remove sample',false,s.id,'trash')}</div></td></tr>`).join('')}</tbody></table></div>`:empty(S.samples.length?'No matching samples.':'Start with a sample folder.',S.samples.length?'Try a different sample ID or folder name.':'Use Add sample or import a CSV/TSV table.',S.samples.length?'':btn('new-sample','Add sample',true))}<div class="section-note">Removing a sample also archives its inactive runs. Input files and reports are kept; active jobs stay visible in Runs.</div></section>`;
}
function runsPage(){
 const archived=U.runList==='archived',q=val('run-search').toLowerCase(),tool=val('run-filter-tool','all'),status=val('run-filter-status','all'),sample=U.runSampleFilter;
 const rows=S.runs.filter(r=>!!r.archived===archived&&`${r.sample.id} ${r.label} ${r.id}`.toLowerCase().includes(q)&&(tool==='all'||r.tool===tool)&&(status==='all'||r.status===status)&&(!sample||r.sample.id===sample));
 const hidden=U.selected.filter(id=>!rows.some(r=>r.id===id)).length;
 return heading('RUNS','Drafts, activity and history.','Each run keeps its own configuration, input references and outputs.',btn('new-run','Create run drafts',true,'','plus'))+
 `<section class="card saved-runs"><div class="run-list-toolbar"><div class="tabs"><button type="button" class="tab ${!archived?'active':''}" data-action="run-list" data-id="current">Current runs <span>${visibleRuns().length}</span></button><button type="button" class="tab ${archived?'active':''}" data-action="run-list" data-id="archived">Archived <span>${S.runs.filter(r=>r.archived).length}</span></button></div>${btn('clear-run-filters','Reset filters')}</div><div class="run-filters"><div class="field"><label for="run-search">Find a run</label><input class="search" id="run-search" aria-label="Search runs" placeholder="Sample, name or run ID…" value="${e(val('run-search'))}"></div>${select('run-filter-tool','Pipeline',[['all','All pipelines'],...['hybas','basis'].map(t=>[t,S.pipelines[t].name])],'all')}${select('run-filter-status','Status',[['all','All statuses'],['draft','Draft'],['checked','Validated'],['queued','Queued'],['running','Running'],['failed','Failed'],['interrupted','Interrupted'],['cancelled','Cancelled'],['completed','Completed']],'all')}</div>
 ${sample?`<div class="filter-context">Runs for <strong>${e(sample)}</strong>${quiet('clear-sample-filter','Show all samples')}</div>`:''}
 ${!archived&&U.selected.length?`<div class="selection-bar"><strong>${U.selected.length} selected${hidden?' · '+hidden+' outside this filter':''}</strong><div class="button-row">${btn('compare-selected','Compare settings')}${btn('batch-dry-run','Validate run')}${btn('batch-prepare','Install environments')}${btn('batch-run','Run / resume',true)}${btn('archive-selected','Archive',false,'','trash')}${btn('clear-selection','Clear')}</div></div>`:''}${runTable(rows,!archived,archived)}<div class="section-note">${rows.length} shown. ${archived?'Restore a run to use it again. Saved configurations and outputs are retained.':'Archive hides inactive runs from this list. It keeps their files, settings and reports.'}</div></section>`;
}
function comparisons(){
 const tab=U.comparisonTab||'review',rows=visibleRuns().filter(r=>U.selected.includes(r.id));
 let body;
 if(tab==='create')body=notice('Independent workflows','Variant drafts inherit the full parent configuration and original inputs. Each executes independently; post-AWARE checkpoint reuse and polishing-only execution are not implemented.',true)+`<section class="card"><div class="card-head"><div><h2>Choose a parent run</h2><p>Create alternate settings without changing the parent.</p></div></div>${runTable(visibleRuns().filter(r=>r.tool==='hybas'),false,false,'parent')}</section>`;
 else body=`<details class="card comparison-picker" ${!rows.length?'open':''}><summary>Choose runs · ${rows.length} selected</summary>${runTable(visibleRuns(),true,false,'comparison')}<div class="padded">${btn('clear-selection','Clear selection')}</div></details><div class="compare-grid">${rows.map(r=>`<section class="card run-compare">${badge(S.pipelines[r.tool].name,'blue')}<h3>${e(r.label)}</h3>${statusBadge(r.status)}<dl class="detail-list"><dt>Sample</dt><dd>${e(r.sample.id)}</dd><dt>Parent</dt><dd>${e(r.parent||'Original draft')}</dd><dt>Kraken mode</dt><dd>${e(krakenLabel(r))}</dd><dt>Polishing</dt><dd>${e(r.strategy)}</dd><dt>Resources</dt><dd>${r.cores} cores · ${r.memory_mb} MB</dd><dt>Source</dt><dd>${e(r.commit?.slice(0,12))}</dd></dl>${btn('run-detail','Open run',false,r.id)}${quiet('view-results','View results',r.id)}</section>`).join('')}</div>${rows.length?notice('Compare native outputs','This view compares saved settings and provenance. Open the actual reports to evaluate results.'):empty('Choose runs to compare.','Select the saved runs above, or select them in Runs and choose Compare settings.')}`;
 return heading('COMPARISONS','Review settings. Explore alternatives.','Compare saved runs or create independent variant drafts.')+`<div class="tabs comparison-tabs">${[['review','Compare saved runs'],['create','Create variants']].map(([id,label])=>`<button type="button" class="tab ${tab===id?'active':''}" data-action="comparison-tab" data-id="${id}" aria-pressed="${tab===id}">${label}</button>`).join('')}</div>`+body;
}
function runPage(){
 const r=runBy(U.runId),d=U.detail?.id===U.runId?U.detail:null;
 if(!r)return heading('RUNS','Run not found.','This run is not registered in the current workspace.',btn('go-runs','Back to runs',true));
 return `<div class="run-page-back">${quiet('go-runs','Back to runs')}</div>`+heading('SAVED RUN',e(r.label),e(r.sample.id)+' · '+e(S.pipelines[r.tool].name)+' · '+e(r.id),r.run_type==='followup'?'':btn('use-run-databases','Use saved databases',false,r.id,'resources',r.busy||r.archived)+btn('edit-run-settings','Edit settings',false,r.id,'config',r.busy||r.archived||!d))+
 `<section class="card run-workspace" aria-label="Saved run workspace">${d?renderSavedRun(r,d):U.error?empty('Run details could not be loaded.','Review the message above and try again.',btn('run-detail','Retry',true,r.id)):'<div class="padded" role="status">Loading saved run settings…</div>'}</section>`;
}
function runActivity(r){
 const jobs=S.jobs.filter(j=>j.payload.run_id===r.id);
 return `<div class="settings-intro"><div><h3>Run activity</h3><p>Validation, environment preparation and execution jobs for this saved run.</p></div></div>`+(jobs.length?`<div class="run-activity">${jobs.map(j=>`<article class="run-job"><div><strong>${e(({'dry-run':'Validate run',prepare:'Install environments',run:'Run / resume','check-environments':'Check environments'}[j.payload.action])||j.kind)}</strong><span class="secondary-line">${e(j.created)} · ${e(j.id)}</span>${j.guidance?`<p class="job-guidance">${e(j.guidance.title)}<br>${e(j.guidance.text)}</p>`:''}</div>${statusBadge(j.status)}<div class="button-row">${btn('job-log','View log',false,j.id)}${usageButton(j)}${['queued','running'].includes(j.status)?btn('cancel-job','Stop',false,j.id,'stop'):''}</div>${jobTelemetry(j)}</article>`).join('')}</div>`:empty('No jobs for this run yet.','Open Overview to validate its configuration and check its environments.'));
}
function runResults(r){
 const rows=S.reports.filter(x=>x.run===r.id);
 return `<div class="settings-intro"><div><h3>Run results</h3><p>Native HTML reports discovered for this run.</p></div></div>`+(rows.length?`<div class="run-reports">${rows.map(x=>`<article><div><strong>${e(x.name)}</strong><p class="path-cell">${e(x.path)}</p></div>${btn('open-report','Open report',true,x.id,'external')}</article>`).join('')}</div>`:empty('No reports found for this run.','Reports appear when the workflow writes them. Check Activity for progress or errors.'))+`<div class="button-row">${btn('open-methods','Methods & downloads',false,r.id,'download')}${quiet('go-reports','All reports')}</div>`;
}

function appUpdateNotice(){const u=S.app_update;if(!u)return '';
if(u.active_job)return notice('Updating Bactologix',u.automatic_restart?'Follow installation in Jobs & progress. After verification, the app will restart and this tab will refresh automatically.':'Follow installation in Jobs & progress. This session needs one manual restart after installation.');
if(u.restart_required&&u.automatic_restart)return notice('Restarting Bactologix','The update is verified. Keep this tab open; it will refresh when the new application is ready.');
if(u.restart_required)return notice('Restart Bactologix',`Installed package: v${e(u.installed_version)}. In the Bactologix terminal press Ctrl+C, then rerun your launch command with the same port. Refresh this browser after restart.`,true);
return '';}
const setupSteps=[['capacity','PC capacity'],['pipeline','Pipeline'],['environments','Environments'],['databases','Databases'],['review','Review setup']];
function install(){const tab=U.setupTab||'guide';return heading('INSTALL & UPDATE','Set up once. Run with confidence.','Follow the setup guide or open the individual tools below.')+`<div class="tabs setup-tabs" aria-label="Setup sections">${[['guide','Guided setup'],['software','Pipelines & environments'],['hardware','PC capacity']].map(([id,label])=>`<button type="button" class="tab ${tab===id?'active':''}" data-action="setup-tab" data-id="${id}" aria-pressed="${tab===id}">${e(label)}</button>`).join('')}</div>`+(tab==='guide'?setupGuide():tab==='hardware'?hardwarePanel():installSoftware());}
function setupGuide(){
 const tool=U.setupTool||'hybas',step=U.setupStep||'capacity',index=setupSteps.findIndex(x=>x[0]===step),item=activeInstall(tool),env=item?S.environments[item.id]:null;
 const verified=env?.rows.filter(r=>r.status==='Verified').length||0,configured=S.databases[tool].rows.filter(r=>!r.managed&&r.configured).length;
 let body='';
 if(step==='capacity')body=hardwarePanel();
 else if(step==='pipeline')body=installSoftware(tool);
 else if(step==='environments')body=`<section class="card guide-environments"><div class="card-head"><div><h2>Prepare your tools</h2><p>${e(S.pipelines[tool].name)} · ${e(item?.version.ref||'No active installation')}</p></div>${badge(verified+' verified / '+(env?.rows.length||0)+' listed')}</div><div class="card-body"><p>Matching environments are reused automatically when you update a pipeline. Check what is installed, install missing tools, or repair a supported dependency problem.</p>${!item?.ready?notice('Set up the runner first','Return to Pipeline and finish setup before preparing tool environments.',true):''}${env?.error?notice('Inventory needs attention',e(env.error),true):''}<div class="button-row">${btn('setup-check-environments','Check environments',true,item?.id||'','check',!item?.ready||item?.setup_busy||env?.busy)}${btn('setup-install-environments','Install environments',false,item?.id||'','download',!item?.ready||item?.setup_busy||env?.busy)}${btn('setup-repair-environments','Repair environments',false,item?.id||'','gear',!item?.ready||item?.setup_busy||env?.busy)}</div>${env?.cache?`<p class="path-cell">Environment folder: ${e(env.cache)}</p>`:''}<div class="guide-environment-list">${(env?.rows||[]).map(r=>`<div><strong>${e(r.name)}</strong>${badge(r.status,r.status==='Verified'?'green':'')}${r.message?`<p>${e(r.message)}</p>`:''}</div>`).join('')}</div><p class="settings-note">These are installation checks. A saved run still needs validation and its own environment inventory.</p></div></section>`;
 else if(step==='databases'){U.resourceTool=tool;body=resources(true);}
 else body=`<section class="card guide-review"><div class="card-head"><div><h2>Review before your first draft</h2><p>Recorded setup status for ${e(S.pipelines[tool].name)}. This does not certify a run as ready.</p></div>${btn('refresh','Refresh status',false,'','clock')}</div><div class="card-body"><div class="guide-review-row"><div><strong>Resource defaults</strong><p>${S.resource_defaults[tool].cores} CPU threads · ${S.resource_defaults[tool].memory_mb.toLocaleString()} MB · ${e(S.resource_defaults[tool].preset)}</p></div>${quiet('setup-step','Review capacity','capacity')}</div><div class="guide-review-row"><div><strong>Pipeline runner</strong><p>${e(item?.setup_state||'Not set up')} · ${e(item?.version.ref||'Choose a supported version')}</p></div>${quiet('setup-step','Review pipeline','pipeline')}</div><div class="guide-review-row"><div><strong>Tool environments</strong><p>${verified} verified / ${env?.rows.length||0} listed. Unchecked tools may need installation.</p></div>${quiet('setup-step','Review environments','environments')}</div><div class="guide-review-row"><div><strong>Saved database locations</strong><p>${configured} configured paths. Required resources depend on the run settings; native validation checks them.</p></div>${quiet('setup-step','Review databases','databases')}</div><div class="guide-review-row"><div><strong>Samples</strong><p>${S.samples.length} registered input folders.</p></div>${btn('new-sample','Add sample',false,'','plus')}</div><p class="settings-note">A draft freezes its input references, pipeline version and configuration. Validate it before running. Nothing launches from this review.</p><div class="button-row">${btn('new-run','Create run drafts',true,'','plus',!item?.ready||!S.samples.length)}${btn('go-samples','Manage samples')}</div></div></section>`;
 return `<section class="setup-guide" aria-label="Guided setup"><div class="guide-heading"><div><span class="eyebrow">YOUR SETUP PATH</span><h2>From this computer to your first run.</h2><p>Move between steps freely. Existing installations and databases can be connected here.</p></div>${select('setup-pipeline','Pipeline to set up',['hybas','basis'].map(t=>[t,S.pipelines[t].name]),tool)}</div><nav class="setup-steps" aria-label="Setup steps">${setupSteps.map(([id,label],n)=>`<button type="button" class="setup-step ${step===id?'active':''}" data-action="setup-step" data-id="${id}" ${step===id?'aria-current="step"':''}><span>${n+1}</span>${label}</button>`).join('')}</nav><div class="guide-content">${body}</div><div class="guide-footer"><span>Step ${index+1} of ${setupSteps.length} · ${e(setupSteps[index][1])}</span><div class="button-row">${index?btn('setup-step','Back',false,setupSteps[index-1][0]):''}${index<setupSteps.length-1?btn('setup-step','Continue to '+setupSteps[index+1][1],true,setupSteps[index+1][0],'arrow'):''}</div></div></section>`;
}
function hardwarePanel(){
 const h=S.hardware;if(!h)return empty('Hardware information unavailable','Refresh the inventory to detect this computer.',btn('detect-hardware','Detect hardware',true));
 const gb=n=>n==null?'Unknown':(n/1024).toLocaleString(undefined,{maximumFractionDigits:1})+' GB',g=h.gpu;
 const descriptions={high:'All available CPU threads, within the app limit. Maximum usable RAM with a system reserve.',medium:'Half the CPU threads and 60% of the usable RAM budget.',low:'A quarter of the CPU threads and 30% of the usable RAM budget.'};
 return `<section class="card hardware-overview"><div class="card-head"><div><h2>This computer</h2><p>${e(h.system)} · ${e(h.architecture)}${h.wsl?' · WSL':''} · Detected ${e(new Date(h.checked_at).toLocaleString())}</p></div>${btn('detect-hardware','Detect again',false,'','cpu')}</div><div class="hardware-stats"><article><span>CPU available</span><strong>${h.cpu.available} threads</strong><small>${e(h.cpu.model)} · ${h.cpu.logical} logical${h.cpu.physical?' / '+h.cpu.physical+' physical':''} reported</small></article><article><span>RAM capacity</span><strong>${gb(h.memory.total_mb)}</strong><small>${gb(h.memory.available_mb)} available now · ${gb(h.memory.swap_mb)} swap</small></article><article><span>Workspace storage</span><strong>${gb(h.disk.free_mb)} free</strong><small>${gb(h.disk.total_mb)} total · ${e(S.workspace)}</small></article></div><p class="inline-info">${h.wsl?'These are resources available inside WSL, which may be lower than the Windows PC total. ':'CPU affinity and container limits are included when reported by Linux. '}Detection is read-only. Swap is excluded from RAM budgets.</p></section>
 <section class="card hardware-gpu"><div class="card-head"><div><h2>GPU & CUDA</h2><p>${e(g.status)}</p></div>${badge(g.cards.length?g.cards.length+' NVIDIA GPU'+(g.cards.length===1?'':'s'):'GPU availability unknown',g.cards.length?'green':'')}</div>${g.cards.map(card=>`<div class="hardware-device"><strong>${e(card.name)}</strong><span>${gb(card.memory_mb)} VRAM · ${gb(card.free_mb)} free</span></div>`).join('')}<dl class="detail-list"><dt>NVIDIA driver</dt><dd>${e(g.driver||'Not detected')}</dd><dt>Driver CUDA support</dt><dd>${e(g.cuda_driver_max||'Not reported')}</dd><dt>CUDA toolkit (nvcc)</dt><dd>${e(g.cuda_toolkit||'Not found in the app environment')}</dd></dl>${g.cuda_visible_devices!==null?`<p class="inline-info">CUDA_VISIBLE_DEVICES: ${e(g.cuda_visible_devices||'(empty — CUDA devices hidden)')}</p>`:''}${g.other_devices.length?`<details class="guided-details"><summary>Graphics devices reported by PCI</summary><pre class="code-block">${e(g.other_devices.join('\n'))}</pre></details>`:''}<p class="inline-info">A GPU not detected here may still exist on the host, especially under WSL. Driver CUDA support and the installed toolkit are separate. GPU availability is informational: these presets set CPU and RAM budgets. GPU use depends on each tool and its environment.</p></section>
 <section class="card hardware-presets"><div class="card-head"><div><h2>Choose your power level</h2><p>Save the YAML resource settings and CPU/RAM defaults used by new run drafts.</p></div>${select('hardware-target','Apply defaults to',[['both','Both pipelines'],...['hybas','basis'].map(t=>[t,S.pipelines[t].name])],U.view==='install'&&(U.setupTab||'guide')==='guide'?(U.setupTool||'hybas'):'both')}</div><div class="power-grid">${h.presets.map(p=>`<article class="power-card ${p.id}"><span class="eyebrow">${p.id==='high'?'MAXIMUM THROUGHPUT':p.id==='medium'?'BALANCED':'LIGHTER LOAD'}</span><h3>${e(p.label)}</h3><div class="power-budget"><strong>${p.cores}</strong> CPU threads<br><strong>${gb(p.memory_mb)}</strong> RAM budget</div><p>${e(descriptions[p.id])}</p>${btn('apply-power-preset','Use '+p.label,p.id==='medium',p.id,'check',!p.available)}</article>`).join('')}</div><p class="inline-info">RAM reserves at least 1 GB or 5% for the system, capped by currently available memory. App limits: 512 CPU threads and 2,000,000 MB RAM. These are execution budgets; individual tools may use less. Database and tool minimum requirements still apply.</p><div class="hardware-saved">${['hybas','basis'].map(t=>{const p=h.defaults[t];return `<div><strong>${e(S.pipelines[t].name)}</strong><span>${p.cores} threads · ${gb(p.memory_mb)} · ${e(p.preset)}</span></div>`;}).join('')}</div><p class="inline-info">Existing saved runs retain their settings. Edit a run to create a revised draft when you want a different budget.</p></section>`;
}
function installSoftware(onlyTool=null){const d=S.doctor;return `<section class="card"><div class="card-head"><div><h2>Bactologix application</h2><p>Running v${e(S.version)} · Choose which app release to install.</p></div>${btn('app-updates','Update Bactologix',true,'','download')}</div></section>
<div class="stats-grid"><div class="stat"><div class="stat-label">Platform</div><div class="stat-value">${e(d.system)}</div></div><div class="stat"><div class="stat-label">Free disk</div><div class="stat-value">${d.free_gb} GB</div></div><div class="stat"><div class="stat-label">CPU threads</div><div class="stat-value">${d.cpus}</div></div><div class="stat"><div class="stat-label">App Python</div><div class="stat-value">${e(d.python)}</div></div></div>
<section class="card install-card"><div class="card-head"><div><h2>Local environment manager</h2><p class="path-cell">${e(d.conda||'Choose an existing Conda installation or install local Conda.')}</p></div><div class="button-row">${btn('conda','Choose Conda')}${!d.conda?btn('bootstrap','Install local Conda',true):''}${btn('smoke','Run software check',false,'','check')}</div></div><p class="inline-info">Each release gets the Python and Snakemake versions it requires. Setup installs a separate runner automatically, including local Conda when needed.</p></section>
<div class="two-col">${(onlyTool?[onlyTool]:['hybas','basis']).map(t=>{const i=activeInstall(t),p=S.pipelines[t],items=S.installs.filter(x=>x.tool===t),display=i||items[0];return `<section class="card install-card"><div class="pipeline-row"><div class="pipeline-logo ${t}">${t==='hybas'?'H':'B'}</div><div class="pipeline-info"><strong>${p.name}</strong><small>${e(p.preferred)}</small></div>${badge(display?.setup_state||'Not installed',display?.ready?'green':'amber')}</div><p>${t==='hybas'?'Assembly, AWARE and polishing workflows.':'Standalone Snakemake analysis and Safety reporting.'}</p><dl class="detail-list"><dt>Active source</dt><dd>${e(i?.version.ref||'No active installation')}</dd><dt>Commit</dt><dd>${e(i?.version.commit?.slice(0,12)||'—')}</dd></dl><div class="button-row">${btn('versions',i?'Update / choose version':'Choose a version',true,t,'download')}${btn('existing-install','Connect existing',false,t,'folder')}${i?btn('check-install','Check setup',false,i.id,'',i.setup_busy):''}</div>
${items.map(x=>`<div class="installation-entry" data-installation="${e(x.id)}"><div class="card-head"><strong>${e(x.version.ref)} · ${e(x.version.commit.slice(0,8))}</strong>${badge(x.setup_state,x.ready?'green':'amber')}</div><p class="inline-info">${e(x.setup_message)}</p><div class="installation-cache"><span>Automatic environment reuse</span><code>${e(S.environments[x.id]?.cache||x.rule_cache||S.workspace+'/envs/'+t)}</code><small>Updates keep this folder. Matching environments are reused; missing or changed requirements need installation.</small></div><div class="button-row">${!x.ready&&x.supported?btn('setup-install',x.setup_state==='Setup incomplete'?'Retry setup':'Finish setup',true,x.id,'',x.setup_busy):''}${!x.supported?btn('app-updates','Update Bactologix'):''}${x.ready?btn('use-install',S.active[t]===x.id?'Active version':'Use this version',false,x.id,'',S.active[t]===x.id||x.setup_busy):''}${x.supported?btn('setup-install-environments','Install environments',true,x.id,'download',!x.ready||x.setup_busy||S.environments[x.id]?.busy):''}${x.supported?btn('setup-check-environments','Check environments',false,x.id,'check',!x.ready||x.setup_busy||S.environments[x.id]?.busy):''}${x.supported?btn('setup-repair-environments','Repair environments',false,x.id,'gear',!x.ready||x.setup_busy||S.environments[x.id]?.busy):''}${btn('preview-uninstall','Uninstall version',false,x.id,'trash',x.setup_busy)}${x.setup_job?btn('job-log','Setup log',false,x.setup_job,'file'):''}</div><details class="config-section"><summary>Installation details</summary><div class="padded"><p class="path-cell">${x.origin==='local'?'Connected from: '+e(x.original_source):e(x.source)}</p><p class="path-cell">${e(x.controller||'Runner has not been created')}</p><div class="button-row">${btn('check-install','Check',false,x.id,'',!x.controller||!x.supported||x.setup_busy)}${btn('repair-install','Repair runner',false,x.id,'',!x.supported||x.setup_busy)}</div></div></details></div>`).join('')}</section>`;}).join('')}</div>${notice('Updates reuse compatible environments','Runner environments are identified by pipeline, environment definition and platform, then checked before reuse. A pipeline update may need a compatible runner. Normal runs reuse matching tool environments and stop if any are missing; use Install environments explicitly. Prior runs retain their original version.')}`;}
function usageNumber(value,digits=1){return Number.isFinite(value)?value.toLocaleString(undefined,{maximumFractionDigits:digits}):'Unavailable';}
function usageMeasure(value,unit){return Number.isFinite(value)?usageNumber(value)+unit:'Unavailable';}
function usageDuration(value){if(!Number.isFinite(value))return 'Unavailable';const s=Math.floor(Math.max(0,value));return s>=3600?Math.floor(s/3600)+'h '+Math.floor(s%3600/60)+'m':s>=60?Math.floor(s/60)+'m '+s%60+'s':s+'s';}
function jobTelemetry(j){
 const t=j.telemetry;if(!t)return '';const p=t.progress||{},running=j.status==='running',live=t.live;
 const counts=Number.isInteger(p.completed)&&Number.isInteger(p.total)&&p.total>0;
 return `<div class="job-progress" data-job-progress="${e(j.id)}"><div class="usage-inline"><span>Elapsed <strong>${usageDuration(t.elapsed_s)}</strong></span><span>${live?'CPU now':'Observed CPU'} <strong>${live?usageMeasure(t.latest?.cpu_percent,'%'):usageMeasure(t.samples?t.observed_cpu_s:null,' s')}</strong></span><span>Peak sampled RAM <strong>${usageMeasure(t.peak_rss_mb,' MiB')}</strong></span>${running?badge(live?'Receiving updates':'Updates paused',live?'green':'amber'):''}</div>${counts?`<div class="task-progress"><label>${p.completed} / ${p.total} tasks in current plan <span>${usageNumber(p.percent)}%</span></label><progress aria-label="Tasks in current plan" value="${p.completed}" max="${p.total}"></progress></div>`:p.source==='workflow log'?'<small>Task count not reported yet.</small>':''}${p.active_rules?.length?`<p class="usage-step">${live?'Active steps':'Last reported steps'}: <strong>${p.active_rules.map(e).join(', ')}</strong>${p.active_count>p.active_rules.length?' · '+p.active_count+' active tasks at last update':''}</p>`:live&&t.command_name?`<p class="usage-step">Command: <strong>${e(t.command_name)}</strong></p>`:''}${p.dynamic_plan?'<small>Checkpoints can change the task total.</small>':''}${t.incomplete||t.observer_errors?'<small class="usage-warning">Partial record — observation ended unexpectedly or some updates failed.</small>':''}${t.stale?'<small class="usage-warning">Showing the last saved measurements. Open the log to check the job.</small>':''}</div>`;
}
function usageButton(j){return j.telemetry?btn('job-usage','Resource use',false,j.id,'cpu'):'';}
function usageChart(points,metric,title,unit){
 // Draw interval bars, not interpolated lines. Compaction retains CPU integrals and RSS peaks.
 const stride=Math.max(1,Math.ceil(points.length/100)),buckets=[];
 for(let i=0;i<points.length;i+=stride){const group=points.slice(i,i+stride),a=group[0],b=group.at(-1),duration=b.end_s-a.start_s,observed=group.reduce((s,p)=>s+p.observed,0);const rss=group.map(p=>p.rss_peak_mb).filter(Number.isFinite);buckets.push({start:a.start_s,end:b.end_s,value:metric==='cpu'?(duration>=.1&&observed?100*group.reduce((s,p)=>s+p.cpu_s,0)/duration:null):(rss.length?Math.max(...rss):null),missing:group.some(p=>p.unavailable>0)});}
 const values=buckets.map(p=>p.value).filter(Number.isFinite);if(!values.length)return `<section class="usage-chart"><h3>${e(title)}</h3><p>No usable measurements recorded.</p></section>`;
 const peak=Math.max(1,...values),end=Math.max(1,...buckets.map(p=>p.end));
 const bars=buckets.filter(p=>Number.isFinite(p.value)).map(p=>{const x=48+408*p.start/end,w=Math.max(1,408*(p.end-p.start)/end),height=114*p.value/peak;return `<rect x="${x.toFixed(2)}" y="${(140-height).toFixed(2)}" width="${w.toFixed(2)}" height="${Math.max(1,height).toFixed(2)}" opacity="${p.missing?'.45':'.85'}"><title>${e(usageDuration(p.start)+'–'+usageDuration(p.end)+': '+usageNumber(p.value)+' '+unit+(p.missing?' (partial readings)':''))}</title></rect>`;}).join('');
 return `<section class="usage-chart"><h3>${e(title)}</h3><svg viewBox="0 0 470 175" role="img" aria-label="${e(title+' over elapsed job time; peak '+usageNumber(Math.max(...values))+' '+unit)}"><text x="4" y="20">${e(usageNumber(peak))}</text><text x="4" y="140">0</text><path d="M48 24V140H456"/><g>${bars}</g><text x="48" y="161">0s</text><text x="456" y="161" text-anchor="end">${e(usageDuration(end))}</text></svg><small>${metric==='cpu'?'Observed CPU per interval; 100% = one logical CPU.':'Peak process RSS sum per interval, in MiB.'} Faded bars contain missing readings.</small></section>`;
}
function usageDetail(data){
 const t=data.summary,j={id:data.id,status:t.job_status,telemetry:t};
 return `<div class="usage-detail">${statusBadge(t.job_status)}${jobTelemetry(j)}<p class="inline-info">Recorded for this job attempt only. A resumed run gets its own record.</p><div class="usage-charts">${usageChart(data.points,'cpu','CPU use (%)','%')}${usageChart(data.points,'ram','Sampled RAM (MiB)','MiB')}</div><div class="usage-facts"><div><span>Observed CPU time</span><strong>${usageMeasure(t.samples?t.observed_cpu_s:null,' s')}</strong></div><div><span>Usable / unavailable samples</span><strong>${usageNumber(t.samples,0)} / ${usageNumber(t.unavailable_samples,0)}</strong></div><div><span>Configured run budget</span><strong>${Number.isFinite(t.budget?.cores)?usageNumber(t.budget.cores,0)+' cores · '+usageNumber(t.budget.memory_mb,0)+' MB':'Not a saved run'}</strong></div><div><span>Recorded platform</span><strong>${e(t.system)} · ${e(t.architecture)}</strong></div></div><p class="secondary-line">Summary updated ${e(t.updated_at)}<br>Charts saved ${e(data.history_updated_at||'not yet')} · ${t.history_compacted?'History combined into intervals':'Charts saved at most every 30 seconds and at command completion'}</p>${t.partial_process_tree?notice('Some processes could not be read','CPU and RAM values describe only the observed processes.',true):''}${t.progress?.log_gap?notice('Some log output was skipped','The displayed task counts and steps may be incomplete.',true):''}<details class="config-section"><summary>Measurement scope & provenance</summary><div class="padded"><ul>${(t.notes||[]).map(n=>`<li>${e(n)}</li>`).join('')}</ul><p>Bactologix ${e(t.app_version)} · Python ${e(t.python)}</p>${t.pipeline?`<p>Pipeline: ${e(S.pipelines[t.pipeline]?.name||t.pipeline)}</p><p class="path-cell">Source commit: ${e(t.commit||'Unavailable')}<br>Configuration SHA-256: ${e(t.config_sha256||'Unavailable')}<br>Workflow SHA-256: ${e(t.workflow_sha256||'Unavailable')}</p>`:''}</div></details></div>`;
}

function jobs(){return heading('JOBS & PROGRESS','See what’s happening.','Real worker status and process output. Jobs continue when the browser closes.',btn('refresh','Refresh',false,'','clock')+btn('recover-jobs','Start / recover queue'))+
`<section class="card">${S.jobs.length?S.jobs.map(j=>`<div class="job-card"><div><div class="job-title">${e(j.kind==='environments'?'Environments · '+j.payload.tool+' · '+j.payload.action:j.kind==='database_download'?'Database · '+j.payload.tool+' · '+j.payload.resource:j.kind==='workflow'?({'dry-run':'Validate run',prepare:'Install environments',run:'Run / resume','check-environments':'Check run environments'}[j.payload.action]||j.payload.action)+' · '+(runBy(j.payload.run_id)?.label||j.payload.run_id):j.kind==='install'?S.pipelines[j.payload.tool]?.name+' · '+j.payload.version.ref:j.kind==='app_update'?'Bactologix → v'+j.payload.release.version:j.kind)}</div><div class="job-sub">${e(j.id)} · ${e(j.created)}</div>${j.guidance?`<div class="job-guidance"><strong>${e(j.guidance.title)}</strong><p>${e(j.guidance.text)}</p></div>`:j.error?`<p class="error">${e(j.error)}</p>`:''}</div><div>${statusBadge(j.status)}${j.cancel?'<small class="secondary-line">Stop requested</small>':''}</div><div class="button-row">${j.guidance?.run_id&&runBy(j.guidance.run_id)?btn('run-detail','Review run',false,j.guidance.run_id):''}${btn('job-log','View log',false,j.id,'file')}${usageButton(j)}${['queued','running'].includes(j.status)&&!(j.kind==='app_update'&&j.status==='running')?btn('cancel-job','Stop',false,j.id,'stop'):''}</div>${jobTelemetry(j)}</div>`).join(''):empty('No jobs yet.','Set up a pipeline or run the live software test to begin.',btn('go-install','Install & update',true))}</section>`;}
function reportsPage(){const rows=S.reports.filter(r=>!U.reportRun||r.run===U.reportRun);return heading('RESULTS & REPORTS','From a run to its results.','Open native MultiQC, Safety and other HTML reports directly in your workspace.',btn('register-report','Add existing report',true,'','plus'))+
`${U.reportRun?`<div class="selection-bar">Reports for ${e(runBy(U.reportRun)?.label||U.reportRun)} ${btn('all-reports','Show all reports')}</div>`:''}<section class="card">${rows.length?`<div class="table-wrap"><table><thead><tr><th>Report</th><th>Sample</th><th>Run</th><th>Run status</th><th>Open</th></tr></thead><tbody>${rows.map(r=>`<tr><td><strong>${e(r.name)}</strong><span class="secondary-line path-cell">${e(r.path)}</span></td><td>${e(r.sample)}</td><td>${e(r.run)}</td><td>${statusBadge(r.run_status||'User-selected')}</td><td>${btn('open-report','Open report',true,r.id,'external')}</td></tr>`).join('')}</tbody></table></div>`:empty('No reports found here yet.','Reports appear when the native workflow writes them. You can also register an existing local HTML report.',btn('register-report','Add report'))}</section>`;}
function defaultEditor(tool=U.tool){return U.defaultEditors?.[tool];}
function defaultsDirty(c){return !!c&&(Object.keys(c.changes).length>0||c.text!==c.savedText);}
function editorUI(c=runEditor()){
 if(c?.scope!=='defaults')return U;
 U.defaultUI ||= {};return U.defaultUI[c.tool] ||= {runConfigTab:'settings',runSettingTab:{},runConfigGroup:{}};
}
function editorSearchId(c){return c.scope==='defaults'?'default-setting-search-'+c.tool:'run-setting-search';}
function editorRoot(c){return c.scope==='defaults'?$('.defaults-editor'):$('.run-wizard');}
function renderEditor(){render();}
function requestEditor(view,validate=false,extra={}){
 const c=runEditor();if(!c)return;
 request(c.scope==='defaults'?'default_config_editor':'run_config_editor',{...runConfigPayload(),view,validate,...(c.scope==='defaults'?{revision:c.revision}:{}),...extra});
}
function updateDefaultStatus(){const c=defaultEditor(),status=$('#default-save-status');if(c&&status)status.textContent=defaultsDirty(c)?'Unsaved changes':'Saved defaults';}
function configuration(){
 const t=U.tool,c=defaultEditor(t),ui=c?editorUI(c):null,stale=!!c&&c.revision!==S.databases[t].revision;
 let body='';
 if(!c)body='<div class="padded"><p>Loading saved configuration…</p></div>';
 else if(ui.runConfigTab==='yaml')body=runSettingTabs(c)+`<div class="guided-panel"><div class="settings-intro"><div><h3>Full configuration</h3><p>Import or edit native YAML, including custom keys. Save defaults when you are ready.</p></div>${btn('run-config-tab','Back to controls',false,'settings')}</div>${area('config-yaml-'+t,'Full configuration',c.text,'spellcheck="false" rows="20" data-run-yaml="'+t+'"')}</div>`;
 else body=runSettingsPanel(c);
 return heading('CONFIGURATION','Your defaults, your settings.','Choose the settings future run drafts start with. Each saved run keeps its own configuration.')+
 `<div class="tabs configuration-pipelines">${['hybas','basis'].map(x=>`<button class="tab ${t===x?'active':''}" data-action="config-tool" data-id="${x}">${S.pipelines[x].name}${defaultsDirty(defaultEditor(x))?'<span class="unsaved-dot" aria-label="Unsaved changes"></span>':''}</button>`).join('')}</div><section class="card defaults-editor"><div class="defaults-header"><div><strong>${e(S.pipelines[t].name)} defaults</strong><p>Category tabs for everyday changes. Advanced contains every setting.</p></div>${badge('Future runs','green')}</div><div class="defaults-toolbar"><div class="button-row"><label class="btn upload-label">${icon('file')}Import YAML<input type="file" id="yaml-upload" accept=".yaml,.yml" class="visually-hidden" ${!c?'disabled':''}></label>${btn('download-yaml','Download YAML',false,'','download',!c)}${btn('check-run-config','Check settings',false,'','check',!c)}</div>${btn('reload-run-defaults','Reload saved defaults',false,'','',!c)}</div>${stale?notice('Saved defaults changed','Download your edits to keep a copy, then reload saved defaults before saving. New database selections or changes from another window are protected.',true):''}<div class="defaults-content">${body}</div><div class="defaults-footer"><div><strong id="default-save-status">${defaultsDirty(c)?'Unsaved changes':'Saved defaults'}</strong><p>Changes apply to future drafts. Existing runs keep their saved settings.</p></div>${btn('save-config','Save defaults',true,t,'check',!c||stale)}</div></section>`;
}
function runEditor(){return wizardKind()==='edit-run'?U.revisionConfig:wizardKind()==='new-run'?U.runConfigs?.[val('run-tool','hybas')]:U.view==='configure'?defaultEditor():U.runConfigs?.[val('run-tool','hybas')];}
function runFieldValue(c,f){return Object.hasOwn(c.changes,f.key)?c.changes[f.key]:f.kind==='yaml'?f.yaml:f.value;}
function runConfigPayload(){const c=runEditor();return c?{tool:c.tool,text:c.text,changes:c.changes}:null;}
function editRunField(c,f,value){
 c.changes[f.key]=value;
 const get=name=>c.fields.find(x=>x.name===name),put=(name,v)=>{const row=get(name);if(row)c.changes[row.key]=v;};
 if(c.tool==='hybas'&&f.name==='run_kraken'){
  if(!value)put('kraken_decision_mode','off');
  else if(runFieldValue(c,get('kraken_decision_mode'))==='off')put('kraken_decision_mode','aware');
 }
 if(c.tool==='hybas'&&f.name==='kraken_decision_mode')put('run_kraken',value!=='off');
 updateDefaultStatus();
 const counter=editorRoot(c)?.querySelector('[data-edited-count]');if(counter)counter.textContent=Object.keys(c.changes).length+' edited settings';
 for(const b of editorRoot(c)?.querySelectorAll('[data-action="reset-run-field"]')||[])b.disabled=!Object.hasOwn(c.changes,b.dataset.id);
}
function runSetting(c,f,index,guided=false,label=f.label,help=f.help){
 const value=runFieldValue(c,f),id=(c.scope==='defaults'?'default-setting-'+c.tool+'-':'run-setting-')+index;
 const attrs=`id="${id}" data-run-field="${e(f.key)}" data-run-name="${e(f.name)}" aria-label="${e(guided?label:f.label+' ('+f.name+')')}"`;
 let control='';
 if(f.managed){
  let managed='Assigned when the draft is created';
  if(['threads','execution.cores'].includes(f.name))managed='Controlled by CPU cores per run above';
  if(['memory_mb','execution.memory_mb'].includes(f.name))managed='Controlled by Memory budget above';
  if(c.scope==='defaults')managed='Assigned for each run when its draft is created';
  control=`<p class="run-managed-value">${e(managed)}</p>`;
 }else if(f.kind==='boolean')control=`<label class="run-toggle"><input type="checkbox" role="switch" ${attrs} ${value?'checked':''}><span>${value?'On':'Off'}</span></label>`;
 else if(f.kind==='select'){
  const labels={off:'Off',aware:'AWARE',auto:'Auto',manual:'Manual',none:'None',racon:'Racon',medaka:'Medaka',tripolish:'Tripolish'};
  const options=f.options.includes(value)?f.options:[value,...f.options];
  control=`<select ${attrs}>${options.map(x=>`<option value="${e(x)}" ${x===value?'selected':''}>${e(labels[x]||x)}</option>`).join('')}</select>`;
 }else if(f.kind==='yaml')control=`<textarea ${attrs} rows="${value?.includes('\n')?4:2}" spellcheck="false">${e(value)}</textarea>`;
 else control=`<input ${attrs} type="${['integer','number','optional_integer'].includes(f.kind)?'number':'text'}" ${f.kind==='number'?'step="any"':f.kind.includes('integer')?'step="1"':''} value="${e(value??'')}" ${f.kind.startsWith('optional_')?'placeholder="Automatic / not set"':''}>`;
 if(guided)return `<div class="guided-field field ${f.kind==='boolean'?'switch-card':''}"><div><label for="${id}">${e(label)}</label>${help?`<p>${e(help)}</p>`:''}</div>${control}</div>`;
 return `<div class="run-setting field" data-run-search="${e((f.name+' '+f.label+' '+f.group).toLowerCase())}" data-setting-group="${e(f.group)}"><div class="run-setting-heading"><label for="${id}">${e(f.label)}</label>${!f.managed?btn('reset-run-field','Reset edit',false,f.key,'',!Object.hasOwn(c.changes,f.key)):badge('Managed')}</div><small class="run-setting-key">${e(f.name)}</small>${control}${f.help?`<small>${e(f.help)}</small>`:''}${!f.included&&!f.managed?`<small>Available default · added to ${c.scope==='defaults'?'saved defaults':'this draft'} only when edited.</small>`:''}${f.kind==='yaml'&&!f.managed?'<small>YAML value: use null for unset values, [] for lists, or {} for mappings.</small>':''}</div>`;
}
function filterRunSettings(){
 const c=runEditor();if(!c)return;const owner=editorRoot(c);if(!owner)return;
 const q=val(editorSearchId(c),'').toLowerCase().trim(),group=editorUI(c).runConfigGroup?.[c.tool]||c.groups[0];
 let count=0;
 for(const row of owner.querySelectorAll('[data-run-search]')){row.hidden=q?!row.dataset.runSearch.includes(q):row.dataset.settingGroup!==group;if(!row.hidden)count++;}
 for(const section of owner.querySelectorAll('[data-run-section]'))section.hidden=![...section.querySelectorAll('[data-run-search]')].some(r=>!r.hidden);
 const empty=owner.querySelector('[data-settings-empty]');if(empty)empty.hidden=count>0;
 for(const b of owner.querySelectorAll('[data-action="run-setting-group"]')){b.classList.toggle('active',!q&&b.dataset.id===group);b.setAttribute('aria-pressed',String(!q&&b.dataset.id===group));}
}
function advancedRunSettings(c){
 const group=editorUI(c).runConfigGroup?.[c.tool]||c.groups[0];
 return `<div class="run-settings-toolbar"><div class="field">${field(editorSearchId(c),c.scope==='defaults'?'Search all settings':'Search run settings','','search','placeholder="Kraken, BUSCO, memory, model…"')}</div><span data-edited-count>${Object.keys(c.changes).length} edited settings</span>${c.scope==='defaults'?'':btn('reload-run-defaults',wizardKind()==='edit-run'?'Reset to saved run':'Reload saved defaults',false,'','config')}</div><div class="run-settings-layout"><nav class="run-settings-nav" aria-label="Configuration sections">${c.groups.map(g=>`<button type="button" class="${group===g?'active':''}" data-action="run-setting-group" data-id="${e(g)}">${e(g)}<span>${c.fields.filter(f=>f.group===g).length}</span></button>`).join('')}</nav><div class="run-settings-fields">${c.groups.map(g=>`<section data-run-section="${e(g)}"><h3>${e(g)}</h3><div class="run-settings-grid">${c.fields.map((f,i)=>f.group===g?runSetting(c,f,i):'').join('')}</div></section>`).join('')}<p data-settings-empty hidden>No settings match this search.</p></div></div>`;
}
const guidedTabs={hybas:[['kraken','Kraken & AWARE','branch'],['assembly','Assembly','samples'],['quality','Quality','check'],['databases','Databases','resources'],['advanced','Advanced','config']],basis:[['modules','Modules','samples'],['aware','AWARE','branch'],['deep','DeepSafe','check'],['resources','Resources','resources'],['advanced','Advanced','config']]};
function runGuideField(c,name,label,help){const i=c.fields.findIndex(f=>f.name===name);return i<0?'':runSetting(c,c.fields[i],i,true,label||c.fields[i].label,help??'');}
function runChoiceCards(c,name,choices){
 const f=c.fields.find(f=>f.name===name);if(!f||f.managed)return '';
 const enabled=c.fields.find(x=>x.name==='run_kraken');
 const value=c.tool==='hybas'&&name==='kraken_decision_mode'&&enabled&&!runFieldValue(c,enabled)?'off':runFieldValue(c,f);
 return `<div class="mode-cards" role="group" aria-label="${e(f.label)}">${choices.map(([v,label,desc])=>`<button type="button" class="mode-card ${value===v?'selected':''}" data-action="run-choice" data-id="${e(f.key)}" data-value="${e(v)}" data-choice-name="${e(name)}" aria-pressed="${value===v}"><span class="mode-card-top"><strong>${e(label)}</strong><span class="choice-mark">${value===v?icon('check'):''}</span></span><span>${e(desc)}</span></button>`).join('')}</div>`;
}
function runSettingTabs(c){
 const tab=editorUI(c).runSettingTab?.[c.tool]||guidedTabs[c.tool][0][0];
 return `<div class="settings-tabs" aria-label="Run settings categories">${guidedTabs[c.tool].map(([id,label,ico])=>`<button type="button" class="settings-tab ${id===tab?'active':''}" data-action="guided-run-tab" data-id="${id}" aria-pressed="${id===tab}">${icon(ico)}${label}</button>`).join('')}</div>`;
}
function runSettingsPanel(c){
 const defaults=c.scope==='defaults',tab=editorUI(c).runSettingTab?.[c.tool]||guidedTabs[c.tool][0][0],v=name=>{const f=c.fields.find(f=>f.name===name);return f?runFieldValue(c,f):null;};
 const fields=items=>`<div class="guided-grid">${items.map(x=>runGuideField(c,...x)).join('')}</div>`;
 const intro=(title,desc,tag='')=>`<div class="settings-intro"><div><h3>${title}</h3><p>${desc}</p></div>${tag?badge(tag,'green'):''}</div>`;
 let content='';
 if(tab==='advanced')content=`${intro('Every setting, when you need it.','Search all native options, or edit the full configuration including custom keys.')}<div class="advanced-actions">${btn('run-config-tab','Full YAML',false,'yaml','file')}</div>${advancedRunSettings(c)}`;
 else if(c.tool==='hybas'&&tab==='kraken'){
  content=intro('Choose how Kraken is used.',defaults?'Choose the starting mode for future drafts.':'Pick a mode for these drafts. Your saved settings remain unchanged.',v('run_kraken')&&v('kraken_decision_mode')!=='off'?'Kraken enabled':'Kraken off')+runChoiceCards(c,'kraken_decision_mode',[
   ['off','Off','Skip Kraken for this run.'],['manual','Manual','Use a specified target taxon.'],['auto','Auto','Use the native Auto rules.'],['aware','AWARE','Use the native AWARE rules.']]);
  content+=fields([['target_taxid','Target taxon ID','Optional unless required by the selected mode or configuration.'],['kraken_mem_mb','Kraken memory (MB)','Leave blank for the pipeline estimate.']]);
  content+=`<div class="settings-tip">${icon('info')}<span>Mode selection keeps the Kraken on/off switch in sync. Decision thresholds and additional options are available in Advanced.</span></div>`;
 }else if(c.tool==='hybas'&&tab==='assembly'){
  content=intro('Assembly & polishing','Choose a strategy, then adjust its settings.')+runChoiceCards(c,'polish_mode',[
   ['none','None','Keep the baseline assembly.'],['racon','Racon','Racon polishing.'],['medaka','Medaka','Medaka polishing.'],['tripolish','Tripolish','Native multi-step polishing.']]);
  content+=fields([...(v('polish_mode')==='racon'||v('polish_mode')==='tripolish'?[['racon_rounds','Racon rounds']]:[]),...(v('polish_mode')==='medaka'||v('polish_mode')==='tripolish'?[['medaka_model','Medaka model','Leave blank to retain native model selection.']]:[]),['run_polypolish','Polypolish','Keep this stage enabled where the selected read mode supports it.'],['keep_percent','Reads to retain (%)']]);
  content+=`<details class="guided-details"><summary>Assembly memory limits</summary>${fields([['assembly_mem_mb','Assembly memory (MB)'],['polishing_mem_mb','Polishing memory (MB)'],['spades_memory_gb','SPAdes memory (GB)']])}</details><p class="settings-note">The native dry-run checks the strategy against each sample’s read mode.</p>`;
 }else if(c.tool==='hybas'&&tab==='quality'){
  content=intro('Quality checks & annotation',defaults?'Choose the starting options for future drafts.':'Choose the outputs to include in these drafts.')+fields([['run_busco','BUSCO','Assembly completeness checks.'],['run_prokka','Prokka','Genome annotation.'],['run_multiqc','MultiQC','Combined quality report.'],['min_fasta_length','Minimum sequence length','Length threshold passed to the native workflow.']]);
 }else if(c.tool==='hybas'&&tab==='databases'){
  content=intro(defaults?'Default database locations':'Database locations for this run',wizardKind()==='edit-run'?'These are the database locations saved with this run.':defaults?'Saved locations are inherited by future drafts.':'These values start from your saved database selections.')+fields([['db_root','Database root folder','Base folder containing the database layout.'],['kraken_db_name','Kraken database folder','Folder name or path used by HybAs AWARE.'],['busco_lineage','BUSCO lineage','Lineage name or path used by HybAs AWARE.']]);
  content+=`<div class="settings-tip">${icon('folder')}<span>${defaults?'Discover or download databases in Databases & models. Save defaults to use these paths in future drafts.':'Discover or download databases in Databases & models. Your unfinished draft is retained; its settings stay unchanged until you edit them here.'}</span></div>`;
 }else if(c.tool==='basis'&&tab==='modules'){
  const modules=c.fields.filter(f=>f.name.startsWith('modules.')&&f.name.endsWith('.enabled')&&f.kind==='boolean');
  content=intro('Build your analysis',defaults?'Choose which modules future drafts start with.':'Switch modules on or off for these drafts.',modules.filter(f=>runFieldValue(c,f)).length+' enabled')+`<div class="guided-grid module-grid">${modules.map(f=>runGuideField(c,f.name,f.group.replace('Module: ',''),'')).join('')}</div><p class="settings-note">Module databases, tool options and per-module resources are available in Advanced.</p>`;
 }else if(c.tool==='basis'&&tab==='aware'){
  content=intro('BaSIS AWARE','Use the saved preset or choose another native preset.')+fields([['deep.aware.enabled','Enable AWARE',defaults?'Starting setting for future drafts.':'Use AWARE for these drafts.']])+runChoiceCards(c,'deep.aware.preset',[
   ['auto-strict','Strict','Native auto-strict preset.'],['auto-medium','Medium','Native auto-medium preset.'],['auto-loose','Loose','Native auto-loose preset.']])+`<p class="settings-note">Custom queue and tool settings remain available in Advanced.</p>`;
 }else if(c.tool==='basis'&&tab==='deep'){
  content=intro('DeepSafe','Optional settings are inherited from your saved configuration.')+fields([['deep.enabled','Enable DeepSafe','Include the DeepSafe workflow.'],['extend','Extend','Use the native Extend setting.'],['deep.threads','DeepSafe threads'],['deep.memory_mb','DeepSafe memory (MB)']])+`<p class="settings-note">Additional DeepSafe options and reference paths are available in Advanced.</p>`;
 }else if(c.tool==='basis'&&tab==='resources'){
  content=intro('Resources & storage','Choose how resources are supplied and where results are retained.')+runChoiceCards(c,'resources.policy',[
   ['auto','Automatic','Allow native resource preparation.'],['manual','Manual','Use the resources you supply.']])+fields([['resources.directory','Resources folder'],['storage.native_results_directory','Native results folder','Optional. Leave blank to use the pipeline location.']]);
 }
 return runSettingTabs(c)+`<div class="guided-panel">${content}</div>`;
}
function runReview(c,chosen,budget={cores:val('run-cores',S.resource_defaults?.[c.tool]?.cores??4),memory_mb:val('run-memory',S.resource_defaults?.[c.tool]?.memory_mb??16000)}){
 const v=name=>{const f=c.fields.find(f=>f.name===name);return f?runFieldValue(c,f):'—';};
 const pairs=c.tool==='hybas'?[['Kraken mode',v('run_kraken')?v('kraken_decision_mode'):'off'],['Polishing',v('polish_mode')],['BUSCO',v('run_busco')?'On':'Off'],['Prokka',v('run_prokka')?'On':'Off'],['MultiQC',v('run_multiqc')?'On':'Off']]:[['Enabled modules',c.fields.filter(f=>f.name.startsWith('modules.')&&f.name.endsWith('.enabled')&&runFieldValue(c,f)===true).length],['AWARE',v('deep.aware.enabled')?'On':'Off'],['DeepSafe',v('deep.enabled')?'On':'Off']];
 return `<div class="settings-intro"><div><h3>Ready to create your drafts?</h3><p>Each sample gets its own saved run. Nothing starts until you launch it.</p></div></div><div class="review-stats"><div><span>Samples</span><strong>${chosen.length}</strong></div><div><span>CPU per run</span><strong data-review-budget="cores">${e(budget.cores)} cores</strong></div><div><span>Memory per run</span><strong data-review-budget="memory">${e(budget.memory_mb)} MB</strong></div></div><dl class="review-settings">${pairs.map(([k,v])=>`<div><dt>${e(k)}</dt><dd>${e(v)}</dd></div>`).join('')}</dl><div class="review-samples">${chosen.map(s=>`<div>${icon('samples')}<strong>${e(s.id)}</strong><span>${e(s.mode.replaceAll('_',' '))}</span></div>`).join('')||'<p>No samples selected. Return to Samples to choose one.</p>'}</div><p class="settings-note">Saved defaults and existing runs are unchanged. Check each new draft with a native dry-run before preparing tools or running it.</p>`;
}
function wizardPage(){
 if(!U.wizard)return heading('RUN DRAFT','No unfinished draft.','Start a new draft from your registered samples.',btn('new-run','Create run drafts',true));
 const revision=U.wizard.kind==='edit-run',c=runEditor(),tab=U.runConfigTab||'samples';
 const empty=!c||(!revision&&!S.samples.some(s=>val('run-sample-'+s.id,false)));
 const ready=revision||!!activeInstall(val('run-tool','hybas'))?.ready;
 const next=tab==='review'?btn(revision?'save-run-revision':'create-runs',revision?'Create revised draft':'Create drafts',true,'','check',empty||!ready):btn('run-config-tab',tab==='samples'?'Continue to settings':'Review drafts',true,tab==='samples'?'settings':'review','arrow',empty);
 return heading(revision?'REVISE RUN':'NEW RUN',revision?'Edit run settings':'Create run drafts',revision?'Save a separate revision of this run.':'Samples → Settings → Review. Save your choices before starting any work.',btn('leave-wizard','Save & exit'))+
  (U.wizardRecovered?notice('Recovered unfinished edits','Your selection and settings were recovered from this browser tab. Review them before creating a run.'):'' )+
  (!ready?notice('Pipeline runner needs setup','Finish setup for the selected pipeline before creating drafts. You can keep editing here.',true):'')+
  `<section class="card run-wizard">${revision?renderRunRevision():renderNewRun()}<div class="wizard-footer"><div><strong id="wizard-save-status">${e(wizardSaveStatus())}</strong><p>Closing this tab may clear recovery. Saved runs are stored in your workspace.</p></div><div class="button-row">${btn('discard-wizard','Discard edits')}${tab!=='samples'&&!(revision&&tab==='settings')?btn('run-config-tab','Back',false,tab==='review'?'settings':'samples'):''}${next}</div></div></section>`;
}
function renderNewRun(){
 const tool=val('run-tool','hybas'),c=runEditor(),tab=U.runConfigTab||'samples',chosen=S.samples.filter(s=>val('run-sample-'+s.id,false));
 let body=`<div class="run-draft-intro"><strong>Your run, your settings.</strong><span>Start with saved defaults. Customize only the drafts you create here.</span></div><div class="form-grid run-budget-grid">${select('run-tool','Pipeline',['hybas','basis'].map(t=>[t,S.pipelines[t].name]),'hybas')}${field('run-cores','CPU cores per run',S.resource_defaults?.[tool]?.cores??Math.min(4,S.doctor.cpus||1),'number','min="1" max="512"')}${field('run-memory','Memory budget (MB)',S.resource_defaults?.[tool]?.memory_mb??16000,'number','min="512" max="2000000" step="512"')}</div><div class="tabs run-config-tabs">${[['samples','Samples'],['settings','Run settings'],['review','Review']].map(([id,label],i)=>`<button type="button" class="tab ${(tab==='yaml'?'settings':tab)===id?'active':''}" data-action="run-config-tab" data-id="${id}" ${!c?'disabled':''}><span class="step-number" aria-hidden="true">${i+1}</span>${label}</button>`).join('')}</div>`;
 if(!c)return body+'<p>Loading saved configuration…</p>';
 if(tab==='samples')body+=`<h3>Select samples</h3><p class="inline-info">These samples receive the same settings. Removing a sample also moves its inactive saved runs to Archived; files and reports are kept.</p>${S.samples.length?S.samples.map(s=>`<div class="sample-choice"><label class="source-card"><input type="checkbox" id="run-sample-${e(s.id)}" data-sample="${e(s.id)}" ${val('run-sample-'+s.id,false)?'checked':''}><span><strong>${e(s.id)}</strong><small>${e(s.path)}</small></span></label>${btn('remove-sample','Remove sample',false,s.id,'trash')}</div>`).join(''):empty('Add a sample first.','Register the folder containing your original inputs.',btn('new-sample','Add sample'))}`;
 else if(tab==='settings')body+=runSettingsPanel(c);
 else if(tab==='review')body+=runReview(c,chosen);
 else body+=`${runSettingTabs(c)}<div class="guided-panel"><div class="settings-intro"><div><h3>Full configuration</h3><p>Edit custom keys or paste your existing YAML. Returning to the controls checks its format.</p></div>${btn('run-config-tab','Back to controls',false,'settings')}</div>${area('run-yaml-'+tool,'Run configuration YAML',c.text,'spellcheck="false" rows="18" data-run-yaml="'+tool+'"')}</div>`;
 body+=`<div class="run-draft-summary"><span>${chosen.length} sample${chosen.length===1?'':'s'} selected · ${e(S.pipelines[tool].name)} · Settings apply to these drafts only</span>${btn('check-run-config','Check settings',false,'','check')}</div>`;
 return body;
}
function renderRunRevision(){
 const r=U.revision,c=runEditor(),tab=U.runConfigTab||'settings';if(!r||!c)return '<p>Loading saved run settings…</p>';
 let body=`<div class="run-draft-intro"><strong>Revise ${e(r.sample.id)}</strong><span>Start from this run’s saved settings. Saving creates a separate draft with the same inputs.</span></div><div class="revision-source">${badge(S.pipelines[r.tool].name,'blue')}<span>${e(r.id)}</span>${badge(r.sample.mode.replaceAll('_',' '))}</div><div class="form-grid revision-budget">${field('revision-cores','CPU cores per run',r.cores,'number','min="1" max="512"')}${field('revision-memory','Memory budget (MB)',r.memory_mb,'number','min="512" max="2000000" step="512"')}</div><div class="tabs run-config-tabs">${[['settings','Run settings'],['review','Review']].map(([id,label])=>`<button type="button" class="tab ${(tab==='yaml'?'settings':tab)===id?'active':''}" data-action="run-config-tab" data-id="${id}">${label}</button>`).join('')}</div>`;
 if(tab==='review')body+=runReview(c,[r.sample],{cores:val('revision-cores',r.cores),memory_mb:val('revision-memory',r.memory_mb)});
 else if(tab==='yaml')body+=`${runSettingTabs(c)}<div class="guided-panel"><div class="settings-intro"><div><h3>Full configuration</h3><p>Custom keys are preserved. Generated paths are assigned to the new draft.</p></div>${btn('run-config-tab','Back to controls',false,'settings')}</div>${area('run-yaml-'+c.tool,'Run configuration YAML',c.text,'spellcheck="false" rows="18" data-run-yaml="'+c.tool+'"')}</div>`;
 else body+=runSettingsPanel(c);
 return body+`<div class="run-draft-summary"><span>Original run and saved defaults stay unchanged.</span>${btn('check-run-config','Check settings',false,'','check')}</div>`;
}
function runMethods(r){
 const m=U.methods?.id===r.id?U.methods:null;
 let body=`<div class="settings-intro"><div><h3>Methods for your publication</h3><p>Generated from this run’s frozen settings and available execution records.</p></div>${btn('refresh-methods',m?'Refresh Methods':'Generate Methods',false,r.id,'clock')}</div>`;
 if(!m)return body+'<p class="settings-note">Generate a preview to review and download. No analysis or database download is started.</p>';
 body+=`<section class="methods-export"><div class="methods-status">${badge(m.state,m.state==='Completed workflow'?'green':'amber')}<span>${e(m.evidence_origin)}</span></div><div class="button-row methods-downloads">${btn('methods-download-txt','Download text',true,r.id,'download')}${btn('methods-download-md','Markdown',false,r.id,'download')}${btn('methods-download-html','Printable HTML',false,r.id,'download')}${btn('methods-download-zip','Reproducibility ZIP',false,r.id,'download')}</div><p class="settings-note">The ZIP includes Methods, frozen YAML, settings, recorded evidence and the pipeline citation when available. Exports contain sample identifiers and local paths; review them before sharing.</p><article class="methods-prose" aria-label="Methods preview">${m.paragraphs.map(p=>`<p>${e(p)}</p>`).join('')}</article><details class="guided-details" open><summary>Review before publication · ${m.warnings.length} notes</summary><ul class="methods-notes">${m.warnings.map(w=>`<li>${e(w)}</li>`).join('')}</ul></details><details class="guided-details"><summary>Recorded execution</summary><div class="padded">${m.execution.map(p=>`<p>${e(p)}</p>`).join('')}</div></details><details class="guided-details"><summary>Software versions</summary><div class="table-wrap"><table><thead><tr><th>Software</th><th>Recorded version</th><th>Scope</th></tr></thead><tbody>${m.software.map(x=>`<tr><td>${e(x.tool)}</td><td>${e(x.version)}</td><td>${e(x.scope)}</td></tr>`).join('')}</tbody></table></div></details><p class="settings-note">Generated ${e(m.generated_at)} · Template v${e(m.generator_version)} · <a href="${e(safeURL(m.citation_url))}" target="_blank" rel="noopener noreferrer">Pinned pipeline source ↗</a></p></section>`;
 return body;
}
function savedValue(value){
 if(typeof value==='boolean')return badge(value?'On':'Off',value?'green':'');
 if(value===null||value==='')return '<span class="saved-unset">Not set</span>';
 const labels={off:'Off',aware:'AWARE',auto:'Auto',manual:'Manual',none:'None',racon:'Racon',medaka:'Medaka',tripolish:'Tripolish'};
 return e(typeof value==='object'?JSON.stringify(value):Object.hasOwn(labels,value)?labels[value]:value);
}
function savedFields(fields,fullNames=false){
 return `<dl class="saved-settings-grid">${fields.map(f=>`<div class="saved-setting"><dt>${e(f.name.startsWith('modules.')&&f.name.endsWith('.enabled')?f.group.replace('Module: ',''):f.label)}</dt><dd data-saved-setting="${e(f.name)}">${savedValue(f.value)}</dd>${fullNames?`<small>${e(f.name)}</small>`:''}</div>`).join('')}</dl>`;
}
function runReadiness(r,d){
 const p=d.readiness;if(!p)return '';
 const next=p.next,disabled=r.busy||r.archived,validated=p.validated&&!p.blocked;
 const actionText={review:'Review the items that need attention.', 'dry-run':'Next: validate this run.', 'check-environments':'Next: check this run’s environment cache.',prepare:'Next: review the missing environments.',run:'Listed environments are available. Execution checks them again.'};
 return `<section class="run-readiness" aria-label="Run checks"><div class="readiness-heading"><h3>Run checks</h3>${btn('run-detail','Refresh checks',false,r.id,'clock',r.busy)}</div><p class="readiness-next">${e(actionText[next])}</p><div class="button-row saved-run-actions">${r.archived?btn('restore-run','Restore run',true,r.id,'arrow'):btn('dry-run','Validate run',next==='dry-run',r.id,'check',disabled||p.blocked)}${btn('check-run-environments','Check environments',next==='check-environments',r.id,'check',disabled||!validated)}${btn('prepare','Install environments',next==='prepare',r.id,'download',disabled||!validated)}${btn('run','Run / resume',next==='run',r.id,'play',disabled||!validated||p.execution_blocked||p.missing_environments>0)}${btn('view-results','View results',false,r.id,'file')}</div><p class="readiness-policy">Run / resume reuses matching environments. Installation happens only when you choose Install environments.</p><div class="readiness-grid">${p.rows.map(x=>`<div class="readiness-item ${e(x.level)}" data-readiness="${e(x.id)}"><div><strong>${e(x.title)}</strong>${badge(x.state,x.level==='good'?'green':x.level==='error'?'red':x.level==='warning'?'amber':'')}</div><p>${e(x.text)}</p>${x.checked_at?`<small>Checked ${e(new Date(x.checked_at).toLocaleString())}</small>`:''}${x.action==='budget'&&r.run_type!=='followup'?quiet('edit-run-settings','Review run budget',r.id):''}${x.id==='pipeline'?quiet('go-install','Open setup'):''}${x.id==='databases'&&r.run_type!=='followup'?quiet('saved-run-tab','Review saved paths','databases'):''}</div>`).join('')}</div>${p.destinations?.length?`<details class="guided-details"><summary>Checked destinations</summary>${p.destinations.map(x=>`<div class="readiness-environment"><strong>${e(x.label)}</strong><code>${e(x.path)}</code><span>${e(x.text)}</span></div>`).join('')}</details>`:''}${p.environments.length?`<details class="guided-details"><summary>Listed tool environments · ${p.environments.length}</summary>${p.environments.map(x=>`<div class="readiness-environment"><strong>${e(x.name)}</strong>${badge(x.state,x.state==='Available'?'green':'amber')}<code>${e(x.path)}</code></div>`).join('')}</details>`:''}</section>`;
}
function renderSavedRun(r,d){
 const c=d.settings,tabs=[['overview','Overview','home'],...(r.run_type==='followup'?[['advanced','Advanced','config']]:guidedTabs[r.tool]),['activity','Activity','jobs'],['results','Results','file'],['methods','Methods','file']];
 let tab=U.runDetailTab||'overview';if(!tabs.some(t=>t[0]===tab)){tab='overview';U.runDetailTab=tab;writeRoute(true);}
 const included=c.fields.filter(f=>f.included),settings=included.filter(f=>!f.managed);
 let body=`<div class="detail-pills">${badge(S.pipelines[r.tool].name,'blue')}${r.archived?badge('Archived'):statusBadge(r.status)}${badge(d.readiness?.validated?'Native dry-run passed':'Needs validation',d.readiness?.validated?'green':'amber')}</div><div class="settings-tabs saved-run-tabs" aria-label="Saved run sections">${tabs.map(([id,label,ico])=>`<button type="button" class="settings-tab ${tab===id?'active':''}" data-action="saved-run-tab" data-id="${id}" aria-pressed="${tab===id}">${icon(ico)}${label}</button>`).join('')}</div>`;
 if(tab==='overview'){
  const names=r.tool==='hybas'?['kraken_decision_mode','polish_mode','run_busco','run_prokka','run_multiqc']:['deep.aware.enabled','deep.aware.preset','deep.enabled'];
  body+=`<div class="settings-intro"><div><h3>Your saved run</h3><p>Review its settings, edit a new revision, or continue execution.</p></div></div>${runReadiness(r,d)}<div class="review-stats"><div><span>Read mode</span><strong>${e(r.sample.mode.replaceAll('_',' '))}</strong></div><div><span>CPU per run</span><strong>${r.cores} cores</strong></div><div><span>Memory per run</span><strong>${r.memory_mb} MB</strong></div></div>${savedFields(settings.filter(f=>names.includes(f.name)))}<details class="guided-details"><summary>Folders and source version</summary><dl class="saved-run-locations"><div><dt>Input folder</dt><dd>${e(r.sample.path)}</dd></div><div><dt>Results folder</dt><dd>${e(d.output)}</dd></div><div><dt>Source version</dt><dd>${e(r.commit?.slice(0,12))}${r.parent?' · Parent '+e(r.parent):''}</dd></div></dl></details><div class="button-row">${r.tool==='hybas'?btn('polish','Clone / compare',false,r.id,'branch',r.archived):''}${r.can_handoff?btn('handoff','Create BaSIS AWARE draft',false,r.id,'',r.archived):''}${r.can_followup?btn('followup','Review follow-up',false,r.id,'',r.archived):''}</div>`;
 }else if(tab==='activity'){body+=runActivity(r);
 }else if(tab==='results'){body+=runResults(r);
 }else if(tab==='methods'){body+=runMethods(r);
 }else if(tab==='advanced'){
  body+=`<div class="settings-intro"><div><h3>Technical details</h3><p>The exact saved configuration and command are available here.</p></div></div><details class="config-section"><summary>Execution command</summary><div class="padded"><pre class="code-block saved-code">${e(d.command)}</pre></div></details><details class="config-section"><summary>Frozen YAML</summary><div class="padded"><pre class="code-block saved-code">${e(d.config)}</pre>${btn('download-frozen','Download frozen YAML',false,r.id)}</div></details><details class="config-section"><summary>All saved fields</summary><div class="padded">${savedFields(included,true)}</div></details><p class="settings-note">${e(d.reuse)}</p>`;
 }else if(tab==='databases'||tab==='resources'){
  body+=`<div class="settings-intro"><div><h3>Databases used by this run</h3><p>These paths belong to this saved run, including any locations selected before it was created.</p></div></div><div class="saved-databases">${(d.database_paths||[]).map(x=>`<div class="saved-database">${icon('resources')}<div><strong>${e(x.name)}</strong><p data-saved-database="${e(x.id)}">${e(x.path||'Pipeline default')}</p></div></div>`).join('')}</div>${savedFields(settings.filter(f=>['Databases','Databases & storage'].includes(f.group)))}`;
 }else{
  const groups={kraken:'Kraken & AWARE',assembly:'Assembly & polishing',quality:'Quality & annotation',aware:'BaSIS AWARE',deep:'DeepSafe'};
  const fields=tab==='modules'?settings.filter(f=>f.name.startsWith('modules.')&&f.name.endsWith('.enabled')):settings.filter(f=>f.group===groups[tab]);
  body+=`<div class="settings-intro"><div><h3>${e(tabs.find(t=>t[0]===tab)?.[1]||'Settings')}</h3><p>Values saved with this run. Use Edit settings to create a revision.</p></div>${badge('Saved values')}</div>${fields.length?savedFields(fields):'<p class="settings-note">No explicit values were saved in this section. The pipeline uses its defaults.</p>'}`;
 }
 return body;
}
function dbPath(t,id){return 'dbpath-'+t+'-'+id;}
function dbState(t){U.databaseStates ||= {};return U.databaseStates[t] ||= {revision:S.databases[t].revision,checks:{},resets:{}};}
function clearDatabases(t){for(const key of Object.keys(U.drafts))if(key.startsWith('dbpath-'+t+'-')||key.startsWith('dbcand-'+t+'-'))delete U.drafts[key];U.databaseStates ||= {};delete U.databaseStates[t];for(const r of S.databases[t].rows){if(!r.managed){setDraft(dbPath(t,r.id),r.path);setDraft('dbcand-'+t+'-'+r.id,'');}}}
function resourceDownloads(tool,key){
 const rows=S.database_downloads.filter(d=>d.tool===tool&&d.resource===key).slice(-3).reverse();
 return rows.map(d=>`<div class="resource-download"><div class="card-head"><strong>Download ${d.id.slice(-6)}</strong>${statusBadge(d.status==='completed'?'completed':d.job_status)}</div><p class="path-cell">${e(d.destination)}</p>${d.error?`<p class="error">${e(d.error)}</p>`:''}<div class="button-row">${d.status==='completed'?btn('use-downloaded','Select downloaded location',false,d.id,'check'):['failed','cancelled','interrupted'].includes(d.job_status)?btn('resume-download','Resume download',false,d.id,'play'):''}${btn('job-log','Download log',false,d.job,'file')}</div></div>`).join('');
}
function resources(embedded=false){const t=U.resourceTool||'hybas',p=S.databases[t],state=dbState(t),scan=state.scan;
const dirty=p.rows.filter(r=>!r.managed&&(state.resets[r.id]||val(dbPath(t,r.id),r.path)!==r.path));
return (embedded?'':heading('DATABASES & MODELS','Your databases, connected.','Choose installed resources for each pipeline. Save selected locations for future runs. Existing drafts keep their own paths until you create a revision.',btn('save-databases','Save for future runs',true,t,'check',!dirty.length)+btn('use-databases-for-runs','Update saved drafts',false,t,'resources',!!dirty.length||!visibleRuns().some(r=>r.tool===t&&!r.busy&&r.run_type!=='followup'))))+
`${embedded?'':`<div class="tabs">${['hybas','basis'].map(x=>`<button class="tab ${x===t?'active':''}" data-action="database-tool" data-id="${x}">${e(S.pipelines[x].name)}</button>`).join('')}</div>`}
<section class="card database-scan"><div class="card-head"><div><h2>Find installed databases</h2><p>Scan known locations and common folders, or include a folder on another disk.</p></div>${btn('scan-databases','Auto scan',true,t,'resources')}</div><div class="card-body"><div class="folder-address">${field('dbscan-'+t,'Additional scan folder (optional)','','text','placeholder="/home/jimi/databases or D:\\databases"')}${btn('browse-folder','Browse',false,'dbscan-'+t,'folder')}</div><p class="inline-info">A valid saved location is preferred. Otherwise the scan suggests a recognized installation; review its release and scope before saving.</p>
${scan?`<details class="database-scan-details"><summary>${Object.values(scan.candidates).reduce((n,a)=>n+a.length,0)} candidates found · ${scan.visited} folders checked${scan.limited?' · scan limits reached':''}</summary><p>${e(scan.scope)}</p>${scan.limits.length?`<p>Reached: ${e(scan.limits.join(', '))}. Scan a more specific folder to continue.</p>`:''}${scan.inaccessible?`<p>${scan.inaccessible} inaccessible folders skipped.</p>`:''}<p class="path-cell">${scan.roots.map(e).join('<br>')}</p></details>`:''}</div></section>
${!S.download_options[t].available?notice('Set up database downloads','In Install & update, select and finish setup for the recommended pipeline version.'):''}${scan?.invalid.length?`<details class="card database-rejected"><summary>${scan.invalid.length} locations need attention</summary>${scan.invalid.map(x=>`<div><strong>${e(p.rows.find(r=>r.id===x.id)?.name||x.id)}</strong><p class="path-cell">${e(x.path)}</p><p class="error">${e(x.message)}</p></div>`).join('')}</details>`:''}
${state.revision!==p.revision?notice('Saved defaults have changed','Reload locations before saving these choices.',true):''}
<div class="database-summary"><span>${dirty.length?dirty.length+' unsaved selection'+(dirty.length===1?'':'s'):'Saved locations'} · ${t==='basis'?'Missing resources follow the '+e(p.policy)+' preparation policy.':'Download databases here, then save the prepared locations.'}</span>${btn('reload-databases','Reload locations',false,t)}</div>
<div class="database-grid">${p.rows.filter(r=>!r.managed).map(r=>{const id=dbPath(t,r.id),value=val(id,r.path),checked=state.checks[r.id],choices=scan?.candidates[r.id]||[],chosen=choices.find(c=>c.path===value),status=checked?.path===value?checked:chosen,reset=state.resets[r.id],invalid=scan?.invalid.find(x=>x.id===r.id&&x.path===value);
return `<section class="card database-card"><div class="database-title"><h2>${e(r.name)}</h2>${badge(reset?'Pipeline default':status?'Layout recognized':value?'Location not checked':'Pipeline default',status?'green':'')}</div><p class="database-description">${e(r.description)}</p><div class="folder-address">${field(id,r.name+' location',r.path,'text','placeholder="Choose an installed database folder"')}${btn('browse-database','Browse',false,r.id,'folder')}</div>
${choices.length?select('dbcand-'+t+'-'+r.id,'Detected installations · '+choices.length,[['','Choose a detected location'],...choices.map(c=>[c.path,(c.preferred?'Preferred · ':'')+(c.version?c.version+' · ':'')+c.path])],chosen?.path||''):''}
${status?`<p class="database-evidence">${icon('check')}<span>${e(status.reason||'Expected files found. Native runtime checks still apply.')}${status.version?'<br>Recorded version: '+e(status.version):''}</span></p>`:invalid?`<p class="error">${e(invalid.message)}</p>`:''}
${reset?'<p class="inline-info">Save to use the pipeline default for this resource.</p>':`<p class="database-selection-state">${value!==r.path?'Selected · not saved':r.path?'Saved for future runs':'Using the pipeline default'}</p>`}
<div class="database-actions">${btn('check-database','Check location',false,r.id,'check',!value||!!reset)}${btn('reset-database','Use pipeline default',false,r.id,'',!value&&!reset)}${btn('download-database',r.id==='kraken'?'Download and build':'Download and prepare',false,r.id,'download',!S.download_options[t].available)}</div>${resourceDownloads(t,r.id)}</section>`;}).join('')}</div>
<section class="card database-managed"><div class="card-head"><h2>Tool-managed resources</h2></div><div class="card-body">${p.rows.filter(r=>r.managed).map(r=>`<div><strong>${e(r.name)}</strong>${badge('Tool-managed')}<p>${e(r.description)}</p></div>`).join('')}</div></section>
${notice('A quick layout check','Discovery reads filenames and small metadata files. The native pipeline performs its full resource checks when preparing a run. Scanning does not download databases.')}
<div class="database-footer"><span>${dirty.length?'Selections are not saved yet. Save to use them in future drafts.':'Existing run drafts keep their frozen resource paths.'}</span>${btn('save-databases','Save for future runs',true,t,'check',!dirty.length)}</div>`;}
function render(){draftInputs();$('#navigation').innerHTML=navs.map(([key,label,ico])=>`${key==='install'?'<div class="nav-divider"></div>':''}<a href="#${key}" aria-label="${e(label)}" data-nav="${key}" class="nav-item ${(['run','draft'].includes(U.view)?'runs':U.view)===key?'active':''}" ${(['run','draft'].includes(U.view)?'runs':U.view)===key?'aria-current="page"':''}>${icon(ico)}<span>${label}</span>${key==='jobs'&&S.jobs.some(j=>['queued','running'].includes(j.status))?`<span class="nav-count">${S.jobs.filter(j=>['queued','running'].includes(j.status)).length}</span>`:''}</a>`).join('');$('#crumb').textContent=U.view==='draft'?'Runs / Unfinished draft':U.view==='run'?'Runs / '+(runBy(U.runId)?.label||'Saved run'):navs.find(n=>n[0]===U.view)?.[1]||'Overview';$('#app-version').textContent='v'+S.version;$('#workspace-name').textContent=S.workspace.split('/').pop();$('#workspace-name').title=S.workspace;const running=S.jobs.filter(j=>j.status==='running').length,queued=S.jobs.filter(j=>j.status==='queued').length;$('#queue-status').textContent=running||queued?`${running} running · ${queued} queued`:'No active jobs';$('.status-dot').classList.toggle('active',!!running);
$('#main').innerHTML=appUpdateNotice()+wizardBanner()+(!U.modal&&U.error?`<div class="error" role="alert">${e(U.error)}</div>`:'')+({overview,samples,runs:runsPage,run:runPage,draft:wizardPage,comparisons,jobs,reports:reportsPage,install,resources,configure:configuration}[U.view]||overview)();
if(!$('#connection-status')){const x=document.createElement('div');x.id='connection-status';x.className='connection-status';$('.topbar').append(x);}renderBusy();renderModal();if(['configure','draft'].includes(U.view)&&!U.modal)filterRunSettings();persistWizard();schedulePoll();
if(U.view==='run'&&!U.modal&&!U.pending&&!U.queuedRequest&&!U.error&&runBy(U.runId)&&U.detail?.id!==U.runId)request('inspect_run',{id:U.runId});
if(U.view==='draft'&&U.wizard&&!runEditor()&&!U.pending&&!U.error){if(wizardKind()==='edit-run')request('edit_run_settings',{id:U.wizard.id});else request('run_config_editor',{tool:val('run-tool','hybas'),view:'samples'});}
if(U.view==='configure'&&!U.modal&&!U.pending&&!U.queuedRequest&&!U.error){const c=defaultEditor();if(!c||!defaultsDirty(c)&&c.revision!==S.databases[U.tool].revision)request('default_config_editor',{tool:U.tool});}}
function schedulePoll(){
 clearTimeout(pollTimer);if(U.pending||U.queuedRequest||!S.jobs.some(j=>['queued','running'].includes(j.status)))return;
 pollTimer=setTimeout(()=>{
  const active=parentElement.activeElement||document.activeElement;
  const editing=active&&root.contains(active)&&active.matches('input,textarea,select,[contenteditable="true"]');
  if(editing||(U.modal&&!['run','log','usage'].includes(U.modal.kind))){schedulePoll();return;}
  const kind=U.modal?.kind==='usage'?'job_metrics':U.modal?.kind==='log'?'job_log':U.view==='run'&&runBy(U.runId)?'inspect_run':'refresh';
  request(kind,U.modal?{id:U.modal.id}:U.view==='run'?{id:U.runId}:{},true);
 },4000);
}
function renderModal(){const m=U.modal;if(!m){$('#modal-root').innerHTML='';$('.app-shell').removeAttribute('inert');return;}
let title='',desc='',body='',foot='',wide=false;
if(m.kind==='discard-wizard'){title='Discard unfinished edits?';desc='Only the unfinished editor will be cleared.';body='<p>Saved runs, samples, outputs and default settings are kept. Close this dialog to continue editing.</p>';foot=btn('confirm-discard-wizard','Discard unfinished edits',true,'','trash');}

if(m.kind==='app-update'){
 wide=true;title='Update Bactologix';desc='Choose an application release from GitHub.';
 const u=S.app_update,c=u.catalog,rows=c?.releases||[],chosen=rows.find(r=>String(r.id)===val('app-release'));
 body=`<div class="card-head"><div><strong>Running v${e(S.version)}</strong><p class="path-cell">Application environment: ${e(u.environment)}</p></div>${btn('refresh-app-releases','Refresh releases')}</div><p class="inline-info">${c?'Last checked: '+e(c.checked_at):'Checking available releases…'}</p>
 <div class="version-list">${rows.map(r=>`<label class="source-card"><input type="radio" name="app-version-choice" value="${r.id}" ${String(r.id)===val('app-release')?'checked':''} ${!r.available||r.version===S.version?'disabled':''}><span><strong>${e(r.tag)}</strong> ${badge(r.prerelease?'Prerelease':'Stable',r.prerelease?'amber':'green')}${r.version===S.version?' '+badge('Running','green'):r.older?' '+badge('Older release','amber'):''}<small>${e(r.name)} · ${e(r.published_at||'')}</small>${!r.available?'<small>Installable wheel with checksum is not available.</small>':''}<a href="${e(safeURL(r.url))}" target="_blank" rel="noopener noreferrer">Release on GitHub ↗</a>${r.notes?`<details><summary>Release notes</summary><pre class="release-notes">${e(r.notes)}</pre></details>`:''}</span></label>`).join('')}</div>
 ${notice('Application update','Installs the selected version in the environment shown above. Finish queued/running jobs first. '+(u.automatic_restart?'After verification, the app restarts on this address and this tab refreshes automatically. Keep the terminal and tab open.':'This session needs a manual restart after installation: press Ctrl+C in its terminal, then run bactologix. Future updates launched with the new command restart automatically.')+' Pipeline runners, databases and run folders stay separate.')}
 ${chosen?.older?notice('Older release selected','Earlier app versions may support fewer pipeline revisions. Choose the intended release before installing.',true):''}
 ${!u.can_install?notice(u.restart_required?'Restart required':'Finish active jobs first',u.restart_required?u.automatic_restart?'The app is restarting automatically. Keep this tab open.':'Restart the app before installing another version.':'You can browse releases now and install when the job queue is idle.',true):''}`;
 foot=btn('install-app-release',chosen?'Install '+chosen.tag:'Choose a release',true,'','download',!chosen?.available||!u.can_install||chosen.version===S.version);
}
if(m.kind==='apply-databases'){
 wide=true;title='Use saved databases';desc='Select saved locations for revised run drafts.';
 const plan=U.databaseRunPlan;
 body=notice('Keep each run reproducible','This creates a revised draft with the selected saved database paths. It keeps the input files, pipeline version and other settings. The revised draft needs a new dry-run. Save changes on the database page before using this action.');
 if(!plan){
  const runs=visibleRuns().filter(r=>r.tool===m.id&&!r.busy&&r.run_type!=='followup');
  body+=`<div class="version-list">${runs.map(r=>`<label class="source-card"><input type="checkbox" id="dbapply-run-${e(r.id)}" data-apply-run="${e(r.id)}" ${val('dbapply-run-'+r.id,false)?'checked':''}><span><strong>${e(r.label)}</strong><small>${e(r.id)}</small></span></label>`).join('')}</div>`;
  foot=btn('review-run-databases','Review database changes',true,'','resources',!runs.some(r=>val('dbapply-run-'+r.id,false)));
 }else{
  body+=plan.runs.map(r=>`<section class="database-apply-run"><h3>${e(r.label)}</h3><p class="path-cell">${e(r.id)}</p>${r.rows.map(x=>`<label class="database-apply-row"><input type="checkbox" id="dbapply-resource-${e(r.id)}-${e(x.id)}" data-apply-resource="${e(x.id)}" data-apply-run-id="${e(r.id)}" ${val('dbapply-resource-'+r.id+'-'+x.id,x.valid&&x.changed)?'checked':''} ${!x.valid||!x.changed?'disabled':''}><span><strong>${e(x.name)}</strong><small>Run: ${e(x.current||'Pipeline default')}</small><small>Saved: ${e(x.saved||'Not set')}</small>${x.error?`<small class="error">${e(x.error)}</small>`:!x.changed?'<small>Already using this saved location.</small>':'<small class="database-evidence">Ready to apply</small>'}</span></label>`).join('')}</section>`).join('');
  foot=btn('change-database-runs','Choose runs')+btn('apply-run-databases','Use saved databases',true,'','check',!plan.runs.some(r=>r.rows.some(x=>x.valid&&x.changed&&val('dbapply-resource-'+r.id+'-'+x.id,true))));
 }
}
if(m.kind==='environments'){
 wide=true;const item=S.installs.find(i=>i.id===m.id),panel=S.environments[m.id],checking=m.action==='check',repairing=m.action==='repair',advanced=$('#environment-storage')?.open??!!m.advanced;
 title=checking?'Check environments':repairing?'Repair environments':'Install environments';desc=S.pipelines[item.tool].name+' · '+item.version.ref;
 body=`<p>${checking?'Check which selected tools are installed and working. This does not install or change environments.':repairing?'Check selected environments, prepare missing or incomplete ones, and apply supported dependency repairs. Healthy environments are reused.':'Prepare the selected tools for this pipeline. Matching completed environments are reused automatically; missing environments are installed.'} No sample or run draft is needed.</p>${notice('Reuse is automatic','Pipeline updates keep the previous version’s environment folder. Snakemake identifies matching environments in that folder. You do not need to select a reuse option.')}<p class="inline-info">${checking?'Checks inspect deployment completion, run native tool checks and test NanoPlot HTML/PNG rendering when included.':repairing?'The known NanoPlot 1.46.2 / MultiQC 1.32 conflict is repaired with Plotly 6.1.2 and Kaleido 0.2.1. Package changes are recorded. Incomplete copies are backed up before rebuilding; unsupported failures are reported without deleting complete environments. Shared runs using these environments will see repaired packages.':'Installation prepares software only. It does not run samples or start database downloads. A changed requirement may need a new environment.'}</p>${panel.error?notice('Environment inventory unavailable',e(panel.error),true):''}
 <div class="environment-selection"><strong id="environment-selection-count"></strong><div class="button-row">${btn('select-environments','Select all')}${btn('clear-environments','Clear selection')}</div></div>
 <div class="environment-list">${panel.rows.map(r=>`<label class="environment-row"><input id="envpick-${e(m.id)}-${e(r.id)}" data-environment="${e(r.id)}" type="checkbox" ${val('envpick-'+m.id+'-'+r.id,true)?'checked':''}><span><strong>${e(r.name)}</strong><small>${e(r.id)}${r.checked_at?' · Checked '+e(r.checked_at):''}</small>${r.path?`<small class="path-cell">${e(r.path)}</small>`:''}${r.message?`<small>${e(r.message)}</small>`:''}</span>${badge(r.status,r.status==='Verified'?'green':['Check failed','Repair failed','Incomplete'].includes(r.status)?'amber':'')}</label>`).join('')}</div>
 <details id="environment-storage" class="guided-details" ${advanced?'open':''}><summary>Advanced: environment storage</summary><p class="inline-info">Change this only to use a different storage location or a cache from an external installation. Choose the parent folder containing environment subfolders, not an individual Conda environment. Existing runs retain their saved folder.</p><div class="folder-address">${field('environment-cache-'+m.id,'Environment cache folder',panel.cache)}${btn('browse-env-cache','Browse',false,m.id,'folder')}</div><div class="button-row">${btn('save-env-cache','Save folder',false,m.id)}</div><p id="environment-cache-note" class="inline-info"></p></details>`;
 foot=btn(checking?'check-environments':repairing?'repair-environments':'install-environments',checking?'Check selected':repairing?'Repair selected':'Install selected',true,m.id,checking?'check':'download',panel.busy);
}
if(m.kind==='uninstall'){
 wide=true;title='Uninstall pipeline version';const plan=U.uninstallPlan?.id===m.id?U.uninstallPlan:null;desc=plan?S.pipelines[plan.tool].name+' · '+plan.version:m.id;
 body=plan?`<div class="uninstall-scope"><h3>Remove this version</h3><p>The version entry and ${plan.source_exists?'this managed source copy':'its missing source reference'} will be removed.</p><code>${e(plan.source)}</code></div><div class="uninstall-kept"><h3>Keep on this machine</h3><ul><li>Runner environment: <span class="path-cell">${e(plan.controller||'None recorded')}</span></li><li>Tool environment cache: <span class="path-cell">${e(plan.cache)}</span></li><li>Databases, saved runs, results and logs</li><li>Any original checkout connected from another folder</li></ul></div>${plan.blocked.length?notice('This version is still needed',plan.blocked.map(e).join('<br>'),true):'<p class="inline-info">This is not the active version and no saved runs depend on it. Uninstall does not remove retained environments to reclaim their disk space.</p>'}`:'<p>Checking version dependencies and source files…</p>';
 foot=btn('uninstall-version','Uninstall this version',true,m.id,'trash',!plan||!!plan.blocked.length);
}
if(m.kind==='download-database'){
 const t=U.resourceTool||'hybas',r=S.databases[t].rows.find(x=>x.id===m.id);title=(r.id==='kraken'?'Download and build ':'Download and prepare ')+r.name;desc='Choose a location and review the preparation steps.';
 body=`<div class="folder-address">${field('download-parent','Download location',S.download_options[t].parent)}${btn('browse-folder','Browse',false,'download-parent','folder')}</div><p>A new subfolder will be created here. After preparation, use the downloaded location and save it for future run drafts.</p><div class="form-grid">${field('download-cores','Threads',4,'number','min="1" max="1024"')}${field('download-memory','Memory budget (MB)',16000,'number','min="1" max="10000000"')}${r.id==='busco'?field('download-lineage','BUSCO lineage',S.databases[t].lineage||'bacteria_odb10'):''}</div>${notice('What will happen','The job may first install the tool environment required by this pipeline’s database preparation rule. It then downloads '+(r.id==='kraken'?'and builds the database':'and prepares the resource')+' in a new subfolder at the location above. No sample analysis is started. Select the completed location and save it for future runs.')} ${notice(r.id==='kraken'?'Kraken2 standard database build':'Native database preparation',r.id==='kraken'?'This downloads and builds the standard database. It can need substantial disk space, RAM and time; choose resources suitable for your machine.':'The pipeline downloads and prepares this resource and any required dependencies. Follow progress or stop the job in Jobs & progress.')}`;
 foot=btn('start-database-download',r.id==='kraken'?'Download and build':'Download and prepare',true,m.id,'download');
}
if(m.kind==='sample'){title='Add a sample';desc='Register an existing folder on this machine.';body=`<div class="form-grid">${field('sample-id','Sample ID','','text','required')}${select('sample-mode','Read mode',[['hybrid','Hybrid'],['ont_only','ONT only'],['illumina_only','Illumina only']],'hybrid')}<div class="field wide">${field('sample-path','Input folder','','text','placeholder="/home/jimi/bactomics/sample01" required')}${btn('browse-folder','Browse folders',false,'sample-path','folder')}</div></div>${notice('Input layout','HybAs AWARE: raw/illumina and raw/nanopore. BaSIS AWARE: annotation/&lt;sample_id&gt;.fna, .faa and .gff, plus .gbk when required.')}`;foot=btn('save-sample','Register sample',true);}
if(m.kind==='folders'){
 wide=true;title='Choose a folder';desc='Browse the machine running Bactologix. Files stay on disk.';
 const b=U.browser;
 body=`<div class="folder-address">${field('folder-path','Folder path',b?.path||'','','placeholder="/home/jimi or C:\\Users\\…"'+(!b?' disabled':''))}${btn('folder-go','Go')}</div>`;
 if(b){
  body+=`<div class="folder-shortcuts">${btn('folder-open','Up',false,b.parent,'arrow',b.path===b.parent)}${b.bookmarks.map(x=>btn('folder-open',x.name,false,x.path,'folder')).join('')}</div>
   <div class="folder-filters">${field('folder-query','Find a subfolder',val('folder-query'))}<label class="folder-hidden"><input id="folder-hidden" type="checkbox" ${val('folder-hidden',false)?'checked':''}> Show hidden</label>${btn('folder-filter','Apply')}</div>
   <div class="folder-location">${icon('folder')}<strong>${e(b.path)}</strong></div>
   <div class="folder-list" aria-label="Subfolders">${b.folders.length?b.folders.map(x=>`<button type="button" class="folder-entry" data-action="folder-open" data-id="${e(x.path)}">${icon('folder')}<span>${e(x.name)}${x.link?' <small>shortcut</small>':''}</span>${icon('chevron')}</button>`).join(''):'<p class="folder-empty">No matching subfolders. You can select this folder.</p>'}</div>
   <div class="folder-pagination"><small>${b.total} matching folders · ${b.files} files${b.limited?' · Listing limited; enter a full path to go deeper.':''}</small><div class="button-row">${btn('folder-page','Previous',false,String(Math.max(0,b.offset-100)),'',!b.offset)}${btn('folder-page','Next',false,String(b.offset+100),'',!b.more)}</div></div>
   ${Object.entries(b.layout).some(([,found])=>found)?`<div class="detail-pills">${Object.entries(b.layout).filter(([,found])=>found).map(([name])=>badge(name,'green')).join('')}</div>`:''}`;
 }
 else body+='<p class="inline-info">Loading folders…</p>';
 foot=btn('folder-select','Select this folder',true,'','check',!b);
}
if(m.kind==='existing'){
 wide=true;const t=m.id, info=U.localInstall?.tool===t?U.localInstall:null, d=U.discovery, envs=U.localEnvs?.environments||[];
 title='Connect existing '+S.pipelines[t].name;desc='Choose a checkout and reuse a compatible runner.';
 body=`<div class="local-install-section"><h3>1. Find your pipeline</h3><div class="folder-address">${field('existing-source','Pipeline folder','','text','placeholder="/home/jimi/Bactomics_'+(t==='hybas'?'HybAs':'BaSIS')+'"')}${btn('browse-folder','Browse',false,'existing-source','folder')}${btn('inspect-local','Inspect folder',true,t)}</div>
 <details class="config-section" ${d?'open':''}><summary>Search this machine</summary><div class="padded"><p class="inline-info">Quick scan: Home, current folder and /opt, up to four levels. Choose a folder to search elsewhere, including a mounted drive.</p><div class="folder-address">${field('discovery-root','Search folder (optional)')}${btn('browse-folder','Browse',false,'discovery-root','folder')}${btn('scan-local','Scan for '+S.pipelines[t].name,false,t)}</div>
 ${d?`<p class="inline-info">Checked ${d.visited} folders${d.limited?' · scan limit reached':''}. ${d.candidates.length} candidate(s).</p><div class="local-candidates">${d.candidates.map(x=>`<div class="local-candidate"><div><strong class="path-cell">${e(x.path)}</strong><small>${e(x.error||x.ref+' · '+x.commit.slice(0,12))}</small>${badge(x.supported?'Supported version':'Needs review',x.supported?'green':'amber')}</div>${btn('choose-local','Inspect',false,x.path,'',!!x.error)}</div>`).join('')||'<p>No checkout found here. Browse directly to its folder or scan a different location.</p>'}</div>`:''}</div></details>
 ${info?`<div class="local-inspection">${badge(info.supported?'Supported source':'Adapter review needed',info.supported?'green':'amber')}<strong>${e(info.ref)}</strong><code>${e(info.commit)}</code><p class="path-cell">${e(info.path)}</p>${info.modified?'<p class="inline-info">Local edits found. Runs use committed source; import your local YAML in Configuration.</p>':''}${!info.supported?'<p class="inline-info">This commit has no reviewed execution adapter yet. Your existing installation is unchanged. Choose a supported version under Install & update.</p>':''}</div>`:''}</div>
 <div class="local-install-section"><h3>2. Choose the runner</h3><p class="inline-info">An existing runner must match the Python and Snakemake versions declared by this release and pass its import checks. Selecting one never installs or updates packages in it.</p>
 <div class="folder-address">${select('existing-env-choice','Detected Conda environments',[['','Create / reuse a Bactologix runner'],...envs.map(x=>[x.path,x.name+' · Python '+x.python+' · Snakemake '+x.snakemake]),['manual','Browse to another environment']],val('existing-env-choice',''))}${btn('find-envs','Detect environments')}</div>
 ${val('existing-env-choice')?`<div class="folder-address">${field('existing-controller','Environment folder','','text','placeholder="/home/jimi/miniforge3/envs/hybas"')}${btn('browse-folder','Browse',false,'existing-controller','folder')}</div>`:'<p class="inline-info">Bactologix will reuse its compatible runner or prepare a separate one.</p>'}
 ${U.localEnvs?.warning?`<p class="inline-info">${e(U.localEnvs.warning)}</p>`:''}</div>
 <details class="local-install-section guided-details"><summary>Advanced: tool environment folder</summary><p class="inline-info">A cache detected in this checkout is selected automatically. Otherwise Bactologix uses the current pipeline’s environment folder. Override this only to use another location.</p><div class="folder-address">${field('existing-cache','Existing Snakemake Conda cache','','text','placeholder="Use the Bactologix folder"')}${btn('browse-folder','Browse',false,'existing-cache','folder')}</div><p class="inline-info">A typical cache is &lt;pipeline&gt;/.snakemake/conda. Matching environments are reused automatically. Prepare missing tools with Install environments after connecting.</p></details>
 ${notice('Keep your existing work','Connecting copies committed workflow source locally. Import your native YAML and set database paths in Configuration. Existing outputs are available as reports; they are not adopted for in-place resume.')}`;
 foot=btn('connect-local','Connect installation',true,t,'check',!info?.supported||info.path!==val('existing-source'));
}
if(m.kind==='import'){title='Import a sample table';desc='CSV or TSV · sample_id, input_folder, read_mode';body=`<label class="btn upload-label">Choose CSV / TSV<input type="file" id="sample-upload" accept=".csv,.tsv" class="visually-hidden"></label><div class="form-grid">${area('sample-table','Table contents','sample_id,input_folder,read_mode\n','rows="9"')}</div>${btn('sample-template','Download template')}`;foot=btn('import-samples','Import samples',true);}
if(m.kind==='conda'){title='Choose Conda';desc='Use an existing executable or install local Conda from Install & update.';body=field('conda-path','Conda executable',S.doctor.conda||'');foot=btn('save-conda','Save location',true);}

if(m.kind==='clone'){title='Compare polishing strategies';desc='Choose a parent run and create independent variants.';const runs=visibleRuns().filter(r=>r.tool==='hybas');const id=val('clone-parent',m.id||runs[0]?.id);const r=runBy(id);const options=r?.sample.mode==='illumina_only'?[['none','Baseline'],['tripolish','Polypolish']]:[['none','Baseline'],['racon','Racon'],['medaka','Medaka'],['tripolish','Tripolish']];body=`${select('clone-parent','Parent run',runs.map(r=>[r.id,r.label+' · '+r.id]),id)}<label class="check-field"><input type="checkbox" id="clone-aware" ${val('clone-aware',false)?'checked':''}><span>Create an AWARE-free branch<small>Otherwise inherit the parent’s AWARE settings.</small></span></label><div class="strategy-grid">${options.map(([s,l])=>`<label class="strategy-card"><span class="row"><input type="checkbox" id="strategy-${s}" data-strategy="${s}" ${val('strategy-'+s,['none','tripolish'].includes(s))?'checked':''}>${l}</span></label>`).join('')}</div>${notice('Fresh workflows from original inputs','Full parent settings are inherited. Post-AWARE checkpoint reuse and polishing-only execution are not yet implemented.',true)}`;if(!r)body=empty('Create a HybAs AWARE run first.','A saved parent configuration is required.',btn('new-run','Create run draft',true));foot=r?btn('create-clones','Create variant drafts',true):'';}
if(m.kind==='versions'){wide=true;const t=m.id,c=S.catalogs[t];title='Choose '+S.pipelines[t].name+' version';desc='Compatible snapshots bundled with this app. '+(c.live?'Live GitHub check: ':'Bundled GitHub snapshot: ')+c.checkedAt;body=`<div class="toolbar"><input class="search" id="version-search" placeholder="Search versions…" aria-label="Search versions" value="${e(val('version-search'))}">${select('version-channel','Channel',[['all','All'],['pinned','Compatible snapshots'],['release','Releases'],['branch','Branches']],'all')}${btn('refresh-versions','Refresh from GitHub',false,t)}</div><div class="version-list">${c.versions.map(v=>`<label class="source-card version-choice" data-version-kind="${v.kind}" data-version-name="${e((v.ref+' '+v.name).toLowerCase())}"><input type="radio" name="version" value="${e(v.id)}" ${val('version-'+t)===v.id?'checked':''}><span><strong>${e(v.ref)}</strong> ${v.preferred&&v.supported?badge('Recommended','green'):''} ${badge(v.kind,'blue')} ${v.supported?badge('Runner supported','green'):badge('Source only')}<small>${e(v.name)} · ${e(v.commit.slice(0,12))}</small><a href="${e(safeURL(v.url))}" target="_blank" rel="noopener noreferrer">Source / release notes ↗</a>${v.notes?`<details><summary>Release notes</summary><pre class="release-notes">${e(v.notes)}</pre></details>`:''}</span></label>`).join('')}</div>${notice('Keep your existing environments','Compatible runners are checked and reused. Other source versions can be downloaded, but managed runs require a reviewed adapter for that exact commit.')}`;const chosen=c.versions.find(x=>x.id===val('version-'+t));foot=btn('install-version',chosen?.supported?'Download and set up':'Download source only',true,t,'',!chosen);}

if(m.kind==='followup'){wide=true;title='Review saved screening for follow-up';desc=m.id;body=`${notice('Separate follow-up','Review the screening report and settings first. Follow-up uses saved screening evidence and writes into its follow-up folder. A later attempt replaces those follow-up outputs; screening outputs remain separate.',true)}${btn('view-results','Open screening results',false,m.id)}${area('followup-yaml-'+m.id,'Reviewed follow-up configuration','deep:\n  enabled: false\n  maximum_candidates: 5\nresource_receipts: []\n','rows="12" spellcheck="false"')}<label class="check-field"><input type="checkbox" id="followup-reviewed" ${val('followup-reviewed',false)?'checked':''}><span>I reviewed the screening report and this follow-up request.</span></label><p>Set enabled to true only when requesting follow-up. Resource paths must be absolute and already prepared. The native dry-run checks saved evidence and resource prerequisites.</p>`;foot=btn('create-followup','Create follow-up draft',true,m.id);}
if(m.kind==='log'){wide=true;title='Job log';desc=m.id;const l=U.log?.id===m.id?U.log:null;const j=S.jobs.find(j=>j.id===m.id);body=`${l?statusBadge(l.status):''}${j?jobTelemetry(j):''}${j&&!j.telemetry?'<p class="inline-info">No resource record has been collected for this job. Recording starts when a new job runs in v0.1.0a27 or later.</p>':''}${l?.error?`<div class="error">${e(l.error)}</div>`:''}<pre class="code-block log-output">${e(l?.text||'Loading process output…')}</pre>`;foot=(j?usageButton(j):'')+btn('job-log','Refresh log',false,m.id)+btn('download-log','Download full log',false,m.id);}
if(m.kind==='usage'){wide=true;title='Resource use & progress';desc=m.id;const u=U.usage?.id===m.id?U.usage:null;body=u?usageDetail(u):'<p>Loading recorded measurements…</p>';foot=btn('refresh-usage','Refresh',false,m.id)+btn('usage-download-csv','CSV',false,m.id,'download',!u)+btn('usage-download-json','JSON',false,m.id,'download',!u)+btn('usage-download-zip','Download record (ZIP)',true,m.id,'download',!u);}
if(m.kind==='register-report'){title='Add an existing HTML report';desc='Choose a local file. Its association is user-selected.';body=`<div class="form-grid">${field('report-name','Report label','Existing report')}${field('report-path','HTML report path','','text','placeholder="/home/jimi/results/safety/report.html"')}</div>`;foot=btn('save-report','Register report',true);}
if(m.kind==='report'){wide=true;const r=U.report?.id===m.id?U.report:null;title=r?.name||'HTML report';desc=r?.path||'Loading report…';body=r?'<div id="report-frame-container"></div><p class="inline-info">Self-contained HTML works best. Open the original with its companion files for relative assets.</p>':'<p>Loading the original HTML report…</p>';foot=btn('open-original','Open original in browser',false,m.id)+btn('download-report','Download original HTML',true,m.id,'download',!r);}
if(m.kind==='credits'){title='Built by Jimi Goldstein';desc='Bactologix · Human Link';body=`<div class="author-card"><span class="brand-mark">b<span>•</span></span><div><h3>Your workflow, connected.</h3><p>HybAs AWARE + BaSIS AWARE in one local workspace.</p></div></div><p class="credits-copy">Created by <strong>Jimi Goldstein</strong> · Human Link<br><a href="https://orcid.org/0000-0002-7829-0090" target="_blank" rel="noopener noreferrer">ORCID 0000-0002-7829-0090 ↗</a></p><pre class="code-block">${e(S.citations['CITATION.txt'])}</pre><div class="button-row">${['ris','bib','cff','txt'].map(x=>btn('citation','Download '+x.toUpperCase(),false,'CITATION.'+x,'download')).join('')}</div><p class="credits-copy">Cite the pipelines, individual tools and databases used in your analysis as well as Bactologix.</p><div class="button-row">${btn('citation','HybAs AWARE citation',false,'CITATION.HybAs.cff')}${btn('citation','BaSIS AWARE citation',false,'CITATION.BaSIS.cff')}</div><p class="credits-copy"><a href="https://github.com/crackberryq/Bactologix" target="_blank" rel="noopener noreferrer">Bactologix on GitHub ↗</a> · MIT license · v${e(S.version)}</p>`;}
$('#modal-root').innerHTML=`<div class="modal-shade"><section class="modal ${wide?'':'narrow'} ${m.kind==='report'?'report-modal':['new-run','edit-run'].includes(m.kind)?'run-draft-modal':m.kind==='run'?'saved-run-modal':''}" role="dialog" aria-modal="true" aria-labelledby="modal-title"><div class="modal-head"><div><h2 id="modal-title">${e(title)}</h2><p>${e(desc)}</p></div><button class="icon-btn" data-action="close" aria-label="Close dialog">${icon('close')}</button></div><div class="modal-body">${body}<div id="action-error" class="error" role="alert">${e(U.error||'')}</div></div><div class="modal-foot"><small>${['new-run','edit-run'].includes(m.kind)?'Draft not saved yet':m.kind==='run'?'Saved run · changes create a revision':m.kind==='download-database'?'Starts only when you confirm':m.kind==='apply-databases'?'Creates a new draft · original kept':'Local workspace'}</small><div class="button-row">${btn('close','Close')}${foot}</div></div></section></div>`;
$('.app-shell').setAttribute('inert','');
if(m.kind==='report'&&U.report?.id===m.id){const f=document.createElement('iframe');f.title=U.report.name;f.setAttribute('sandbox','allow-scripts allow-downloads');f.referrerPolicy='no-referrer';f.className='html-report-frame';f.srcdoc=new TextDecoder().decode(Uint8Array.from(atob(U.report.base64),c=>c.charCodeAt(0)));$('#report-frame-container').append(f);}
if(m.kind==='versions')filterVersions();
if(['new-run','edit-run'].includes(m.kind)&&U.runConfigTab==='settings')filterRunSettings();
if(m.kind==='environments')syncEnvironmentControls();
renderBusy();
}
function syncEnvironmentControls(){
 const m=U.modal;if(m?.kind!=='environments')return;
 const panel=S.environments[m.id],count=$$('[data-environment]:checked').length,changed=val('environment-cache-'+m.id,panel.cache)!==panel.cache;
 $('#environment-selection-count').textContent=count+' of '+panel.rows.length+' selected';
 $('#environment-cache-note').textContent=changed?'Save the folder change before installing or checking environments.':'Folder changes affect this installation and future drafts. Use a path without spaces or shell characters.';
 for(const b of $$('[data-action="check-environments"],[data-action="install-environments"],[data-action="repair-environments"]'))b.disabled=panel.busy||!!U.pending||!count||changed;
 $('[data-action="save-env-cache"]').disabled=!changed||!!U.pending||S.jobs.some(j=>['queued','running'].includes(j.status));
}
function safeURL(url){try{const u=new URL(url);return u.protocol==='https:'&&u.hostname==='github.com'?u.href:'#';}catch{return '#';}}
function filterVersions(){const q=val('version-search').toLowerCase(),c=val('version-channel','all');for(const row of $$('.version-choice')){row.hidden=!row.dataset.versionName.includes(q)||(c!=='all'&&row.dataset.versionKind!==c);const radio=row.querySelector('input');if(row.hidden&&radio.checked){radio.checked=false;U.drafts['version-'+U.modal.id]='';}}const chosen=S.catalogs[U.modal.id].versions.find(x=>x.id===val('version-'+U.modal.id));const button=$('[data-action="install-version"]');if(button){button.disabled=!chosen;button.textContent=!chosen?'Choose a version':chosen.supported?'Download and set up':'Download source only';}}

async function change(event){const t=event.target;if(t.id&&t.type!=='file'){U.drafts[t.id]=t.type==='checkbox'?t.checked:t.value;}
if(t.id==='setup-pipeline'){U.setupTool=t.value;setDraft('hardware-target',t.value);render();}
if(t.dataset.sample&&wizardKind()==='new-run')render();
if(t.dataset.runField){const c=runEditor(),f=c?.fields.find(f=>f.key===t.dataset.runField);if(f){editRunField(c,f,t.type==='checkbox'?t.checked:t.value);if(t.type==='checkbox'||t.tagName==='SELECT')renderEditor();}}
if(t.id==='run-tool'){draftInputs();U.runBudgetTool=t.value;const budget=draftBudget(t.value);setDraft('run-cores',budget.cores);setDraft('run-memory',budget.memory_mb);const c=runEditor();if(c){if(c.raw){U.runSettingTab[c.tool]='advanced';U.runConfigTab='yaml';render();}else if(U.runConfigTab==='yaml'&&Object.keys(c.changes).length)request('run_config_editor',{...runConfigPayload(),view:'yaml'});else render();}else request('run_config_editor',{tool:t.value,view:U.runConfigTab||'samples'});}
if(t.dataset.applyRun||t.dataset.applyResource)renderModal();
if(t.dataset.environment)syncEnvironmentControls();
if(t.id==='run-filter-tool'||t.id==='run-filter-status')render();
if(t.dataset.select){U.selected=t.checked?[...new Set([...U.selected,t.dataset.select])]:U.selected.filter(x=>x!==t.dataset.select);render();}
if(t.id?.startsWith('dbcand-')){const tool=U.resourceTool||'hybas',key=t.id.slice(('dbcand-'+tool+'-').length);if(t.value){setDraft(dbPath(tool,key),t.value);delete dbState(tool).resets[key];render();}}
if(t.id==='existing-env-choice'){setDraft('existing-controller',t.value==='manual'?'':t.value);renderModal();}
if(t.id==='clone-parent'){delete U.drafts['strategy-medaka'];delete U.drafts['strategy-racon'];renderModal();}
if(t.name==='version'){U.drafts['version-'+U.modal.id]=t.value;renderModal();}
if(t.name==='app-version-choice'){U.drafts['app-release']=t.value;renderModal();}
if(t.id==='yaml-upload'||t.id==='sample-upload'){
 const f=t.files?.[0];if(!f)return;if(f.size>2*1024*1024){toast('Choose a text file smaller than 2 MB.',true);return;}
 const c=t.id==='yaml-upload'?defaultEditor():null,text=await f.text();
 if(c){c.text=text;c.changes={};c.raw=true;const ui=editorUI(c);ui.runConfigTab='yaml';ui.runSettingTab[c.tool]='advanced';setDraft('config-yaml-'+c.tool,text);render();}
 else {setDraft('sample-table',text);}
}
}
function onChange(event){const result=change(event);persistWizard();return result;}
function onInput(event){input(event);persistWizard();}
function input(event){const t=event.target;
if(['run-cores','revision-cores','run-memory','revision-memory'].includes(t.id)){const kind=t.id.endsWith('cores')?'cores':'memory',label=$('[data-review-budget="'+kind+'"]');if(label)label.textContent=t.value+(kind==='cores'?' cores':' MB');}
if(t.dataset.runField){const c=runEditor(),f=c?.fields.find(f=>f.key===t.dataset.runField);if(f)editRunField(c,f,t.type==='checkbox'?t.checked:t.value);}
if(t.dataset.runYaml){const c=runEditor();c.text=t.value;c.changes={};c.raw=true;updateDefaultStatus();}
if(t.id&&t.type!=='file')U.drafts[t.id]=t.type==='checkbox'?t.checked:t.value;if(t.id?.startsWith('environment-cache-'))syncEnvironmentControls();if(t.id?.startsWith('dbpath-')){const tool=U.resourceTool||'hybas',key=t.id.slice(('dbpath-'+tool+'-').length);delete dbState(tool).resets[key];setDraft('dbcand-'+tool+'-'+key,'');for(const b of $$('[data-action="save-databases"]'))b.disabled=false;const pos=t.selectionStart,id=t.id;render();const field=$('#'+id);field?.focus();if(pos!==null)field?.setSelectionRange(pos,pos);}if(t.id===editorSearchId(runEditor()||{}))filterRunSettings();if(t.id==='version-search'||t.id==='version-channel')filterVersions();if(['registered-sample-search','run-search'].includes(t.id)){const pos=t.selectionStart,id=t.id;render();$('#'+id).focus();$('#'+id).setSelectionRange(pos,pos);}}
function click(event){const nav=event.target.closest('[data-nav]');if(nav){if(event.ctrlKey||event.metaKey||event.shiftKey||event.altKey)return;event.preventDefault();navigate(nav.dataset.nav);return;}const a=event.target.closest('[data-action]');if(!a)return;if(a.tagName==='A'){if(event.ctrlKey||event.metaKey||event.shiftKey||event.altKey)return;event.preventDefault();}const action=a.dataset.action,id=a.dataset.id;draftInputs();U.error='';
if(action==='close'){closeModal();return;}if(action.startsWith('go-')){navigate(action.slice(3));return;}
const v=x=>$('#'+x)?.value??val(x);
if(action==='resume-wizard')openWizard();
else if(action==='leave-wizard'){persistWizard();navigate(U.wizard?.kind==='edit-run'?'runs':'samples');}
else if(action==='discard-wizard')modal('discard-wizard');
else if(action==='confirm-discard-wizard'){clearWizard();U.modal=null;navigate('runs');}
else if(action==='browse-folder')browse(id,v(id));
else if(action==='folder-open'){setDraft('folder-query','');request('browse_folders',{path:id,show_hidden:!!val('folder-hidden',false)});}
else if(action==='folder-go'||action==='folder-filter'||action==='folder-page'){request('browse_folders',{path:v('folder-path'),query:v('folder-query'),offset:action==='folder-page'?Number(id):0,show_hidden:!!val('folder-hidden',false)});}
else if(action==='folder-select'&&U.browser){
 const target=U.modal.id,b=U.browser;setDraft(target,b.path);
 if(target==='sample-path'){
  if(!val('sample-id'))setDraft('sample-id',b.name.replace(/[^A-Za-z0-9_.-]/g,'-').replace(/^[^A-Za-z0-9]+/,'').slice(0,100));
  if(b.layout['raw/illumina']||b.layout['raw/nanopore'])setDraft('sample-mode',b.layout['raw/illumina']?(b.layout['raw/nanopore']?'hybrid':'illumina_only'):'ont_only');
 }
 if(target==='existing-controller')setDraft('existing-env-choice','manual');
 if(target==='existing-source')U.localInstall=null;
 if(target.startsWith('dbpath-')){const tool=U.resourceTool||'hybas';delete dbState(tool).resets[target.slice(('dbpath-'+tool+'-').length)];}
 closeModal();
}
else if(action==='existing-install'){U.localInstall=null;U.discovery=null;setDraft('existing-source','');setDraft('existing-cache','');modal('existing',id);request('discover_environments');}
else if(action==='find-envs')request('discover_environments');
else if(action==='scan-local')request('discover_installations',{tool:id,root:v('discovery-root')});
else if(action==='choose-local'){setDraft('existing-source',id);U.localInstall=null;request('inspect_installation',{tool:U.modal.id,path:id});}
else if(action==='inspect-local'){U.localInstall=null;request('inspect_installation',{tool:id,path:v('existing-source')});}
else if(action==='connect-local'){if(!U.localInstall?.supported)return;request('connect_installation',{tool:id,path:v('existing-source'),commit:U.localInstall.commit,runner_mode:v('existing-env-choice')?'existing':'managed',controller:v('existing-env-choice')?v('existing-controller'):'',rule_cache:v('existing-cache')});}
else if(action==='app-updates'){modal('app-update');if(!S.app_update.catalog)request('refresh_app_releases');}
else if(action==='refresh-app-releases')request('refresh_app_releases');
else if(action==='install-app-release'){const r=S.app_update.catalog?.releases.find(x=>String(x.id)===val('app-release'));if(r?.available)request('update_app',{release_id:r.id,version:r.version});}
else if(action==='new-sample')modal('sample');
else if(action==='import')modal('import');
else if(action==='conda')modal('conda');
else if(action==='new-run'||action==='sample-draft'){
 if(U.wizard){openWizard();toast('Resumed your unfinished edits. Discard them explicitly to start again.');return;}
 if(U.view==='install'&&(U.setupTab||'guide')==='guide')setDraft('run-tool',U.setupTool||'hybas');
 if(action==='sample-draft'){for(const sample of S.samples)setDraft('run-sample-'+sample.id,sample.id===id);}
 U.wizard={kind:'new-run'};U.runConfigs={};U.runConfigGroup={};U.runSettingTab={};U.runConfigTab='samples';setDraft('run-setting-search','');U.runBudgetTool=val('run-tool','hybas');const budget=draftBudget(U.runBudgetTool);setDraft('run-cores',budget.cores);setDraft('run-memory',budget.memory_mb);
 openWizard();
}
else if(action==='run-config-tab'){
 const c=runEditor();if(!c)return;
 if(id==='yaml'||id==='review'||c.raw)requestEditor(id,id==='review');
 else {editorUI(c).runConfigTab=id;renderEditor();}
}
else if(action==='guided-run-tab'){
 const c=runEditor();if(!c)return;const ui=editorUI(c);ui.runSettingTab[c.tool]=id;
 if(ui.runConfigTab==='yaml')requestEditor('settings');else renderEditor();
}
else if(action==='run-choice'){
 const c=runEditor(),f=c?.fields.find(f=>f.key===id);if(f){editRunField(c,f,a.dataset.value);renderEditor();}
}
else if(action==='run-setting-group'){const c=runEditor();editorUI(c).runConfigGroup[c.tool]=id;setDraft(editorSearchId(c),'');filterRunSettings();}
else if(action==='reload-run-defaults'){
 if(runEditor()?.scope==='defaults')request('default_config_editor',{tool:U.tool,view:editorUI().runConfigTab});
 else if(wizardKind()==='edit-run')request('edit_run_settings',{id:U.revision.id});
 else request('run_config_editor',{tool:val('run-tool','hybas'),view:U.runConfigTab});
}
else if(action==='check-run-config')requestEditor(editorUI().runConfigTab,true);
else if(action==='reset-run-field'){
 const c=runEditor(),f=c.fields.find(f=>f.key===id);delete c.changes[id];
 if(c.tool==='hybas'&&['run_kraken','kraken_decision_mode'].includes(f?.name))for(const x of c.fields.filter(x=>['run_kraken','kraken_decision_mode'].includes(x.name)))delete c.changes[x.key];
 renderEditor();
}
else if(['cite','about'].includes(action))modal('credits');
else if(action==='citation')download('Bactologix-'+id,S.citations[id]);
else if(action==='export-workspace')download('Bactologix-workspace.json',JSON.stringify({exported:new Date().toISOString(),...S},null,2),'application/json');
else if(action==='save-sample')request('add_sample',{id:v('sample-id'),path:v('sample-path'),mode:v('sample-mode')});
else if(action==='sample-template')download('Bactologix-samples.csv','sample_id,input_folder,read_mode\nsample01,/home/jimi/bactomics/sample01,hybrid\n','text/csv');
else if(action==='run-list'){U.runList=id;render();}
else if(action==='sample-runs'){U.runSampleFilter=id;U.runList='current';setDraft('run-search','');setDraft('run-filter-tool','all');setDraft('run-filter-status','all');navigate('runs');}
else if(action==='clear-sample-filter'){U.runSampleFilter=null;render();}
else if(action==='clear-run-filters'){U.runSampleFilter=null;setDraft('run-search','');setDraft('run-filter-tool','all');setDraft('run-filter-status','all');render();}
else if(action==='comparison-tab'){U.comparisonTab=id;writeRoute(true);render();}
else if(action==='compare-selected'){U.comparisonTab='review';navigate('comparisons');}
else if(action==='archive-run'||action==='restore-run')request(action==='archive-run'?'archive_runs':'restore_runs',{ids:[id]});
else if(action==='archive-selected')request('archive_runs',{ids:U.selected});
else if(action==='remove-sample')request('remove_sample',{id,sample:S.samples.find(s=>s.id===id)});
else if(action==='import-samples')request('import_samples',{text:v('sample-table')});
else if(action==='save-conda')request('save_conda',{path:v('conda-path')});
else if(['bootstrap','smoke','refresh'].includes(action))request(action);
else if(action==='recover-jobs')request('recover_jobs');
else if(action==='versions'){U.drafts['version-search']='';U.drafts['version-channel']='all';U.drafts['version-'+id]=S.catalogs[id].versions.find(x=>x.preferred&&x.supported)?.id||'';modal('versions',id);}
else if(action==='refresh-versions')request('refresh_versions',{tool:id});
else if(action==='install-version'){const ver=S.catalogs[id].versions.find(x=>x.id===val('version-'+id));if(!ver){toast('Choose a version first.',true);return;}request('install',{tool:id,version_id:ver.id,commit:ver.commit});}
else if(['check-install','repair-install','use-install','setup-install'].includes(action))request(action.replace('-','_'),{id});
else if(action==='use-run-databases'||action==='use-databases-for-runs'){
 for(const key of Object.keys(U.drafts))if(key.startsWith('dbapply-'))delete U.drafts[key];
 U.databaseRunPlan=null;const run=action==='use-run-databases'?runBy(id):null;
 if(run)setDraft('dbapply-run-'+id,true);
 modal('apply-databases',run?run.tool:id);
 if(run)request('preview_run_databases',{ids:[id]});
}
else if(action==='review-run-databases')request('preview_run_databases',{ids:$$('[data-apply-run]:checked').map(x=>x.dataset.applyRun)});
else if(action==='change-database-runs'){U.databaseRunPlan=null;renderModal();}
else if(action==='apply-run-databases'){
 const selections={};for(const x of $$('[data-apply-resource]:checked:not(:disabled)'))(selections[x.dataset.applyRunId]||=[]).push(x.dataset.applyResource);
 request('apply_run_databases',{ids:U.databaseRunPlan.runs.map(r=>r.id),selections,signature:U.databaseRunPlan.signature});
}
else if(['setup-install-environments','setup-check-environments','setup-repair-environments'].includes(action)){setDraft('environment-cache-'+id,S.environments[id]?.cache||'');modal('environments',id,{action:action.split('-')[1]});}
else if(action==='setup-tab'){U.setupTab=id;render();}
else if(action==='setup-step'){U.setupStep=id;U.setupTab='guide';render();$('.setup-guide')?.scrollIntoView({block:'start'});}
else if(action==='detect-hardware')request('detect_hardware');
else if(action==='apply-power-preset')request('apply_resource_preset',{preset:id,tools:v('hardware-target')==='both'?['hybas','basis']:[v('hardware-target')],revisions:S.hardware.revisions,hardware_id:S.hardware.id});
else if(action==='select-environments'||action==='clear-environments'){for(const el of $$('[data-environment]')){el.checked=action==='select-environments';U.drafts[el.id]=el.checked;}syncEnvironmentControls();}
else if(action==='preview-uninstall'){U.uninstallPlan=null;modal('uninstall',id);request('preview_uninstall',{id});}
else if(action==='uninstall-version')request('uninstall_version',{id,signature:U.uninstallPlan.signature});
else if(action==='browse-env-cache'){U.modal.advanced=true;browse('environment-cache-'+id,v('environment-cache-'+id),true);}
else if(action==='save-env-cache')request('set_environment_cache',{id,path:v('environment-cache-'+id)});
else if(['check-environments','install-environments','repair-environments'].includes(action))request('manage_environments',{id,action:action.split('-')[0],selected:$$('[data-environment]:checked').map(x=>x.dataset.environment)});
else if(action==='download-database')modal('download-database',id);
else if(action==='start-database-download')request('download_database',{tool:U.resourceTool||'hybas',id,parent:v('download-parent'),cores:Number(v('download-cores')),memory_mb:Number(v('download-memory')),lineage:v('download-lineage')||'bacteria_odb10'});
else if(action==='use-downloaded')request('use_downloaded_database',{id});
else if(action==='resume-download')request('resume_database_download',{id});
else if(action==='config-tool'){U.tool=id;render();}
else if(action==='database-tool'){U.resourceTool=id;render();}
else if(action==='reload-databases')request('load_databases',{tool:id});
else if(action==='scan-databases')request('scan_databases',{tool:id,root:v('dbscan-'+id)});
else if(action==='browse-database'){const t=U.resourceTool||'hybas',key=dbPath(t,id),state=dbState(t),value=v(key),c=state.checks[id]||state.scan?.candidates[id]?.find(x=>x.path===value);browse(key,c?.folder||(value.match(/\.(json|dmnd|fas|fasta|faa)$/i)?value.replace(/[\\/][^\\/]+$/,''):value),true);}
else if(action==='check-database'){const t=U.resourceTool||'hybas';request('check_database',{tool:t,id,path:v(dbPath(t,id))});}
else if(action==='reset-database'){const t=U.resourceTool||'hybas';dbState(t).resets[id]=true;setDraft(dbPath(t,id),'');setDraft('dbcand-'+t+'-'+id,'');render();}
else if(action==='save-databases'){const state=dbState(id),changes={};for(const r of S.databases[id].rows){if(r.managed)continue;const path=v(dbPath(id,r.id));if(state.resets[r.id])changes[r.id]=null;else if(path!==r.path)changes[r.id]=path;}request('save_databases',{tool:id,changes,revision:state.revision});}
else if(action==='save-config')request('save_default_config',{...runConfigPayload(),revision:runEditor().revision,view:editorUI().runConfigTab});
else if(action==='download-yaml')requestEditor(editorUI().runConfigTab,false,{download:true});
else if(action==='create-runs')request('create_runs',{tool:v('run-tool'),ids:S.samples.filter(s=>val('run-sample-'+s.id,false)).map(s=>s.id),cores:Number(v('run-cores')),memory_mb:Number(v('run-memory')),config:runConfigPayload()});
else if(action==='clear-selection'){U.selected=[];render();}
else if(action==='run-detail')openRun(id);
else if(action==='check-run-environments')request('workflow',{ids:[id],action:'check-environments'});
else if(['dry-run','prepare','run'].includes(action))request('workflow',{ids:[id],action});
else if(action.startsWith('batch-'))request('workflow',{ids:U.selected,action:action.slice(6)});
else if(action==='polish'||action==='noaware'){if(id)U.drafts['clone-parent']=id;U.drafts['clone-aware']=action==='noaware';modal('clone',id);}
else if(action==='create-clones')request('clone',{id:v('clone-parent'),aware_free:$('#clone-aware').checked,strategies:$$('[data-strategy]:checked').map(x=>x.dataset.strategy)});
else if(action==='saved-run-tab'){U.runDetailTab=id;writeRoute(true);render();if(id==='methods')request('inspect_methods',{id:U.runId});}
else if(action==='open-methods'){U.runDetailTab='methods';writeRoute(true);render();request('inspect_methods',{id});}
else if(action==='refresh-methods')request('inspect_methods',{id});
else if(action.startsWith('methods-download-'))request('download_methods',{id,format:action.slice('methods-download-'.length)});
else if(action==='edit-run-settings'){if(U.wizard){openWizard();toast('Resume or discard your unfinished edits before editing another run.');}else request('edit_run_settings',{id});}
else if(action==='save-run-revision')request('revise_run',{id:U.revision.id,signature:U.revision.signature,config:runConfigPayload(),cores:Number(v('revision-cores')),memory_mb:Number(v('revision-memory'))});
else if(action==='handoff')request('handoff',{id});
else if(action==='followup'){delete U.drafts['followup-reviewed'];modal('followup',id);}
else if(action==='create-followup')request('create_followup',{id,text:v('followup-yaml-'+id),reviewed:$('#followup-reviewed').checked});
else if(action==='download-frozen'&&U.detail?.id===id)download(id+'.yaml',U.detail.config,'text/yaml');
else if(action==='job-log'){modal('log',id);request('job_log',{id});}
else if(action==='job-usage'){modal('usage',id);request('job_metrics',{id});}
else if(action==='refresh-usage')request('job_metrics',{id});
else if(action.startsWith('usage-download-'))request('download_metrics',{id,format:action.slice('usage-download-'.length)});
else if(action==='download-log')request('download_log',{id});
else if(action==='cancel-job')request('cancel_job',{id});
else if(action==='view-results')openRun(id,'results');
else if(action==='all-reports'){U.reportRun=null;render();}
else if(action==='register-report')modal('register-report');
else if(action==='save-report')request('register_report',{path:v('report-path'),name:v('report-name')});
else if(action==='open-report'){U.report=null;modal('report',id);request('open_report',{id});}
else if(action==='open-original')request('open_original',{id});
else if(action==='download-report'&&U.report){const r=U.report;download(r.path.split('/').pop(),Uint8Array.from(atob(r.base64),c=>c.charCodeAt(0)),'text/html');}
}
function keydown(event){if(event.key==='Escape'&&U.modal){event.preventDefault();closeModal();}if(event.key==='Tab'&&U.modal){const nodes=$$('.modal button:not(:disabled),.modal input:not(:disabled),.modal select,.modal textarea,.modal a[href],.modal summary').filter(n=>!n.hidden);const first=nodes[0],last=nodes[nodes.length-1],focused=parentElement.activeElement;if(event.shiftKey&&focused===first){last?.focus();event.preventDefault();}else if(!event.shiftKey&&focused===last){first?.focus();event.preventDefault();}}}
// This watcher survives component disposal and Streamlit reconnects during an update.
function watchAppRestart(){
 const u=S.app_update;if(!u)return;
 let watcher=window.__bactologixRestart;
 const fail=watcher&&(S.jobs.find(j=>j.id===watcher.job)?.status==='failed'||S.jobs.find(j=>j.id===watcher.job)?.status==='cancelled'||(u.last_update?.job===watcher.job&&u.last_update?.restart_status==='failed'));
 if(fail){clearTimeout(watcher.timer);watcher.cancelled=true;window.__bactologixRestart=null;return;}
 const job=u.active_job&&S.jobs.find(j=>j.id===u.active_job);
 const target=job?.payload?.release?.version||(u.restart_required&&u.last_update?.status==='installed'?u.last_update.version:null);
 if(watcher||!u.automatic_restart||!u.launcher||!target)return;
 watcher={id:u.launcher.id,version:target,job:job?.id||u.last_update.job,deadline:Date.now()+1200000,cancelled:false};
 window.__bactologixRestart=watcher;
 const check=async()=>{
  if(watcher.cancelled)return;
  if(Date.now()>watcher.deadline){window.__bactologixRestart=null;const msg=document.createElement('div');msg.textContent='Automatic restart did not finish. Check the terminal, start bactologix if needed, then refresh this page.';msg.setAttribute('role','alert');msg.style.cssText='position:fixed;bottom:20px;left:20px;right:20px;padding:18px;background:#fff3d6;color:#543c13;z-index:99999;border:1px solid #d9bd76;border-radius:8px';document.body.append(msg);return;}
  const abort=new AbortController(),timer=setTimeout(()=>abort.abort(),2500);
  try{
   const runtime=await fetch('/app/static/runtime.json',{cache:'no-store',signal:abort.signal});
   if(runtime.ok){const fresh=await runtime.json();if(fresh.id&&fresh.id!==watcher.id&&fresh.version===watcher.version){
    const health=await fetch('/_stcore/health',{cache:'no-store',signal:abort.signal});
    if(health.ok&&(await health.text()).trim()==='ok'&&!watcher.cancelled){window.__bactologixRestart=null;window.location.reload();return;}
   }}
  }catch(_){/* The server is expected to be briefly unavailable during restart. */}
  finally{clearTimeout(timer);}
  if(!watcher.cancelled)watcher.timer=setTimeout(check,750);
 };
 watcher.timer=setTimeout(check,750);
}
watchAppRestart();
if(!U.routeInitialized){U.routeInitialized=true;readRoute();}
const response=data.response;
const newResponse=response&&response.id!==U.handled;
const backgroundResponse=U.background;
// Preserve drafts before rendering a new server snapshot.
draftInputs();
if(newResponse){U.handled=response.id;U.pending=null;U.error=response.ok?'':response.message;if(response.config){const c=response.config;U.drafts['yaml-'+c.tool]=c.text;U.fields[c.tool]=c.fields;const el=$('#yaml-'+c.tool);if(el)el.value=c.text;}if(response.browser){U.browser=response.browser;setDraft('folder-path',response.browser.path);}if(response.database_scan){const scan=response.database_scan,t=scan.tool,state=dbState(t);state.scan=scan;for(const r of S.databases[t].rows){const key=dbPath(t,r.id),preferred=scan.candidates[r.id]?.find(c=>c.preferred);if(preferred&&!state.resets[r.id]&&val(key,r.path)===r.path){setDraft(key,preferred.path);setDraft('dbcand-'+t+'-'+r.id,preferred.path);}}}
if(response.removed_sample){
 const key='run-sample-'+response.removed_sample;delete U.drafts[key];
 // render() captures the outgoing form once more before replacing the dialog.
 const checkbox=$$('[data-sample]').find(el=>el.dataset.sample===response.removed_sample);if(checkbox)checkbox.checked=false;
}
if(response.edit_run){
 U.revision=response.edit_run;U.wizard={kind:'edit-run',id:U.revision.id};U.view='draft';U.modal=null;writeRoute();U.runConfigGroup={};U.runSettingTab={};
 U.runSettingTab[U.revision.tool]=guidedTabs[U.revision.tool].some(t=>t[0]===U.runDetailTab)?U.runDetailTab:guidedTabs[U.revision.tool][0][0];
 setDraft('revision-cores',U.revision.cores);setDraft('revision-memory',U.revision.memory_mb);setDraft('run-setting-search','');
}
if(response.run_config){
 const c=response.run_config;U.runConfigs ||= {};if(wizardKind()==='edit-run')U.revisionConfig={...c,changes:{},raw:false};else U.runConfigs[c.tool]={...c,changes:{},raw:false};
 U.runConfigTab=response.run_config_view;if(U.runConfigTab==='yaml'){U.runSettingTab ||= {};U.runSettingTab[c.tool]='advanced';}setDraft('run-yaml-'+c.tool,c.text);
}
if(response.default_config){
 const c=response.default_config,old=defaultEditor(c.tool);U.defaultEditors ||= {};
 U.defaultEditors[c.tool]={...c,scope:'defaults',changes:{},raw:false,savedText:response.default_config_loaded||response.default_config_saved?c.text:old?.savedText??c.text};
 const ui=editorUI(U.defaultEditors[c.tool]);ui.runConfigTab=response.default_config_view;
 if(ui.runConfigTab==='yaml')ui.runSettingTab[c.tool]='advanced';
 setDraft('config-yaml-'+c.tool,c.text);
}
if(response.uninstall_plan)U.uninstallPlan=response.uninstall_plan;
if(response.open_environments){const id=response.open_environments;setDraft('environment-cache-'+id,S.environments[id]?.cache||'');U.modal={kind:'environments',id,action:U.modal?.action||'install',advanced:true};}
if(response.database_run_plan)U.databaseRunPlan=response.database_run_plan;
if(response.database_check){const c=response.database_check,t=c.tool;dbState(t).checks[c.id]=c;setDraft(dbPath(t,c.id),c.path);delete dbState(t).resets[c.id];}
if(response.database_saved){clearDatabases(response.database_saved);}
if(response.discovery)U.discovery=response.discovery;if(response.local_envs)U.localEnvs=response.local_envs;if(response.local_install){U.localInstall=response.local_install;setDraft('existing-source',response.local_install.path);setDraft('existing-cache',response.local_install.rule_cache);}if(response.detail)U.detail=response.detail;if(response.methods)U.methods=response.methods;if(response.log)U.log=response.log;if(response.usage)U.usage=response.usage;if(response.report)U.report=response.report;if(response.download)download(response.download.name,response.download.base64?Uint8Array.from(atob(response.download.base64),c=>c.charCodeAt(0)):response.download.text,response.download.type);if(response.ok&&['create_runs','revise_run'].includes(U.requestOrigin?.kind))clearWizard();if(response.ok&&!response.keep_modal&&!backgroundResponse)U.modal=null;if(response.navigate){if(response.navigate==='jobs'&&U.requestOrigin?.kind==='workflow'&&U.requestOrigin.view==='run'&&U.view==='run'&&U.requestOrigin.runId===U.runId){U.runDetailTab='activity';writeRoute(true);}else{U.view=response.navigate;writeRoute();}}if(response.open_run){U.runDetailTab='overview';U.revision=null;U.revisionConfig=null;U.modal=null;U.view='run';U.runId=response.open_run;writeRoute(true);}}
window.addEventListener('beforeunload',beforeUnload);window.addEventListener('popstate',routeChanged);window.addEventListener('hashchange',routeChanged);
root.addEventListener('click',click);root.addEventListener('input',onInput);root.addEventListener('change',onChange);root.addEventListener('keydown',keydown);
render();
if(newResponse&&response.message)toast(response.message,!response.ok);else renderToast();
if(newResponse&&U.queuedRequest){const queued=U.queuedRequest;U.queuedRequest=null;request(queued.kind,queued.payload);}
const dispose=()=>{window.removeEventListener('beforeunload',beforeUnload);window.removeEventListener('popstate',routeChanged);window.removeEventListener('hashchange',routeChanged);clearTimeout(pollTimer);clearTimeout(toastTimer);root.removeEventListener('click',click);root.removeEventListener('input',onInput);root.removeEventListener('change',onChange);root.removeEventListener('keydown',keydown);};
root._dispose=dispose;
return dispose;
}
