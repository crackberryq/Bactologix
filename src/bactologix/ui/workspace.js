export default function(component) {
"use strict";
const {parentElement, data, setTriggerValue} = component;
const root = parentElement.querySelector(".bacto-root");
root._dispose?.();
const icons={home:'M3 10 12 3l9 7M5 9v12h14V9M9 21v-7h6v7',samples:'M5 3h14v18H5zM9 8h6M9 12h6M9 16h4',install:'M12 3v12m-5-5 5 5 5-5M4 16v5h16v-5',resources:'M4 6c0-4 16-4 16 0s-16 4-16 0m0 0v12c0 4 16 4 16 0V6M4 12c0 4 16 4 16 0',config:'M4 7h16M4 17h16M8 4v6m8 4v6',jobs:'M8 5h12v16H4V5h4m0-2h8v4H8zM8 12h8m-8 4h5',compare:'M5 20V10m7 10V4m7 16v-7M3 21h18',plus:'M12 5v14M5 12h14',arrow:'M5 12h14m-5-5 5 5-5 5',clone:'M8 8h12v12H8zM4 16H3V3h13v1',check:'m5 12 4 4 10-10',clock:'M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18m0 4v5l3 2',close:'m6 6 12 12M6 18 18 6',external:'M14 3h7v7m0-7-11 11M10 5H4v15h15v-6',play:'m8 4 12 8-12 8z',stop:'M6 6h12v12H6z',info:'M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18m0 7v6m0-9v.01',chevron:'m9 5 7 7-7 7',folder:'M3 7h7l2-3h9v16H3z',gear:'M12 8a4 4 0 1 0 0 8 4 4 0 0 0 0-8M12 2v3m0 14v3M2 12h3m14 0h3M5 5l2 2m10 10 2 2M5 19l2-2M17 7l2-2',file:'M5 3h9l5 5v13H5zM14 3v6h5M9 13h6m-6 4h6',cpu:'M7 7h10v10H7zM9 2v3m6-3v3M9 19v3m6-3v3M2 9h3m-3 6h3m14-6h3m-3 6h3',branch:'M6 3v14a4 4 0 0 0 4 4m-4-9h8a4 4 0 0 0 4-4V3',trash:'M3 6h18M9 6V3h6v3M5 6l1 15h12l1-15M10 10v7m4-7v7',download:'M12 3v12m-4-4 4 4 4-4M4 18v3h16v-3'};
const icon=n=>`<svg class="icon" viewBox="0 0 24 24" aria-hidden="true"><path d="${icons[n]||icons.file}"/></svg>`;

const heroDiagram = '<svg class="experiment-visual" viewBox="0 0 180 205" aria-hidden="true"><g fill="none" stroke="#b7cfc0" stroke-width="1.5"><path d="M90 26v35M90 61H40v33m50-33h50v33M40 123v28H20v22m20-22h35v22m65-50v28h-35v22m35-22h20v22"/></g><rect x="62" y="3" width="56" height="32" rx="8" fill="#1c7161"/><text x="90" y="24" text-anchor="middle" fill="white" font-size="12">Sample</text><rect x="12" y="90" width="58" height="30" rx="7" fill="#d4e7d9" stroke="#aecbb6"/><text x="41" y="109" text-anchor="middle" fill="#44745a" font-size="10">AWARE</text><rect x="105" y="90" width="72" height="30" rx="7" fill="#e5e3ef" stroke="#c8c1db"/><text x="141" y="109" text-anchor="middle" fill="#75618d" font-size="10">AWARE-free</text><g fill="#fff" stroke="#bad2c1"><rect x="5" y="169" width="29" height="26" rx="6"/><rect x="60" y="169" width="29" height="26" rx="6"/><rect x="94" y="169" width="29" height="26" rx="6"/><rect x="145" y="169" width="29" height="26" rx="6"/></g><g fill="#5e8e71" font-size="10" text-anchor="middle"><text x="20" y="186">R</text><text x="75" y="186">T</text><text x="109" y="186">R</text><text x="160" y="186">T</text></g></svg>';
const e=x=>String(x??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const $=s=>root.querySelector(s), $$=s=>[...root.querySelectorAll(s)];
const U=root._workspaceState ||= {view:'overview',modal:null,drafts:{},selected:[],tool:'hybas',filter:'',handled:null,pending:null,fields:{}};
const S=data.workspace;
let pollTimer, toastTimer;
const navs=[['overview','Overview','home'],['samples','Samples & variants','samples'],['experiments','Polishing comparisons','branch'],['jobs','Jobs & progress','jobs'],['reports','Results & reports','file'],['compare','Compare variants','compare'],['install','Install & update','install'],['resources','Databases & models','resources'],['configure','Configuration','config']];
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
function toast(message,error=false){const t=$('#toast');t.textContent=message;t.classList.add('show');t.classList.toggle('toast-error',error);clearTimeout(toastTimer);toastTimer=setTimeout(()=>t.classList.remove('show'),6500);}
function download(name,content,type='text/plain'){const url=URL.createObjectURL(new Blob([content],{type}));const a=document.createElement('a');a.href=url;a.download=name;root.append(a);a.click();a.remove();setTimeout(()=>URL.revokeObjectURL(url),10000);}
function request(kind,payload={}){if(U.pending)return;U.pending=Array.from(crypto.getRandomValues(new Uint32Array(4)),x=>x.toString(16)).join('-');setTriggerValue('action',{id:U.pending,kind,payload});renderBusy();}
function renderBusy(){root.classList.toggle('is-busy',!!U.pending);const status=$('#connection-status');if(status)status.textContent=U.pending?'Working…':'Connected to local worker';for(const el of $$('input,textarea,select,button[data-action]:not([data-action="close"])')){if(U.pending&&!el.disabled){el.disabled=true;el.dataset.pendingDisabled='true';}else if(!U.pending&&el.dataset.pendingDisabled){el.disabled=false;delete el.dataset.pendingDisabled;}}}
function navigate(view){U.view=view;U.modal=null;U.error='';render();$('#main').focus({preventScroll:true});window.scrollTo(0,0);}
function modal(kind,id=''){U.modal={kind,id};renderModal();$('.modal input,.modal select,.modal button')?.focus();}
function closeModal(){if(U.modal?.kind==='folders'&&U.browserReturn){U.modal=U.browserReturn;U.browserReturn=null;U.error='';renderModal();return;}U.modal=null;U.browserReturn=null;$('#modal-root').innerHTML='';$('.app-shell').removeAttribute('inert');render();}
function setDraft(id,value){U.drafts[id]=value;const el=$('#'+id);if(el)el.value=value;}
function browse(target,path='',nearest=false){U.browserReturn=U.modal?{...U.modal}:null;U.browser=null;U.drafts['folder-query']='';U.drafts['folder-hidden']=false;modal('folders',target);request('browse_folders',{path,nearest});}
function draftInputs(){for(const el of $$('input[id],textarea[id],select[id]')){if(el.type==='file')continue;U.drafts[el.id]=el.type==='checkbox'?el.checked:el.value;}}
function runTable(runs,checks=false){
 if(!runs.length)return empty(U.runList==='archived'?'No archived runs.':'No saved runs in this list.',U.runList==='archived'?'Removed runs appear here and can be restored.':'Add samples and create a run draft when you are ready.');
 return `<div class="table-wrap"><table><thead><tr>${checks?'<th>Select</th>':''}<th>Saved run</th><th>Source branch</th><th>Polishing</th><th>Status</th><th>Actions</th></tr></thead><tbody>${runs.map(r=>`<tr data-run-row="${e(r.id)}">${checks?`<td><input type="checkbox" data-select="${e(r.id)}" aria-label="Select ${e(r.label)}" ${U.selected.includes(r.id)?'checked':''}></td>`:''}<td><button class="sample-name" data-action="run-detail" data-id="${e(r.id)}">${e(r.label)}</button><span class="secondary-line">${e(r.sample.id)} · ${S.pipelines[r.tool].name}</span><span class="secondary-line run-identity">${e(r.id)}</span></td><td>${r.tool==='hybas'?badge(r.aware==='off'?'AWARE-free':'AWARE',r.aware==='off'?'off':'green'):badge('BaSIS','blue')}</td><td>${e(r.strategy)}</td><td>${r.archived?badge('Archived'):statusBadge(r.status)}</td><td><div class="table-actions">${quiet('run-detail','Details',r.id)}${r.archived?btn('restore-run','Restore',false,r.id,'arrow'):btn('archive-run','Remove from list',false,r.id,'trash',r.busy)}</div></td></tr>`).join('')}</tbody></table></div>`;
}
function pipelineRows(){return ['hybas','basis'].map(t=>{const i=activeInstall(t)||S.installs.find(x=>x.tool===t);return `<div class="pipeline-row"><div class="pipeline-logo ${t}">${t==='hybas'?'H':'B'}</div><div class="pipeline-info"><strong>${S.pipelines[t].name}</strong><small>${e(i?.version.ref||'Choose a version to get started')}</small></div>${badge(i?.setup_state||'Not installed',i?.ready?'green':'amber')}</div>`;}).join('');}
function overview(){const active=S.jobs.filter(j=>['queued','running'].includes(j.status)).length;
return heading('WORKSPACE OVERVIEW','Your next run starts here.','Prepare your tools, organize sample variants and explore polishing strategies.',btn('new-sample','Add samples',true,'','plus'))+
`<div class="stats-grid">${[['Samples',S.samples.length,'Registered local folders','samples'],['Run variants',visibleRuns().length,'Originals and independent branches','branch'],['Active jobs',active,'Durable local execution','clock'],['Pipelines',S.installs.filter(i=>i.ready&&S.active[i.tool]===i.id).length+' / 2','HybAs + standalone BaSIS','cpu']].map(([l,v,f,i])=>`<div class="stat"><div class="stat-label">${l}${icon(i)}</div><div class="stat-value">${v}</div><div class="stat-foot">${f}</div></div>`).join('')}</div>
<div class="split"><section class="card experiment-card"><div class="card-body"><div class="copy"><span class="mini-chip">DESIGNED FOR YOUR WORKFLOW</span><h2>One sample.<br>More ways to compare.</h2><p>Keep your source run. Create separate AWARE-free branches and explore alternative polishing strategies.</p><div class="button-row">${btn('polish','Compare polishing',true,'','branch')}${quiet('noaware','Clone without AWARE')}</div></div>${heroDiagram}</div></section><section class="card"><div class="card-head"><h2>Your pipelines</h2>${quiet('go-install','Manage')}</div><div class="card-body">${pipelineRows()}<div class="foot-meta">Exact source versions · reusable environments</div></div></section></div>
<section class="card"><div class="card-head"><div><h2>Samples & variants</h2><p>Your originals and their alternative runs, together.</p></div>${quiet('go-samples','View all')}</div>${runTable(visibleRuns().slice(0,5))}<div class="section-note">Each variant keeps its parent identity and full configuration.</div></section>
<div class="software-test"><div><strong>Is your workspace ready?</strong><p>Run a tiny Snakemake workflow and open its HTML test report.</p></div>${btn('smoke','Run live smoke test',false,'','check')}</div>`;}
function samples(){
 const archived=U.runList==='archived',rows=S.runs.filter(r=>!!r.archived===archived&&`${r.sample.id} ${r.label} ${r.id}`.toLowerCase().includes(U.filter.toLowerCase()));
 return heading('SAMPLES & RUNS','One sample, a clear history.','Input folders and saved runs each have their own list.',btn('import','Import table',false,'','file')+btn('new-sample','Add sample',true,'','plus'))+
 `<section class="card saved-runs"><div class="card-head"><div><h2>Run drafts & history</h2><p>Each row is a saved configuration with its own run ID.</p></div>${btn('new-run','Create run drafts',true,'','play')}</div><div class="run-list-toolbar"><div class="tabs"><button type="button" class="tab ${!archived?'active':''}" data-action="run-list" data-id="current">Current runs <span>${visibleRuns().length}</span></button><button type="button" class="tab ${archived?'active':''}" data-action="run-list" data-id="archived">Archived <span>${S.runs.filter(r=>r.archived).length}</span></button></div><input class="search" id="sample-search" aria-label="Search samples or variants" placeholder="Find a sample or run…" value="${e(U.filter)}"></div>
 ${!archived&&U.selected.length?`<div class="selection-bar"><strong>${U.selected.length} selected</strong><div class="button-row">${btn('batch-dry-run','Check / dry-run')}${btn('batch-prepare','Prepare tools')}${btn('batch-run','Run / resume',true)}${btn('archive-selected','Remove from list',false,'','trash')}${btn('clear-selection','Clear')}</div></div>`:''}${runTable(rows,!archived)}<div class="section-note">${archived?'Restore a run to use it again. Its configuration, files and reports are retained.':'Remove from list moves an inactive run to Archived. Queued and running jobs stay visible.'}</div></section>
 <section class="card registered-samples"><div class="card-head"><div><h2>Registered samples</h2><p>Folders available when creating new drafts.</p></div>${badge(S.samples.length+' folder'+(S.samples.length===1?'':'s'))}</div>${S.samples.length?`<div class="table-wrap"><table><thead><tr><th>Sample</th><th>Read mode</th><th>Input folder</th><th>Actions</th></tr></thead><tbody>${S.samples.map(s=>`<tr><td>${e(s.id)}</td><td>${e(s.mode)}</td><td class="path-cell">${e(s.path)}</td><td>${btn('remove-sample','Remove sample',false,s.id,'trash')}</td></tr>`).join('')}</tbody></table></div>`:empty('Start with a sample folder.','Your original input files stay where they are.',btn('new-sample','Add sample',true))}<div class="section-note">Remove sample also moves its inactive saved runs to Archived. Input files and reports are kept.</div></section>`;
}
function experiments(){const runs=visibleRuns().filter(r=>r.tool==='hybas');return heading('POLISHING COMPARISONS','Explore the alternatives.','Choose an original run or an AWARE-free variant as the parent for new strategies.',btn('polish','New comparison',true,'','branch'))+
notice('Independent workflows in this alpha','Variants inherit the full parent configuration and original input files. Efficient post-AWARE checkpoint reuse and polishing-only execution are not implemented yet.',true)+
`<section class="card"><div class="card-head"><h2>Choose your starting point</h2></div>${runTable(runs)}</section>`;}
function compare(){const rows=U.selected.length?visibleRuns().filter(r=>U.selected.includes(r.id)):visibleRuns().slice(0,6);return heading('COMPARE VARIANTS','Different settings. Clear provenance.','Select variants in Samples & variants to compare their settings and open their reports.')+`<div class="compare-grid">${rows.map(r=>`<section class="card run-compare">${badge(S.pipelines[r.tool].name,'blue')}<h3>${e(r.label)}</h3>${statusBadge(r.status)}<dl class="detail-list"><dt>Sample</dt><dd>${e(r.sample.id)}</dd><dt>Parent</dt><dd>${e(r.parent||'Original draft')}</dd><dt>Branch</dt><dd>${r.aware==='off'?'AWARE-free':'AWARE'}</dd><dt>Polishing</dt><dd>${e(r.strategy)}</dd><dt>Resources</dt><dd>${r.cores} cores · ${r.memory_mb} MB</dd><dt>Commit</dt><dd>${e(r.commit?.slice(0,12))}</dd></dl>${btn('run-detail','Frozen configuration',false,r.id)}${quiet('view-results','View results',r.id)}</section>`).join('')}</div>${!rows.length?empty('No variants to compare yet.','Create a run draft, then add an alternative polishing strategy.',btn('new-run','Create run drafts',true)):notice('Compare the actual reports','Scientific metrics are not synthesized here. Use MultiQC, Safety and other native outputs to evaluate your variants.')}`;}
function appUpdateNotice(){const u=S.app_update;if(!u)return '';
if(u.active_job)return notice('Updating Bactologix','Open Jobs & progress to follow the installation. Wait for the result before restarting.');
if(u.restart_required)return notice('Restart Bactologix',`Installed package: v${e(u.installed_version)}. In the Bactologix terminal press Ctrl+C, then rerun your launch command with the same port. Refresh this browser after restart.`,true);
return '';}
function install(){const d=S.doctor;return heading('INSTALL & UPDATE','Your tools, ready when you are.','Choose source versions, prepare runners and keep existing environments where compatible.')+
`<section class="card"><div class="card-head"><div><h2>Bactologix application</h2><p>Running v${e(S.version)} · Choose which app release to install.</p></div>${btn('app-updates','Update Bactologix',true,'','download')}</div></section>
<div class="stats-grid"><div class="stat"><div class="stat-label">Platform</div><div class="stat-value">${e(d.system)}</div></div><div class="stat"><div class="stat-label">Free disk</div><div class="stat-value">${d.free_gb} GB</div></div><div class="stat"><div class="stat-label">CPU threads</div><div class="stat-value">${d.cpus}</div></div><div class="stat"><div class="stat-label">App Python</div><div class="stat-value">${e(d.python)}</div></div></div>
<section class="card install-card"><div class="card-head"><div><h2>Local environment manager</h2><p class="path-cell">${e(d.conda||'Choose an existing Conda installation or install local Conda.')}</p></div><div class="button-row">${btn('conda','Choose Conda')}${!d.conda?btn('bootstrap','Install local Conda',true):''}</div></div><p class="inline-info">Each pipeline gets its own Python 3.11 / Snakemake runner. Your base Python and Streamlit stay separate.</p></section>
<div class="two-col">${['hybas','basis'].map(t=>{const i=activeInstall(t),p=S.pipelines[t],items=S.installs.filter(x=>x.tool===t),display=i||items[0];return `<section class="card install-card"><div class="pipeline-row"><div class="pipeline-logo ${t}">${t==='hybas'?'H':'B'}</div><div class="pipeline-info"><strong>${p.name}</strong><small>${e(p.preferred)}</small></div>${badge(display?.setup_state||'Not installed',display?.ready?'green':'amber')}</div><p>${t==='hybas'?'Assembly, AWARE and polishing workflows.':'Standalone Snakemake analysis and Safety reporting.'}</p><dl class="detail-list"><dt>Active source</dt><dd>${e(i?.version.ref||'No active installation')}</dd><dt>Commit</dt><dd>${e(i?.version.commit?.slice(0,12)||'—')}</dd></dl><div class="button-row">${btn('versions',i?'Update / choose version':'Choose a version',true,t,'download')}${btn('existing-install','Connect existing',false,t,'folder')}${i?btn('check-install','Check setup',false,i.id,'',i.setup_busy):''}</div>
${items.map(x=>`<div class="installation-entry"><div class="card-head"><strong>${e(x.version.ref)} · ${e(x.version.commit.slice(0,8))}</strong>${badge(x.setup_state,x.ready?'green':'amber')}</div><p class="inline-info">${e(x.setup_message)}</p><div class="button-row">${!x.ready&&x.supported?btn('setup-install',x.setup_state==='Setup incomplete'?'Retry setup':'Finish setup',true,x.id,'',x.setup_busy):''}${!x.supported?btn('app-updates','Update Bactologix'):''}${x.ready?btn('use-install',S.active[t]===x.id?'Active version':'Use this version',false,x.id,'',S.active[t]===x.id||x.setup_busy):''}${x.ready?btn('environments','Check / install environments',false,x.id,'cpu',x.setup_busy):''}${x.setup_job?btn('job-log','Setup log',false,x.setup_job,'file'):''}</div><details class="config-section"><summary>Installation details</summary><div class="padded"><p class="path-cell">${x.origin==='local'?'Connected from: '+e(x.original_source):e(x.source)}</p><p class="path-cell">${e(x.controller||'Runner has not been created')}</p><div class="button-row">${btn('check-install','Check',false,x.id,'',!x.controller||!x.supported||x.setup_busy)}${btn('repair-install','Repair runner',false,x.id,'',!x.supported||x.setup_busy)}</div></div></details></div>`).join('')}</section>`;}).join('')}</div>${notice('Updates reuse compatible environments','Runner environments are identified by pipeline, environment definition and platform, then checked before reuse. Changed definitions create separate environments; prior runs retain their original version.')}`;}
function jobs(){return heading('JOBS & PROGRESS','See what’s happening.','Real worker status and process output. Jobs continue when the browser closes.',btn('refresh','Refresh',false,'','clock')+btn('recover-jobs','Start / recover queue'))+
`<section class="card">${S.jobs.length?S.jobs.map(j=>`<div class="job-card"><div><div class="job-title">${e(j.kind==='environments'?'Environments · '+j.payload.tool+' · '+j.payload.action:j.kind==='database_download'?'Database · '+j.payload.tool+' · '+j.payload.resource:j.kind==='workflow'?j.payload.action+' · '+(runBy(j.payload.run_id)?.label||j.payload.run_id):j.kind==='install'?S.pipelines[j.payload.tool]?.name+' · '+j.payload.version.ref:j.kind==='app_update'?'Bactologix → v'+j.payload.release.version:j.kind)}</div><div class="job-sub">${e(j.id)} · ${e(j.created)}</div>${j.error?`<p class="error">${e(j.error)}</p>`:''}</div><div>${statusBadge(j.status)}${j.cancel?'<small class="secondary-line">Stop requested</small>':''}</div><div class="button-row">${btn('job-log','View log',false,j.id,'file')}${['queued','running'].includes(j.status)&&!(j.kind==='app_update'&&j.status==='running')?btn('cancel-job','Stop',false,j.id,'stop'):''}</div></div>`).join(''):empty('No jobs yet.','Set up a pipeline or run the live software test to begin.',btn('go-install','Install & update',true))}</section>`;}
function reportsPage(){const rows=S.reports.filter(r=>!U.reportRun||r.run===U.reportRun);return heading('RESULTS & REPORTS','From a run to its results.','Open native MultiQC, Safety and other HTML reports directly in your workspace.',btn('register-report','Add existing report',true,'','plus'))+
`${U.reportRun?`<div class="selection-bar">Reports for ${e(runBy(U.reportRun)?.label||U.reportRun)} ${btn('all-reports','Show all reports')}</div>`:''}<section class="card">${rows.length?`<div class="table-wrap"><table><thead><tr><th>Report</th><th>Sample</th><th>Run</th><th>Run status</th><th>Open</th></tr></thead><tbody>${rows.map(r=>`<tr><td><strong>${e(r.name)}</strong><span class="secondary-line path-cell">${e(r.path)}</span></td><td>${e(r.sample)}</td><td>${e(r.run)}</td><td>${statusBadge(r.run_status||'User-selected')}</td><td>${btn('open-report','View HTML',true,r.id,'external')}</td></tr>`).join('')}</tbody></table></div>`:empty('No reports found here yet.','Reports appear when the native workflow writes them. You can also register an existing local HTML report.',btn('register-report','Add report'))}</section>`;}
function configuration(){const t=U.tool, fields=U.fields[t]||S.fields[t], yaml=val('yaml-'+t,S.configs[t]);return heading('CONFIGURATION','Every detail, in one place.','Edit the full native configuration. Every new run keeps a frozen copy.')+
`<div class="tabs">${['hybas','basis'].map(x=>`<button class="tab ${t===x?'active':''}" data-action="config-tool" data-id="${x}">${S.pipelines[x].name}</button>`).join('')}</div><section class="card config-card"><div class="config-source"><div><span class="eyebrow">NATIVE RUN CONFIGURATION</span><strong>${e(S.pipelines[t].preferred)}</strong><small>${fields.length} settings available · unknown keys are preserved</small></div>${badge('Future run defaults','green')}</div>
<div class="config-toolbar"><div class="button-row"><label class="btn upload-label">Import YAML<input type="file" id="yaml-upload" accept=".yaml,.yml" class="visually-hidden"></label>${btn('download-yaml','Download YAML')}${btn('inspect-config','Validate / refresh settings')}</div>${btn('save-config','Save defaults',true)}</div>
<details class="config-section"><summary>Find and edit a setting<span class="section-count">${fields.length}</span></summary><div class="padded"><div class="field"><label for="setting-search">Search settings</label><input id="setting-search" placeholder="threads, model, resource path…" value="${e(val('setting-search'))}"></div><div class="form-grid">${select('setting-'+t,'Setting',fields.map(f=>[f.name,f.name]),fields[0]?.name)}${area('setting-value-'+t,'YAML value',fields.find(f=>f.name===val('setting-'+t,fields[0]?.name))?.yaml||'')}</div>${btn('apply-setting','Apply setting to draft')}<p class="inline-info">Guided editing preserves values and rewrites YAML formatting and comments.</p></div></details>
<div class="yaml-field">${area('yaml-'+t,'Full configuration',yaml,'spellcheck="false" rows="25"')}</div><div class="config-bottom"><p>Managed output paths and CPU limits are bound when a run is created. Use absolute paths for existing databases and models. Saving defaults never rewrites an existing run.</p>${btn('save-config','Save defaults',true)}</div></section>`;}
function runEditor(){return U.runConfigs?.[val('run-tool','hybas')];}
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
 const counter=$('#run-config-changes');if(counter)counter.textContent=Object.keys(c.changes).length+' edited settings';
 for(const b of $$('[data-action="reset-run-field"]'))b.disabled=!Object.hasOwn(c.changes,b.dataset.id);
}
function runSetting(c,f,index,guided=false,label=f.label,help=f.help){
 const value=runFieldValue(c,f),id='run-setting-'+index;
 const attrs=`id="${id}" data-run-field="${e(f.key)}" data-run-name="${e(f.name)}" aria-label="${e(guided?label:f.label+' ('+f.name+')')}"`;
 let control='';
 if(f.managed){
  let managed='Assigned when the draft is created';
  if(['threads','execution.cores'].includes(f.name))managed='Controlled by CPU cores per run above';
  if(['memory_mb','execution.memory_mb'].includes(f.name))managed='Controlled by Memory budget above';
  control=`<p class="run-managed-value">${e(managed)}</p>`;
 }else if(f.kind==='boolean')control=`<label class="run-toggle"><input type="checkbox" role="switch" ${attrs} ${value?'checked':''}><span>${value?'On':'Off'}</span></label>`;
 else if(f.kind==='select'){
  const labels={off:'Off',aware:'AWARE',auto:'Auto',manual:'Manual',none:'None',racon:'Racon',medaka:'Medaka',tripolish:'Tripolish'};
  const options=f.options.includes(value)?f.options:[value,...f.options];
  control=`<select ${attrs}>${options.map(x=>`<option value="${e(x)}" ${x===value?'selected':''}>${e(labels[x]||x)}</option>`).join('')}</select>`;
 }else if(f.kind==='yaml')control=`<textarea ${attrs} rows="${value?.includes('\n')?4:2}" spellcheck="false">${e(value)}</textarea>`;
 else control=`<input ${attrs} type="${['integer','number','optional_integer'].includes(f.kind)?'number':'text'}" ${f.kind==='number'?'step="any"':f.kind.includes('integer')?'step="1"':''} value="${e(value??'')}" ${f.kind.startsWith('optional_')?'placeholder="Automatic / not set"':''}>`;
 if(guided)return `<div class="guided-field field ${f.kind==='boolean'?'switch-card':''}"><div><label for="${id}">${e(label)}</label>${help?`<p>${e(help)}</p>`:''}</div>${control}</div>`;
 return `<div class="run-setting field" data-run-search="${e((f.name+' '+f.label+' '+f.group).toLowerCase())}" data-setting-group="${e(f.group)}"><div class="run-setting-heading"><label for="${id}">${e(f.label)}</label>${!f.managed?btn('reset-run-field','Reset edit',false,f.key,'',!Object.hasOwn(c.changes,f.key)):badge('Managed')}</div><small class="run-setting-key">${e(f.name)}</small>${control}${f.help?`<small>${e(f.help)}</small>`:''}${!f.included&&!f.managed?'<small>Available default · added to this draft only when edited.</small>':''}${f.kind==='yaml'&&!f.managed?'<small>YAML value: use null for unset values, [] for lists, or {} for mappings.</small>':''}</div>`;
}
function filterRunSettings(){
 const c=runEditor();if(!c)return;
 const q=val('run-setting-search','').toLowerCase().trim(),group=U.runConfigGroup?.[c.tool]||c.groups[0];
 let count=0;
 for(const row of $$('[data-run-search]')){row.hidden=q?!row.dataset.runSearch.includes(q):row.dataset.settingGroup!==group;if(!row.hidden)count++;}
 for(const section of $$('[data-run-section]'))section.hidden=![...section.querySelectorAll('[data-run-search]')].some(r=>!r.hidden);
 const empty=$('#run-settings-empty');if(empty)empty.hidden=count>0;
 for(const b of $$('[data-action="run-setting-group"]')){b.classList.toggle('active',!q&&b.dataset.id===group);b.setAttribute('aria-pressed',String(!q&&b.dataset.id===group));}
}
function advancedRunSettings(c){
 const group=U.runConfigGroup?.[c.tool]||c.groups[0];
 return `<div class="run-settings-toolbar"><div class="field">${field('run-setting-search','Search run settings','','search','placeholder="Kraken, BUSCO, memory, model…"')}</div><span id="run-config-changes">${Object.keys(c.changes).length} edited settings</span>${btn('reload-run-defaults','Reload saved defaults',false,'','config')}</div><div class="run-settings-layout"><nav class="run-settings-nav" aria-label="Configuration sections">${c.groups.map(g=>`<button type="button" class="${group===g?'active':''}" data-action="run-setting-group" data-id="${e(g)}">${e(g)}<span>${c.fields.filter(f=>f.group===g).length}</span></button>`).join('')}</nav><div class="run-settings-fields">${c.groups.map(g=>`<section data-run-section="${e(g)}"><h3>${e(g)}</h3><div class="run-settings-grid">${c.fields.map((f,i)=>f.group===g?runSetting(c,f,i):'').join('')}</div></section>`).join('')}<p id="run-settings-empty" hidden>No settings match this search.</p></div></div>`;
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
 const tab=U.runSettingTab?.[c.tool]||guidedTabs[c.tool][0][0];
 return `<div class="settings-tabs" aria-label="Run settings categories">${guidedTabs[c.tool].map(([id,label,ico])=>`<button type="button" class="settings-tab ${id===tab?'active':''}" data-action="guided-run-tab" data-id="${id}" aria-pressed="${id===tab}">${icon(ico)}${label}</button>`).join('')}</div>`;
}
function runSettingsPanel(c){
 const tab=U.runSettingTab?.[c.tool]||guidedTabs[c.tool][0][0],v=name=>{const f=c.fields.find(f=>f.name===name);return f?runFieldValue(c,f):null;};
 const fields=items=>`<div class="guided-grid">${items.map(x=>runGuideField(c,...x)).join('')}</div>`;
 const intro=(title,desc,tag='')=>`<div class="settings-intro"><div><h3>${title}</h3><p>${desc}</p></div>${tag?badge(tag,'green'):''}</div>`;
 let content='';
 if(tab==='advanced')content=`${intro('Every setting, when you need it.','Search all native options, or edit the full configuration including custom keys.')}<div class="advanced-actions">${btn('run-config-tab','Full YAML',false,'yaml','file')}</div>${advancedRunSettings(c)}`;
 else if(c.tool==='hybas'&&tab==='kraken'){
  content=intro('Choose how Kraken is used.','Pick a mode for these drafts. Your saved settings remain unchanged.',v('run_kraken')&&v('kraken_decision_mode')!=='off'?'Kraken enabled':'Kraken off')+runChoiceCards(c,'kraken_decision_mode',[
   ['off','Off','Skip Kraken for this run.'],['manual','Manual','Use a specified target taxon.'],['auto','Auto','Use the native Auto rules.'],['aware','AWARE','Use the native AWARE rules.']]);
  content+=fields([['target_taxid','Target taxon ID','Optional unless required by the selected mode or configuration.'],['kraken_mem_mb','Kraken memory (MB)','Leave blank for the pipeline estimate.']]);
  content+=`<div class="settings-tip">${icon('info')}<span>Mode selection keeps the Kraken on/off switch in sync. Decision thresholds and additional options are available in Advanced.</span></div>`;
 }else if(c.tool==='hybas'&&tab==='assembly'){
  content=intro('Assembly & polishing','Choose a strategy, then adjust its settings.')+runChoiceCards(c,'polish_mode',[
   ['none','None','Keep the baseline assembly.'],['racon','Racon','Racon polishing.'],['medaka','Medaka','Medaka polishing.'],['tripolish','Tripolish','Native multi-step polishing.']]);
  content+=fields([...(v('polish_mode')==='racon'||v('polish_mode')==='tripolish'?[['racon_rounds','Racon rounds']]:[]),...(v('polish_mode')==='medaka'||v('polish_mode')==='tripolish'?[['medaka_model','Medaka model','Leave blank to retain native model selection.']]:[]),['run_polypolish','Polypolish','Keep this stage enabled where the selected read mode supports it.'],['keep_percent','Reads to retain (%)']]);
  content+=`<details class="guided-details"><summary>Assembly memory limits</summary>${fields([['assembly_mem_mb','Assembly memory (MB)'],['polishing_mem_mb','Polishing memory (MB)'],['spades_memory_gb','SPAdes memory (GB)']])}</details><p class="settings-note">The native dry-run checks the strategy against each sample’s read mode.</p>`;
 }else if(c.tool==='hybas'&&tab==='quality'){
  content=intro('Quality checks & annotation','Choose the outputs to include in these drafts.')+fields([['run_busco','BUSCO','Assembly completeness checks.'],['run_prokka','Prokka','Genome annotation.'],['run_multiqc','MultiQC','Combined quality report.'],['min_fasta_length','Minimum sequence length','Length threshold passed to the native workflow.']]);
 }else if(c.tool==='hybas'&&tab==='databases'){
  content=intro('Database locations for this run','These values start from your saved database selections.')+fields([['db_root','Database root folder','Base folder containing the database layout.'],['kraken_db_name','Kraken database folder','Folder name or path used by HybAs.'],['busco_lineage','BUSCO lineage','Lineage name or path used by HybAs.']]);
  content+=`<div class="settings-tip">${icon('folder')}<span>To discover or download databases, close this dialog and open Databases & models. Changes here apply only to the drafts you create.</span></div>`;
 }else if(c.tool==='basis'&&tab==='modules'){
  const modules=c.fields.filter(f=>f.name.startsWith('modules.')&&f.name.endsWith('.enabled')&&f.kind==='boolean');
  content=intro('Build your analysis','Switch modules on or off for these drafts.',modules.filter(f=>runFieldValue(c,f)).length+' enabled')+`<div class="guided-grid module-grid">${modules.map(f=>runGuideField(c,f.name,f.group.replace('Module: ',''),'')).join('')}</div><p class="settings-note">Module databases, tool options and per-module resources are available in Advanced.</p>`;
 }else if(c.tool==='basis'&&tab==='aware'){
  content=intro('BaSIS AWARE','Use the saved preset or choose another native preset.')+fields([['deep.aware.enabled','Enable AWARE','Use AWARE for these drafts.']])+runChoiceCards(c,'deep.aware.preset',[
   ['auto-strict','Strict','Native auto-strict preset.'],['auto-medium','Medium','Native auto-medium preset.'],['auto-loose','Loose','Native auto-loose preset.']])+`<p class="settings-note">Custom queue and tool settings remain available in Advanced.</p>`;
 }else if(c.tool==='basis'&&tab==='deep'){
  content=intro('DeepSafe','Optional settings are inherited from your saved configuration.')+fields([['deep.enabled','Enable DeepSafe','Include the DeepSafe workflow.'],['extend','Extend','Use the native Extend setting.'],['deep.threads','DeepSafe threads'],['deep.memory_mb','DeepSafe memory (MB)']])+`<p class="settings-note">Additional DeepSafe options and reference paths are available in Advanced.</p>`;
 }else if(c.tool==='basis'&&tab==='resources'){
  content=intro('Resources & storage','Choose how resources are supplied and where results are retained.')+runChoiceCards(c,'resources.policy',[
   ['auto','Automatic','Allow native resource preparation.'],['manual','Manual','Use the resources you supply.']])+fields([['resources.directory','Resources folder'],['storage.native_results_directory','Native results folder','Optional. Leave blank to use the pipeline location.']]);
 }
 return runSettingTabs(c)+`<div class="guided-panel">${content}</div>`;
}
function runReview(c,chosen){
 const v=name=>{const f=c.fields.find(f=>f.name===name);return f?runFieldValue(c,f):'—';};
 const pairs=c.tool==='hybas'?[['Kraken mode',v('run_kraken')?v('kraken_decision_mode'):'off'],['Polishing',v('polish_mode')],['BUSCO',v('run_busco')?'On':'Off'],['Prokka',v('run_prokka')?'On':'Off'],['MultiQC',v('run_multiqc')?'On':'Off']]:[['Enabled modules',c.fields.filter(f=>f.name.startsWith('modules.')&&f.name.endsWith('.enabled')&&runFieldValue(c,f)===true).length],['AWARE',v('deep.aware.enabled')?'On':'Off'],['DeepSafe',v('deep.enabled')?'On':'Off']];
 return `<div class="settings-intro"><div><h3>Ready to create your drafts?</h3><p>Each sample gets its own saved run. Nothing starts until you launch it.</p></div></div><div class="review-stats"><div><span>Samples</span><strong>${chosen.length}</strong></div><div><span>CPU per run</span><strong>${e(val('run-cores',4))} cores</strong></div><div><span>Memory per run</span><strong>${e(val('run-memory',16000))} MB</strong></div></div><dl class="review-settings">${pairs.map(([k,v])=>`<div><dt>${e(k)}</dt><dd>${e(v)}</dd></div>`).join('')}</dl><div class="review-samples">${chosen.map(s=>`<div>${icon('samples')}<strong>${e(s.id)}</strong><span>${e(s.mode.replaceAll('_',' '))}</span></div>`).join('')||'<p>No samples selected. Return to Samples to choose one.</p>'}</div><p class="settings-note">Saved defaults and existing runs are unchanged. Check each new draft with a native dry-run before preparing tools or running it.</p>`;
}
function renderNewRun(){
 const tool=val('run-tool','hybas'),c=runEditor(),tab=U.runConfigTab||'samples',chosen=S.samples.filter(s=>val('run-sample-'+s.id,false));
 let body=`<div class="run-draft-intro"><strong>Your run, your settings.</strong><span>Start with saved defaults. Customize only the drafts you create here.</span></div><div class="form-grid run-budget-grid">${select('run-tool','Pipeline',[['hybas','HybAs'],['basis','BaSIS']],'hybas')}${field('run-cores','CPU cores per run',Math.min(4,S.doctor.cpus||1),'number','min="1" max="512"')}${field('run-memory','Memory budget (MB)',16000,'number','min="512" max="2000000" step="512"')}</div><div class="tabs run-config-tabs">${[['samples','Samples'],['settings','Run settings'],['review','Review']].map(([id,label],i)=>`<button type="button" class="tab ${(tab==='yaml'?'settings':tab)===id?'active':''}" data-action="run-config-tab" data-id="${id}" ${!c?'disabled':''}><span class="step-number">${i+1}</span>${label}</button>`).join('')}</div>`;
 if(!c)return body+'<p>Loading saved configuration…</p>';
 if(tab==='samples')body+=`<h3>Select samples</h3><p class="inline-info">These samples receive the same settings. Removing a sample also moves its inactive saved runs to Archived; files and reports are kept.</p>${S.samples.length?S.samples.map(s=>`<div class="sample-choice"><label class="source-card"><input type="checkbox" id="run-sample-${e(s.id)}" data-sample="${e(s.id)}" ${val('run-sample-'+s.id,false)?'checked':''}><span><strong>${e(s.id)}</strong><small>${e(s.path)}</small></span></label>${btn('remove-sample','Remove sample',false,s.id,'trash')}</div>`).join(''):empty('Add a sample first.','Register the folder containing your original inputs.',btn('new-sample','Add sample'))}`;
 else if(tab==='settings')body+=runSettingsPanel(c);
 else if(tab==='review')body+=runReview(c,chosen);
 else body+=`${runSettingTabs(c)}<div class="guided-panel"><div class="settings-intro"><div><h3>Full configuration</h3><p>Edit custom keys or paste your existing YAML. Returning to the controls checks its format.</p></div>${btn('run-config-tab','Back to controls',false,'settings')}</div>${area('run-yaml-'+tool,'Run configuration YAML',c.text,'spellcheck="false" rows="18" data-run-yaml="'+tool+'"')}</div>`;
 body+=`<div class="run-draft-summary"><span>${chosen.length} sample${chosen.length===1?'':'s'} selected · ${e(S.pipelines[tool].name)} · Settings apply to these drafts only</span>${btn('check-run-config','Check settings',false,'','check')}</div>`;
 return body;
}
function dbPath(t,id){return 'dbpath-'+t+'-'+id;}
function dbState(t){U.databaseStates ||= {};return U.databaseStates[t] ||= {revision:S.databases[t].revision,checks:{},resets:{}};}
function clearDatabases(t){for(const key of Object.keys(U.drafts))if(key.startsWith('dbpath-'+t+'-')||key.startsWith('dbcand-'+t+'-'))delete U.drafts[key];U.databaseStates ||= {};delete U.databaseStates[t];for(const r of S.databases[t].rows){if(!r.managed){setDraft(dbPath(t,r.id),r.path);setDraft('dbcand-'+t+'-'+r.id,'');}}}
function resourceDownloads(tool,key){
 const rows=S.database_downloads.filter(d=>d.tool===tool&&d.resource===key).slice(-3).reverse();
 return rows.map(d=>`<div class="resource-download"><div class="card-head"><strong>Download ${d.id.slice(-6)}</strong>${statusBadge(d.status==='completed'?'completed':d.job_status)}</div><p class="path-cell">${e(d.destination)}</p>${d.error?`<p class="error">${e(d.error)}</p>`:''}<div class="button-row">${d.status==='completed'?btn('use-downloaded','Use downloaded',false,d.id,'check'):['failed','cancelled','interrupted'].includes(d.job_status)?btn('resume-download','Resume download',false,d.id,'play'):''}${btn('job-log','Download log',false,d.job,'file')}</div></div>`).join('');
}
function resources(){const t=U.resourceTool||'hybas',p=S.databases[t],state=dbState(t),scan=state.scan;
const dirty=p.rows.filter(r=>!r.managed&&(state.resets[r.id]||val(dbPath(t,r.id),r.path)!==r.path));
return heading('DATABASES & MODELS','Your databases, connected.','Choose installed resources for each pipeline. Saved locations apply to new run drafts.',btn('save-databases','Save locations',true,t,'check',!dirty.length)+btn('use-databases-for-runs','Use saved databases for runs',false,t,'resources',!!dirty.length||!visibleRuns().some(r=>r.tool===t&&!r.busy&&r.run_type!=='followup')))+
`<div class="tabs">${['hybas','basis'].map(x=>`<button class="tab ${x===t?'active':''}" data-action="database-tool" data-id="${x}">${e(S.pipelines[x].name)}</button>`).join('')}</div>
<section class="card database-scan"><div class="card-head"><div><h2>Find installed databases</h2><p>Scan known locations and common folders, or include a folder on another disk.</p></div>${btn('scan-databases','Auto scan',true,t,'resources')}</div><div class="card-body"><div class="folder-address">${field('dbscan-'+t,'Additional scan folder (optional)','','text','placeholder="/home/jimi/databases or D:\\databases"')}${btn('browse-folder','Browse',false,'dbscan-'+t,'folder')}</div><p class="inline-info">A valid saved location is preferred. Otherwise the scan suggests a recognized installation; review its release and scope before saving.</p>
${scan?`<details class="database-scan-details"><summary>${Object.values(scan.candidates).reduce((n,a)=>n+a.length,0)} candidates found · ${scan.visited} folders checked${scan.limited?' · scan limits reached':''}</summary><p>${e(scan.scope)}</p>${scan.limits.length?`<p>Reached: ${e(scan.limits.join(', '))}. Scan a more specific folder to continue.</p>`:''}${scan.inaccessible?`<p>${scan.inaccessible} inaccessible folders skipped.</p>`:''}<p class="path-cell">${scan.roots.map(e).join('<br>')}</p></details>`:''}</div></section>
${!S.download_options[t].available?notice('Set up database downloads','In Install & update, select and finish setup for the recommended pipeline version.'):''}${scan?.invalid.length?`<details class="card database-rejected"><summary>${scan.invalid.length} locations need attention</summary>${scan.invalid.map(x=>`<div><strong>${e(p.rows.find(r=>r.id===x.id)?.name||x.id)}</strong><p class="path-cell">${e(x.path)}</p><p class="error">${e(x.message)}</p></div>`).join('')}</details>`:''}
${state.revision!==p.revision?notice('Saved defaults have changed','Reload locations before saving these choices.',true):''}
<div class="database-summary"><span>${dirty.length?dirty.length+' unsaved selection'+(dirty.length===1?'':'s'):'Saved locations'} · ${t==='basis'?'Missing resources follow the '+e(p.policy)+' preparation policy.':'Download databases here, then save the prepared locations.'}</span>${btn('reload-databases','Reload locations',false,t)}</div>
<div class="database-grid">${p.rows.filter(r=>!r.managed).map(r=>{const id=dbPath(t,r.id),value=val(id,r.path),checked=state.checks[r.id],choices=scan?.candidates[r.id]||[],chosen=choices.find(c=>c.path===value),status=checked?.path===value?checked:chosen,reset=state.resets[r.id],invalid=scan?.invalid.find(x=>x.id===r.id&&x.path===value);
return `<section class="card database-card"><div class="database-title"><h2>${e(r.name)}</h2>${badge(reset?'Pipeline default':status?'Layout recognized':value?'Not checked':'Pipeline default',status?'green':'')}</div><p class="database-description">${e(r.description)}</p><div class="folder-address">${field(id,r.name+' location',r.path,'text','placeholder="Choose an installed database folder"')}${btn('browse-database','Browse',false,r.id,'folder')}</div>
${choices.length?select('dbcand-'+t+'-'+r.id,'Detected installations · '+choices.length,[['','Choose a detected location'],...choices.map(c=>[c.path,(c.preferred?'Preferred · ':'')+(c.version?c.version+' · ':'')+c.path])],chosen?.path||''):''}
${status?`<p class="database-evidence">${icon('check')}<span>${e(status.reason||'Expected files found. Native runtime checks still apply.')}${status.version?'<br>Recorded version: '+e(status.version):''}</span></p>`:invalid?`<p class="error">${e(invalid.message)}</p>`:''}
${reset?'<p class="inline-info">Save to use the pipeline default for this resource.</p>':''}
<div class="database-actions">${btn('check-database','Check location',false,r.id,'check',!value||!!reset)}${btn('reset-database','Use pipeline default',false,r.id,'',!value&&!reset)}${btn('download-database','Download database',false,r.id,'download',!S.download_options[t].available)}</div>${resourceDownloads(t,r.id)}</section>`;}).join('')}</div>
<section class="card database-managed"><div class="card-head"><h2>Tool-managed resources</h2></div><div class="card-body">${p.rows.filter(r=>r.managed).map(r=>`<div><strong>${e(r.name)}</strong>${badge('Tool-managed')}<p>${e(r.description)}</p></div>`).join('')}</div></section>
${notice('A quick layout check','Discovery reads filenames and small metadata files. The native pipeline performs its full resource checks when preparing a run. Scanning does not download databases.')}
<div class="database-footer"><span>${dirty.length?'Review your choices, then save for future runs.':'Existing run drafts keep their frozen resource paths.'}</span>${btn('save-databases','Save locations',true,t,'check',!dirty.length)}</div>`;}
function render(){draftInputs();$('#navigation').innerHTML=navs.map(([key,label,ico])=>`${key==='install'?'<div class="nav-divider"></div>':''}<a href="#${key}" data-nav="${key}" class="nav-item ${U.view===key?'active':''}" ${U.view===key?'aria-current="page"':''}>${icon(ico)}<span>${label}</span>${key==='jobs'&&S.jobs.some(j=>['queued','running'].includes(j.status))?`<span class="nav-count">${S.jobs.filter(j=>['queued','running'].includes(j.status)).length}</span>`:''}</a>`).join('');$('#crumb').textContent=navs.find(n=>n[0]===U.view)?.[1]||'Overview';$('#app-version').textContent='v'+S.version;$('#workspace-name').textContent=S.workspace.split('/').pop();$('#workspace-name').title=S.workspace;
$('#main').innerHTML=appUpdateNotice()+(!U.modal&&U.error?`<div class="error" role="alert">${e(U.error)}</div>`:'')+({overview,samples,experiments,jobs,reports:reportsPage,compare,install,resources,configure:configuration}[U.view]||overview)();
if(!$('#connection-status')){const x=document.createElement('div');x.id='connection-status';x.className='connection-status';$('.topbar').append(x);}renderBusy();renderModal();schedulePoll();}
function schedulePoll(){clearTimeout(pollTimer);if(!U.pending&&(S.app_update?.active_job||(U.view==='jobs'&&S.jobs.some(j=>['queued','running'].includes(j.status))))){pollTimer=setTimeout(()=>request(U.modal?.kind==='log'?'job_log':'refresh',U.modal?.kind==='log'?{id:U.modal.id}:{}),4000);}}
function renderModal(){const m=U.modal;if(!m){$('#modal-root').innerHTML='';$('.app-shell').removeAttribute('inert');return;}
let title='',desc='',body='',foot='',wide=false;
if(m.kind==='app-update'){
 wide=true;title='Update Bactologix';desc='Choose an application release from GitHub.';
 const u=S.app_update,c=u.catalog,rows=c?.releases||[],chosen=rows.find(r=>String(r.id)===val('app-release'));
 body=`<div class="card-head"><div><strong>Running v${e(S.version)}</strong><p class="path-cell">Application environment: ${e(u.environment)}</p></div>${btn('refresh-app-releases','Refresh releases')}</div><p class="inline-info">${c?'Last checked: '+e(c.checked_at):'Checking available releases…'}</p>
 <div class="version-list">${rows.map(r=>`<label class="source-card"><input type="radio" name="app-version-choice" value="${r.id}" ${String(r.id)===val('app-release')?'checked':''} ${!r.available||r.version===S.version?'disabled':''}><span><strong>${e(r.tag)}</strong> ${badge(r.prerelease?'Prerelease':'Stable',r.prerelease?'amber':'green')}${r.version===S.version?' '+badge('Running','green'):r.older?' '+badge('Older release','amber'):''}<small>${e(r.name)} · ${e(r.published_at||'')}</small>${!r.available?'<small>Installable wheel with checksum is not available.</small>':''}<a href="${e(safeURL(r.url))}" target="_blank" rel="noopener noreferrer">Release on GitHub ↗</a>${r.notes?`<details><summary>Release notes</summary><pre class="release-notes">${e(r.notes)}</pre></details>`:''}</span></label>`).join('')}</div>
 ${notice('Application update','Installs the selected version in the environment shown above. Finish queued/running jobs first. After installation, restart Bactologix from its terminal using the same launch command. Pipeline runners, databases and run folders stay separate.')}
 ${chosen?.older?notice('Older release selected','Earlier app versions may support fewer pipeline revisions. Choose the intended release before installing.',true):''}
 ${!u.can_install?notice(u.restart_required?'Restart required':'Finish active jobs first',u.restart_required?'Restart the app before installing another version.':'You can browse releases now and install when the job queue is idle.',true):''}`;
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
 wide=true;title='Tool environments';const item=S.installs.find(i=>i.id===m.id),panel=S.environments[m.id];desc=S.pipelines[item.tool].name+' · '+item.version.ref;
 body=`<p>Check installed tools or install the selected environments. These controls do not run samples or enable optional analyses.</p><div class="folder-address">${field('environment-cache-'+m.id,'Environment cache folder',panel.cache)}${btn('browse-env-cache','Browse',false,m.id,'folder')}</div>${btn('save-env-cache','Use this cache',false,m.id)}<p class="inline-info">Use a cache path without spaces or shell characters. Cache changes apply to future drafts; existing runs retain their original cache.</p>${panel.error?notice('Environment inventory unavailable',e(panel.error),true):''}<div class="environment-list">${panel.rows.map(r=>`<label class="environment-row"><input id="envpick-${e(m.id)}-${e(r.id)}" data-environment="${e(r.id)}" type="checkbox" ${val('envpick-'+m.id+'-'+r.id,true)?'checked':''}><span><strong>${e(r.name)}</strong><small>${e(r.id)}${r.checked_at?' · Checked '+e(r.checked_at):''}</small>${r.path?`<small class="path-cell">${e(r.path)}</small>`:''}${r.message?`<small>${e(r.message)}</small>`:''}</span>${badge(r.status,r.status==='Verified'?'green':['Check failed','Incomplete'].includes(r.status)?'amber':'')}</label>`).join('')}</div>${notice('Reusable environments','Installation uses the pipeline recipes and its existing cache. A failed tool check stays visible; a completed environment is not deleted automatically.')}`;
 foot=btn('check-environments','Check selected',false,m.id,'check',panel.busy)+btn('install-environments','Install selected',true,m.id,'download',panel.busy);
}
if(m.kind==='download-database'){
 const t=U.resourceTool||'hybas',r=S.databases[t].rows.find(x=>x.id===m.id);title='Download '+r.name;desc='Prepare a database on this machine.';
 body=`<div class="folder-address">${field('download-parent','Download location',S.download_options[t].parent)}${btn('browse-folder','Browse',false,'download-parent','folder')}</div><p>A new subfolder will be created here. After preparation, use the downloaded location and save it for future run drafts.</p><div class="form-grid">${field('download-cores','Threads',4,'number','min="1" max="1024"')}${field('download-memory','Memory budget (MB)',16000,'number','min="1" max="10000000"')}${r.id==='busco'?field('download-lineage','BUSCO lineage',S.databases[t].lineage||'bacteria_odb10'):''}</div>${notice(r.id==='kraken'?'Kraken2 standard database build':'Native database preparation',r.id==='kraken'?'This downloads and builds the standard database. It can need substantial disk space, RAM and time; choose resources suitable for your machine.':'The pipeline downloads and prepares this resource and any required dependencies. Follow progress or stop the job in Jobs & progress.')}`;
 foot=btn('start-database-download','Download and prepare',true,m.id,'download');
}
if(m.kind==='sample'){title='Add a sample';desc='Register an existing folder on this machine.';body=`<div class="form-grid">${field('sample-id','Sample ID','','text','required')}${select('sample-mode','Read mode',[['hybrid','Hybrid'],['ont_only','ONT only'],['illumina_only','Illumina only']],'hybrid')}<div class="field wide">${field('sample-path','Input folder','','text','placeholder="/home/jimi/bactomics/sample01" required')}${btn('browse-folder','Browse folders',false,'sample-path','folder')}</div></div>${notice('Input layout','HybAs: raw/illumina and raw/nanopore. BaSIS: annotation/&lt;sample_id&gt;.fna, .faa and .gff, plus .gbk when required.')}`;foot=btn('save-sample','Register sample',true);}
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
 <div class="local-install-section"><h3>2. Choose the runner</h3><p class="inline-info">An existing runner must pass the Python 3.11 / Snakemake 9.6.2 and pipeline import checks. Selecting one never installs or updates packages in it.</p>
 <div class="folder-address">${select('existing-env-choice','Detected Conda environments',[['','Create / reuse a Bactologix runner'],...envs.map(x=>[x.path,x.name+' · Python '+x.python+' · Snakemake '+x.snakemake]),['manual','Browse to another environment']],val('existing-env-choice',''))}${btn('find-envs','Detect environments')}</div>
 ${val('existing-env-choice')?`<div class="folder-address">${field('existing-controller','Environment folder','','text','placeholder="/home/jimi/miniforge3/envs/hybas"')}${btn('browse-folder','Browse',false,'existing-controller','folder')}</div>`:'<p class="inline-info">Bactologix will reuse its compatible runner or prepare a separate one.</p>'}
 ${U.localEnvs?.warning?`<p class="inline-info">${e(U.localEnvs.warning)}</p>`:''}</div>
 <div class="local-install-section"><h3>3. Reuse analysis tool environments (optional)</h3><div class="folder-address">${field('existing-cache','Existing Snakemake Conda cache','','text','placeholder="Leave blank for the Bactologix cache"')}${btn('browse-folder','Browse',false,'existing-cache','folder')}</div><p class="inline-info">A typical cache is &lt;pipeline&gt;/.snakemake/conda. Snakemake reuses matching environments and may add missing ones when you prepare a run.</p></div>
 ${notice('Keep your existing work','Connecting copies committed workflow source locally. Import your native YAML and set database paths in Configuration. Existing outputs are available as reports; they are not adopted for in-place resume.')}`;
 foot=btn('connect-local','Connect installation',true,t,'check',!info?.supported||info.path!==val('existing-source'));
}
if(m.kind==='import'){title='Import a sample table';desc='CSV or TSV · sample_id, input_folder, read_mode';body=`<label class="btn upload-label">Choose CSV / TSV<input type="file" id="sample-upload" accept=".csv,.tsv" class="visually-hidden"></label><div class="form-grid">${area('sample-table','Table contents','sample_id,input_folder,read_mode\n','rows="9"')}</div>${btn('sample-template','Download template')}`;foot=btn('import-samples','Import samples',true);}
if(m.kind==='conda'){title='Choose Conda';desc='Use an existing executable or install local Conda from Install & update.';body=field('conda-path','Conda executable',S.doctor.conda||'');foot=btn('save-conda','Save location',true);}
if(m.kind==='new-run'){wide=true;title='Create run drafts';desc='Choose samples and configure this run.';body=renderNewRun();const empty=!runEditor()||!S.samples.some(s=>val('run-sample-'+s.id,false));foot=U.runConfigTab==='review'?btn('create-runs','Create drafts',true,'','play',empty):btn('run-config-tab',U.runConfigTab==='samples'?'Continue to settings':'Review drafts',true,U.runConfigTab==='samples'?'settings':'review','arrow',empty);}
if(m.kind==='clone'){title='Compare polishing strategies';desc='Choose a parent run and create independent variants.';const runs=visibleRuns().filter(r=>r.tool==='hybas');const id=val('clone-parent',m.id||runs[0]?.id);const r=runBy(id);const options=r?.sample.mode==='illumina_only'?[['none','Baseline'],['tripolish','Polypolish']]:[['none','Baseline'],['racon','Racon'],['medaka','Medaka'],['tripolish','Tripolish']];body=`${select('clone-parent','Parent run',runs.map(r=>[r.id,r.label+' · '+r.id]),id)}<label class="check-field"><input type="checkbox" id="clone-aware" ${val('clone-aware',false)?'checked':''}><span>Create an AWARE-free branch<small>Otherwise inherit the parent’s AWARE settings.</small></span></label><div class="strategy-grid">${options.map(([s,l])=>`<label class="strategy-card"><span class="row"><input type="checkbox" id="strategy-${s}" data-strategy="${s}" ${val('strategy-'+s,['none','tripolish'].includes(s))?'checked':''}>${l}</span></label>`).join('')}</div>${notice('Fresh workflows from original inputs','Full parent settings are inherited. Post-AWARE checkpoint reuse and polishing-only execution are not yet implemented.',true)}`;if(!r)body=empty('Create a HybAs run first.','A saved parent configuration is required.',btn('new-run','Create run draft',true));foot=r?btn('create-clones','Create variant drafts',true):'';}
if(m.kind==='versions'){wide=true;const t=m.id,c=S.catalogs[t];title='Choose '+S.pipelines[t].name+' version';desc='Compatible snapshots bundled with this app. '+(c.live?'Live GitHub check: ':'Bundled GitHub snapshot: ')+c.checkedAt;body=`<div class="toolbar"><input class="search" id="version-search" placeholder="Search versions…" aria-label="Search versions" value="${e(val('version-search'))}">${select('version-channel','Channel',[['all','All'],['pinned','Compatible snapshots'],['release','Releases'],['branch','Branches']],'all')}${btn('refresh-versions','Refresh from GitHub',false,t)}</div><div class="version-list">${c.versions.map(v=>`<label class="source-card version-choice" data-version-kind="${v.kind}" data-version-name="${e((v.ref+' '+v.name).toLowerCase())}"><input type="radio" name="version" value="${e(v.id)}" ${val('version-'+t)===v.id?'checked':''}><span><strong>${e(v.ref)}</strong> ${v.preferred&&v.supported?badge('Recommended','green'):''} ${badge(v.kind,'blue')} ${v.supported?badge('Runner supported','green'):badge('Source only')}<small>${e(v.name)} · ${e(v.commit.slice(0,12))}</small><a href="${e(safeURL(v.url))}" target="_blank" rel="noopener noreferrer">Source / release notes ↗</a>${v.notes?`<details><summary>Release notes</summary><pre class="release-notes">${e(v.notes)}</pre></details>`:''}</span></label>`).join('')}</div>${notice('Keep your existing environments','Compatible runners are checked and reused. Other source versions can be downloaded, but managed runs require a reviewed adapter for that exact commit.')}`;const chosen=c.versions.find(x=>x.id===val('version-'+t));foot=btn('install-version',chosen?.supported?'Download and set up':'Download source only',true,t,'',!chosen);}
if(m.kind==='run'){wide=true;const r=runBy(m.id),d=U.detail?.id===m.id?U.detail:null;title=r?.label||'Run details';desc=m.id;body=d?`<div class="detail-pills">${badge(S.pipelines[r.tool].name,'blue')}${statusBadge(r.status)}${badge(r.dry_run_ok?'Native dry-run passed':'Needs dry-run',r.dry_run_ok?'green':'amber')}</div><p class="path-cell run-output">${e(d.output)}</p><div class="button-row">${r.archived?btn('restore-run','Restore run',true,r.id,'arrow'):btn('dry-run','Check / dry-run',true,r.id,'check',r.busy)}${btn('prepare','Prepare tools',false,r.id,'',r.busy||r.archived||!r.dry_run_ok)}${btn('run','Run / resume',false,r.id,'play',r.busy||r.archived||!r.dry_run_ok)}${r.run_type!=='followup'?btn('use-run-databases','Use saved databases',false,r.id,'resources',r.busy||r.archived):''}</div><details class="config-section"><summary>Database paths in this run</summary><div class="padded">${(d.database_paths||[]).map(x=>`<p><strong>${e(x.name)}</strong><br><span class="path-cell">${e(x.path||'Pipeline default')}</span></p>`).join('')}</div></details><details class="config-section"><summary>Execution plan and frozen configuration</summary><div class="padded"><pre class="code-block">${e(d.command)}</pre><pre class="code-block">${e(d.config)}</pre>${btn('download-frozen','Download frozen YAML',false,r.id)}<p class="inline-info">${e(d.reuse)}</p></div></details>${r.run_type==='followup'?'':`<details class="config-section"><summary>Create a revised draft</summary><div class="padded">${area('revised-'+r.id,'New draft configuration',d.submitted,'rows="12" spellcheck="false"')}${btn('revise','Create revised draft',false,r.id)}</div></details>`}<div class="button-row">${r.tool==='hybas'?btn('polish','Clone / compare',false,r.id,'branch'):''}${r.can_handoff?btn('handoff','Create BaSIS draft',false,r.id):''}${r.can_followup?btn('followup','Review follow-up',false,r.id):''}${btn('view-results','View results',false,r.id,'file')}</div>`:'<p>Loading frozen run configuration…</p>';}
if(m.kind==='followup'){wide=true;title='Review saved screening for follow-up';desc=m.id;body=`${notice('Separate follow-up','Review the screening report and settings first. Follow-up uses saved screening evidence and writes into its follow-up folder. A later attempt replaces those follow-up outputs; screening outputs remain separate.',true)}${btn('view-results','Open screening results',false,m.id)}${area('followup-yaml-'+m.id,'Reviewed follow-up configuration','deep:\n  enabled: false\n  maximum_candidates: 5\nresource_receipts: []\n','rows="12" spellcheck="false"')}<label class="check-field"><input type="checkbox" id="followup-reviewed" ${val('followup-reviewed',false)?'checked':''}><span>I reviewed the screening report and this follow-up request.</span></label><p>Set enabled to true only when requesting follow-up. Resource paths must be absolute and already prepared. The native dry-run checks saved evidence and resource prerequisites.</p>`;foot=btn('create-followup','Create follow-up draft',true,m.id);}
if(m.kind==='log'){wide=true;title='Job log';desc=m.id;const l=U.log?.id===m.id?U.log:null;body=`${l?statusBadge(l.status):''}${l?.error?`<div class="error">${e(l.error)}</div>`:''}<pre class="code-block log-output">${e(l?.text||'Loading process output…')}</pre>`;foot=btn('job-log','Refresh log',false,m.id)+btn('download-log','Download full log',false,m.id);}
if(m.kind==='register-report'){title='Add an existing HTML report';desc='Choose a local file. Its association is user-selected.';body=`<div class="form-grid">${field('report-name','Report label','Existing report')}${field('report-path','HTML report path','','text','placeholder="/home/jimi/results/safety/report.html"')}</div>`;foot=btn('save-report','Register report',true);}
if(m.kind==='report'){wide=true;const r=U.report?.id===m.id?U.report:null;title=r?.name||'HTML report';desc=r?.path||'Loading report…';body=r?'<div id="report-frame-container"></div><p class="inline-info">Self-contained HTML works best. Open the original with its companion files for relative assets.</p>':'<p>Loading the original HTML report…</p>';foot=btn('open-original','Open original in browser',false,m.id)+btn('download-report','Download original HTML',true,m.id,'download',!r);}
if(m.kind==='credits'){title='Built by Jimi Goldstein';desc='Bactologix · Human Link';body=`<div class="author-card"><span class="brand-mark">b<span>•</span></span><div><h3>Your workflow, connected.</h3><p>HybAs + BaSIS in one local workspace.</p></div></div><p class="credits-copy">Created by <strong>Jimi Goldstein</strong> · Human Link<br><a href="https://orcid.org/0000-0002-7829-0090" target="_blank" rel="noopener noreferrer">ORCID 0000-0002-7829-0090 ↗</a></p><pre class="code-block">${e(S.citations['CITATION.txt'])}</pre><div class="button-row">${['ris','bib','cff','txt'].map(x=>btn('citation','Download '+x.toUpperCase(),false,'CITATION.'+x,'download')).join('')}</div><p class="credits-copy">Cite the pipelines, individual tools and databases used in your analysis as well as Bactologix.</p><div class="button-row">${btn('citation','HybAs citation',false,'CITATION.HybAs.cff')}${btn('citation','BaSIS citation',false,'CITATION.BaSIS.cff')}</div><p class="credits-copy"><a href="https://github.com/crackberryq/Bactologix" target="_blank" rel="noopener noreferrer">Bactologix on GitHub ↗</a> · MIT license · v${e(S.version)}</p>`;}
$('#modal-root').innerHTML=`<div class="modal-shade"><section class="modal ${wide?'':'narrow'} ${m.kind==='report'?'report-modal':''}" role="dialog" aria-modal="true" aria-labelledby="modal-title"><div class="modal-head"><div><h2 id="modal-title">${e(title)}</h2><p>${e(desc)}</p></div><button class="icon-btn" data-action="close" aria-label="Close dialog">${icon('close')}</button></div><div class="modal-body">${body}<div id="action-error" class="error" role="alert">${e(U.error||'')}</div></div><div class="modal-foot"><small>Saved on your machine</small><div class="button-row">${btn('close','Close')}${foot}</div></div></section></div>`;
$('.app-shell').setAttribute('inert','');
if(m.kind==='report'&&U.report?.id===m.id){const f=document.createElement('iframe');f.title=U.report.name;f.setAttribute('sandbox','allow-scripts allow-downloads');f.referrerPolicy='no-referrer';f.className='html-report-frame';f.srcdoc=new TextDecoder().decode(Uint8Array.from(atob(U.report.base64),c=>c.charCodeAt(0)));$('#report-frame-container').append(f);}
if(m.kind==='versions')filterVersions();
if(m.kind==='new-run'&&U.runConfigTab==='settings')filterRunSettings();
renderBusy();
}
function safeURL(url){try{const u=new URL(url);return u.protocol==='https:'&&u.hostname==='github.com'?u.href:'#';}catch{return '#';}}
function filterVersions(){const q=val('version-search').toLowerCase(),c=val('version-channel','all');for(const row of $$('.version-choice')){row.hidden=!row.dataset.versionName.includes(q)||(c!=='all'&&row.dataset.versionKind!==c);const radio=row.querySelector('input');if(row.hidden&&radio.checked){radio.checked=false;U.drafts['version-'+U.modal.id]='';}}const chosen=S.catalogs[U.modal.id].versions.find(x=>x.id===val('version-'+U.modal.id));const button=$('[data-action="install-version"]');if(button){button.disabled=!chosen;button.textContent=!chosen?'Choose a version':chosen.supported?'Download and set up':'Download source only';}}
function filterSettings(){const q=val('setting-search').toLowerCase();for(const o of $$('#setting-'+U.tool+' option'))o.hidden=!o.textContent.toLowerCase().includes(q);}
async function change(event){const t=event.target;if(t.id&&t.type!=='file'){U.drafts[t.id]=t.type==='checkbox'?t.checked:t.value;}
if(t.dataset.sample&&U.modal?.kind==='new-run')renderModal();
if(t.dataset.runField){const c=runEditor(),f=c?.fields.find(f=>f.key===t.dataset.runField);if(f){editRunField(c,f,t.type==='checkbox'?t.checked:t.value);if(t.type==='checkbox'||t.tagName==='SELECT')renderModal();}}
if(t.id==='run-tool'){draftInputs();const c=runEditor();if(c){if(c.raw){U.runSettingTab[c.tool]='advanced';U.runConfigTab='yaml';renderModal();}else if(U.runConfigTab==='yaml'&&Object.keys(c.changes).length)request('run_config_editor',{...runConfigPayload(),view:'yaml'});else renderModal();}else request('run_config_editor',{tool:t.value,view:U.runConfigTab||'samples'});}
if(t.dataset.applyRun||t.dataset.applyResource)renderModal();
if(t.dataset.select){U.selected=t.checked?[...new Set([...U.selected,t.dataset.select])]:U.selected.filter(x=>x!==t.dataset.select);render();}
if(t.id?.startsWith('dbcand-')){const tool=U.resourceTool||'hybas',key=t.id.slice(('dbcand-'+tool+'-').length);if(t.value){setDraft(dbPath(tool,key),t.value);delete dbState(tool).resets[key];render();}}
if(t.id==='existing-env-choice'){setDraft('existing-controller',t.value==='manual'?'':t.value);renderModal();}
if(t.id==='clone-parent'){delete U.drafts['strategy-medaka'];delete U.drafts['strategy-racon'];renderModal();}
if(t.name==='version'){U.drafts['version-'+U.modal.id]=t.value;renderModal();}
if(t.name==='app-version-choice'){U.drafts['app-release']=t.value;renderModal();}
if(t.id==='setting-'+U.tool){const f=(U.fields[U.tool]||S.fields[U.tool]).find(x=>x.name===t.value);U.drafts['setting-value-'+U.tool]=f?.yaml||'';$('#setting-value-'+U.tool).value=U.drafts['setting-value-'+U.tool];}
if(t.id==='yaml-upload'||t.id==='sample-upload'){const f=t.files?.[0];if(!f)return;if(f.size>2*1024*1024){toast('Choose a text file smaller than 2 MB.',true);return;}const text=await f.text();const id=t.id==='yaml-upload'?'yaml-'+U.tool:'sample-table';U.drafts[id]=text;$('#'+id).value=text;if(t.id==='yaml-upload')request('inspect_config',{tool:U.tool,text});}
}
function input(event){const t=event.target;
if(t.dataset.runField){const c=runEditor(),f=c?.fields.find(f=>f.key===t.dataset.runField);if(f)editRunField(c,f,t.type==='checkbox'?t.checked:t.value);}
if(t.dataset.runYaml){const c=U.runConfigs[t.dataset.runYaml];c.text=t.value;c.changes={};c.raw=true;}
if(t.id&&t.type!=='file')U.drafts[t.id]=t.type==='checkbox'?t.checked:t.value;if(t.id?.startsWith('dbpath-')){const tool=U.resourceTool||'hybas',key=t.id.slice(('dbpath-'+tool+'-').length);delete dbState(tool).resets[key];setDraft('dbcand-'+tool+'-'+key,'');for(const b of $$('[data-action="save-databases"]'))b.disabled=false;const pos=t.selectionStart,id=t.id;render();const field=$('#'+id);field?.focus();if(pos!==null)field?.setSelectionRange(pos,pos);}if(t.id==='run-setting-search')filterRunSettings();if(t.id==='version-search'||t.id==='version-channel')filterVersions();if(t.id==='setting-search')filterSettings();if(t.id==='sample-search'){U.filter=t.value;const pos=t.selectionStart;render();$('#sample-search').focus();$('#sample-search').setSelectionRange(pos,pos);}}
function click(event){const nav=event.target.closest('[data-nav]');if(nav){event.preventDefault();navigate(nav.dataset.nav);return;}const a=event.target.closest('[data-action]');if(!a)return;const action=a.dataset.action,id=a.dataset.id;draftInputs();U.error='';
if(action==='close'){closeModal();return;}if(action.startsWith('go-')){navigate(action.slice(3));return;}
const v=x=>$('#'+x)?.value??val(x);
if(action==='browse-folder')browse(id,v(id));
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
else if(action==='new-run'){
 U.runConfigs={};U.runConfigGroup={};U.runSettingTab={};U.runConfigTab='samples';setDraft('run-setting-search','');
 modal('new-run');request('run_config_editor',{tool:val('run-tool','hybas'),view:'samples'});
}
else if(action==='run-config-tab'){
 const c=runEditor();if(!c)return;
 if(id==='yaml'||id==='review'||c.raw)request('run_config_editor',{...runConfigPayload(),view:id,validate:id==='review'});
 else {U.runConfigTab=id;renderModal();}
}
else if(action==='guided-run-tab'){
 const c=runEditor();if(!c)return;U.runSettingTab[c.tool]=id;
 if(U.runConfigTab==='yaml')request('run_config_editor',{...runConfigPayload(),view:'settings'});
 else renderModal();
}
else if(action==='run-choice'){
 const c=runEditor(),f=c?.fields.find(f=>f.key===id);if(f){editRunField(c,f,a.dataset.value);renderModal();}
}
else if(action==='run-setting-group'){U.runConfigGroup[runEditor().tool]=id;setDraft('run-setting-search','');filterRunSettings();}
else if(action==='reload-run-defaults')request('run_config_editor',{tool:val('run-tool','hybas'),view:U.runConfigTab});
else if(action==='check-run-config')request('run_config_editor',{...runConfigPayload(),view:U.runConfigTab,validate:true});
else if(action==='reset-run-field'){
 const c=runEditor(),f=c.fields.find(f=>f.key===id);delete c.changes[id];
 if(c.tool==='hybas'&&['run_kraken','kraken_decision_mode'].includes(f?.name))for(const x of c.fields.filter(x=>['run_kraken','kraken_decision_mode'].includes(x.name)))delete c.changes[x.key];
 renderModal();
}
else if(['cite','about'].includes(action))modal('credits');
else if(action==='citation')download('Bactologix-'+id,S.citations[id]);
else if(action==='export-workspace')download('Bactologix-workspace.json',JSON.stringify({exported:new Date().toISOString(),...S},null,2),'application/json');
else if(action==='save-sample')request('add_sample',{id:v('sample-id'),path:v('sample-path'),mode:v('sample-mode')});
else if(action==='sample-template')download('Bactologix-samples.csv','sample_id,input_folder,read_mode\nsample01,/home/jimi/bactomics/sample01,hybrid\n','text/csv');
else if(action==='run-list'){U.runList=id;render();}
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
else if(action==='environments')modal('environments',id);
else if(action==='browse-env-cache')browse('environment-cache-'+id,v('environment-cache-'+id),true);
else if(action==='save-env-cache')request('set_environment_cache',{id,path:v('environment-cache-'+id)});
else if(action==='check-environments'||action==='install-environments')request('manage_environments',{id,action:action==='check-environments'?'check':'install',selected:$$('[data-environment]:checked').map(x=>x.dataset.environment)});
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
else if(action==='save-config'||action==='inspect-config')request(action.replace('-','_'),{tool:U.tool,text:v('yaml-'+U.tool)});
else if(action==='download-yaml')download(U.tool+'-config.yaml',v('yaml-'+U.tool),'text/yaml');
else if(action==='apply-setting')request('edit_setting',{tool:U.tool,text:v('yaml-'+U.tool),field:v('setting-'+U.tool),value:v('setting-value-'+U.tool)});
else if(action==='create-runs')request('create_runs',{tool:v('run-tool'),ids:S.samples.filter(s=>val('run-sample-'+s.id,false)).map(s=>s.id),cores:Number(v('run-cores')),memory_mb:Number(v('run-memory')),config:runConfigPayload()});
else if(action==='clear-selection'){U.selected=[];render();}
else if(action==='run-detail'){U.detail=null;modal('run',id);request('inspect_run',{id});}
else if(['dry-run','prepare','run'].includes(action))request('workflow',{ids:[id],action});
else if(action.startsWith('batch-'))request('workflow',{ids:U.selected,action:action.slice(6)});
else if(action==='polish'||action==='noaware'){if(id)U.drafts['clone-parent']=id;U.drafts['clone-aware']=action==='noaware';modal('clone',id);}
else if(action==='create-clones')request('clone',{id:v('clone-parent'),aware_free:$('#clone-aware').checked,strategies:$$('[data-strategy]:checked').map(x=>x.dataset.strategy)});
else if(action==='revise')request('revise_run',{id,text:v('revised-'+id)});
else if(action==='handoff')request('handoff',{id});
else if(action==='followup'){delete U.drafts['followup-reviewed'];modal('followup',id);}
else if(action==='create-followup')request('create_followup',{id,text:v('followup-yaml-'+id),reviewed:$('#followup-reviewed').checked});
else if(action==='download-frozen'&&U.detail?.id===id)download(id+'.yaml',U.detail.config,'text/yaml');
else if(action==='job-log'){modal('log',id);request('job_log',{id});}
else if(action==='download-log')request('download_log',{id});
else if(action==='cancel-job')request('cancel_job',{id});
else if(action==='view-results'){U.reportRun=id;navigate('reports');}
else if(action==='all-reports'){U.reportRun=null;render();}
else if(action==='register-report')modal('register-report');
else if(action==='save-report')request('register_report',{path:v('report-path'),name:v('report-name')});
else if(action==='open-report'){U.report=null;modal('report',id);request('open_report',{id});}
else if(action==='open-original')request('open_original',{id});
else if(action==='download-report'&&U.report){const r=U.report;download(r.path.split('/').pop(),Uint8Array.from(atob(r.base64),c=>c.charCodeAt(0)),'text/html');}
}
function keydown(event){if(event.key==='Escape'&&U.modal){event.preventDefault();closeModal();}if(event.key==='Tab'&&U.modal){const nodes=$$('.modal button:not(:disabled),.modal input:not(:disabled),.modal select,.modal textarea,.modal a[href],.modal summary').filter(n=>!n.hidden);const first=nodes[0],last=nodes[nodes.length-1],focused=parentElement.activeElement;if(event.shiftKey&&focused===first){last?.focus();event.preventDefault();}else if(!event.shiftKey&&focused===last){first?.focus();event.preventDefault();}}}
const response=data.response;
// Preserve drafts before rendering a new server snapshot.
draftInputs();
if(response&&response.id!==U.handled){U.handled=response.id;U.pending=null;U.error=response.ok?'':response.message;if(response.config){const c=response.config;U.drafts['yaml-'+c.tool]=c.text;U.fields[c.tool]=c.fields;const el=$('#yaml-'+c.tool);if(el)el.value=c.text;}if(response.browser){U.browser=response.browser;setDraft('folder-path',response.browser.path);}if(response.database_scan){const scan=response.database_scan,t=scan.tool,state=dbState(t);state.scan=scan;for(const r of S.databases[t].rows){const key=dbPath(t,r.id),preferred=scan.candidates[r.id]?.find(c=>c.preferred);if(preferred&&!state.resets[r.id]&&val(key,r.path)===r.path){setDraft(key,preferred.path);setDraft('dbcand-'+t+'-'+r.id,preferred.path);}}}
if(response.removed_sample){
 const key='run-sample-'+response.removed_sample;delete U.drafts[key];
 // render() captures the outgoing form once more before replacing the dialog.
 const checkbox=$$('[data-sample]').find(el=>el.dataset.sample===response.removed_sample);if(checkbox)checkbox.checked=false;
}
if(response.run_config){
 const c=response.run_config;U.runConfigs ||= {};U.runConfigs[c.tool]={...c,changes:{},raw:false};
 U.runConfigTab=response.run_config_view;if(U.runConfigTab==='yaml'){U.runSettingTab ||= {};U.runSettingTab[c.tool]='advanced';}setDraft('run-yaml-'+c.tool,c.text);
}
if(response.database_run_plan)U.databaseRunPlan=response.database_run_plan;
if(response.database_check){const c=response.database_check,t=c.tool;dbState(t).checks[c.id]=c;setDraft(dbPath(t,c.id),c.path);delete dbState(t).resets[c.id];}
if(response.database_saved){clearDatabases(response.database_saved);}
if(response.discovery)U.discovery=response.discovery;if(response.local_envs)U.localEnvs=response.local_envs;if(response.local_install){U.localInstall=response.local_install;setDraft('existing-source',response.local_install.path);setDraft('existing-cache',response.local_install.rule_cache);}if(response.detail)U.detail=response.detail;if(response.log)U.log=response.log;if(response.report)U.report=response.report;if(response.download)download(response.download.name,response.download.text,response.download.type);if(response.ok&&!response.keep_modal)U.modal=null;if(response.navigate)U.view=response.navigate;if(response.open_run)U.modal={kind:'run',id:response.open_run};}
root.addEventListener('click',click);root.addEventListener('input',input);root.addEventListener('change',change);root.addEventListener('keydown',keydown);
render();
if(response?.id===U.handled&&response.message)toast(response.message,!response.ok);
const dispose=()=>{clearTimeout(pollTimer);clearTimeout(toastTimer);root.removeEventListener('click',click);root.removeEventListener('input',input);root.removeEventListener('change',change);root.removeEventListener('keydown',keydown);};
root._dispose=dispose;
return dispose;
}
