"""Browser regressions for local progress, using software-only job snapshots."""
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
import pytest
from bactologix.runs import create_run
from bactologix.ui_backend import dispatch
from test_browser import render_component, assert_workspace_fits_viewport
from test_maintenance import installed

pytestmark = pytest.mark.skipif(os.environ.get('BACTOLOGIX_BROWSER_TESTS') != '1',
                               reason='Enable browser acceptance explicitly')

@pytest.fixture
def progress_page(tmp_path, installed):
    from playwright.sync_api import sync_playwright
    store, _ = installed
    sample = tmp_path / 'software sample'
    (sample / 'raw/illumina').mkdir(parents=True)
    for mate in (1, 2):
        (sample / f'raw/illumina/fixture_R{mate}.fastq').write_text('@fixture\nA\n+\nI\n')
    run = create_run(store, 'hybas', {'id':'isolate0001','path':str(sample),'mode':'illumina_only'},
                     '{}', label='isolate0001 · assembly')
    jid = store.submit('workflow', {'run_id':run['id'],'action':'run'})
    detail = dispatch(store, 'inspect_run', {'id':run['id']})
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, executable_path=os.environ.get('BACTOLOGIX_BROWSER_EXECUTABLE'))
        page = browser.new_page(viewport={'width':1440,'height':1050})
        page.clock.install()
        page.clock.pause_at(datetime.now(timezone.utc)+timedelta(seconds=1))
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        render_component(page, store)
        page.get_by_role('link',name='Runs',exact=True).click()
        page.get_by_role('link',name='isolate0001 · assembly',exact=True).click()
        page.evaluate('''({rid,jid,detail})=>{
          const root=document.querySelector('#component-host').shadowRoot.querySelector('.bacto-root');
          window.progressState=root._workspaceState;
          window.progressDetail=detail.detail;
          window.progressJob=acceptanceWorkspace.jobs.find(j=>j.id===jid);
          progressJob.status='running';
          acceptanceWorkspace.runs.find(r=>r.id===rid).status='running';
          let reply=0;
          // Never return component's disposer: Playwright auto-invokes a function
          // returned by string-form evaluate(), which would remove every listener.
          window.progressRerender=()=>{acceptanceRender({id:progressState.pending||'progress-fixture-'+(++reply),
            ok:true,detail:progressDetail,keep_modal:true});};
          if(progressRerender()!==undefined)throw new Error('Fixture must not return the component disposer');
        }''', {'rid':run['id'],'jid':jid,'detail':detail})
        page.locator('.primary-run-tabs [data-action="saved-run-tab"][data-id="activity"]').click()
        yield page
        assert errors == []
        browser.close()

def measured(page, completed=3, total=12):
    page.evaluate('''({completed,total})=>{
      const j=progressJob;
      j.telemetry={id:j.id,run_id:j.payload.run_id,action:j.payload.action,
        updated_at:new Date().toISOString(),live:true,elapsed_s:253,peak_rss_mb:203.7,
        latest:{cpu_percent:183.3},samples:10,
        progress:{source:'workflow log',completed,total,percent:99,active_rules:['assembly'],dynamic_plan:true}};
      progressRerender();
    }''', {'completed':completed,'total':total})

def test_run_progress_counts_and_attempt_states(progress_page):
    from playwright.sync_api import expect
    page=progress_page
    bar=page.locator('#main > .run-progress')
    expect(bar).to_contain_text('Running · Run / resume')
    expect(bar).to_contain_text('Task count not reported yet')
    assert bar.get_by_role('progressbar').get_attribute('aria-valuenow') is None
    assert bar.locator('.run-progress-track span').evaluate('n=>getComputedStyle(n).animationName')=='run-progress-stripes'
    measured(page)
    page.evaluate('progressJob.telemetry.run_id=null;progressRerender()')
    expect(bar.locator('progress')).to_have_count(0)
    expect(page.locator('.task-progress')).to_contain_text('3 / 12 tasks in current plan')
    measured(page)
    expect(bar).to_contain_text('3 / 12 tasks in current plan')
    expect(bar.locator('progress')).to_have_attribute('value','3')
    expect(bar.locator('progress')).to_have_attribute('max','12')
    expect(page.locator('.task-progress')).to_contain_text('25%')
    measured(page,3,20)
    expect(bar).to_contain_text('3 / 20 tasks in current plan')
    expect(bar).to_contain_text('Checkpoints can change the task total')
    measured(page,20,20)
    expect(bar).to_contain_text('Running')
    page.evaluate('progressJob.cancel=1;progressRerender()')
    expect(bar).to_contain_text('Stopping')
    page.evaluate("progressJob.cancel=0;progressJob.status='queued';progressJob.held=true;progressRerender()")
    expect(bar).to_contain_text('Held')
    expect(bar.locator('progress,.is-moving')).to_have_count(0)
    page.evaluate('progressJob.held=false;acceptanceWorkspace.queue.paused=true;progressRerender()')
    expect(bar).to_contain_text('Queue paused')
    page.evaluate('acceptanceWorkspace.queue.paused=false;progressRerender()')
    expect(bar).to_contain_text('Queued')
    page.evaluate("progressJob.status='running';progressJob.payload.action='prepare';progressRerender()")
    expect(bar).to_contain_text('Preparing environments')
    expect(bar.locator('progress')).to_have_count(0)
    page.evaluate("progressJob.payload.action='run'")
    for completed,total in [(-1,12),(13,12),(0,0)]:
        measured(page,completed,total)
        expect(bar.locator('progress')).to_have_count(0)
    measured(page)
    page.evaluate("progressJob.telemetry.id='other-attempt';progressRerender()")
    expect(bar.locator('progress')).to_have_count(0)
    page.evaluate("acceptanceWorkspace.jobs.push({...progressJob,id:'old-attempt',status:'failed'});progressJob.telemetry=null;progressRerender()")
    expect(bar).to_contain_text('Task count not reported yet')
    for status in ('succeeded','failed','cancelled','interrupted'):
        page.evaluate('(status)=>{progressJob.status=status;progressRerender()}',status)
        expect(bar).to_have_count(0)

def test_progress_freshness_motion_and_list(progress_page):
    from playwright.sync_api import expect
    page=progress_page
    bar=page.locator('#main > .run-progress')
    page.emulate_media(reduced_motion='reduce')
    assert bar.locator('.run-progress-track span').evaluate('n=>getComputedStyle(n).animationName')=='none'
    page.context.set_offline(True)
    expect(bar).to_contain_text('Offline · last saved status')
    expect(bar.locator('.is-moving')).to_have_count(0)
    page.context.set_offline(False)
    expect(bar).to_contain_text('Running')
    measured(page)
    page.evaluate('progressJob.telemetry.live=false;progressRerender()')
    expect(bar).to_contain_text('Updates paused')
    expect(bar).to_contain_text('Last reported · 3 / 12 tasks in current plan')
    expect(page.locator('.job-progress')).not_to_contain_text('Receiving updates')
    expect(page.locator('.job-progress')).to_contain_text('Last reported steps')
    measured(page)
    page.clock.set_system_time('2000-01-01T00:00:00Z')
    expect(bar).to_contain_text('Running')
    expect(page.locator('.job-progress')).to_contain_text('Receiving updates')
    page.clock.fast_forward(16000)
    expect(bar).to_contain_text('Updates paused')
    measured(page)
    page.get_by_role('button',name='Back to runs',exact=True).click()
    expect(page.locator('.run-progress-compact')).to_contain_text('3 / 12 tasks in current plan')

def test_background_progress_refresh_keeps_editing_context(progress_page):
    from playwright.sync_api import expect
    page=progress_page
    measured(page)
    page.get_by_role('button',name='Back to runs',exact=True).click()
    search=page.get_by_label('Search runs',exact=True)
    search.fill('isolate0001')
    search.focus()
    search.evaluate('n=>n.setSelectionRange(3,5)')
    page.evaluate('progressState.background=true;progressRerender()')
    expect(search).to_be_focused()
    expect(search).to_have_value('isolate0001')
    assert search.evaluate('n=>[n.selectionStart,n.selectionEnd]')==[3,5]
    page.get_by_role('link',name='isolate0001 · assembly',exact=True).click()
    page.evaluate("progressRerender()")
    page.locator('.primary-run-tabs [data-action="saved-run-tab"][data-id="activity"]').click()
    details=page.locator('#main .saved-settings-nav')
    expect(details).to_have_count(1)
    details.evaluate('n=>n.open=true')
    details.locator('summary').focus()
    page.evaluate('progressState.background=true;progressRerender()')
    expect(details).to_have_attribute('open','')
    expect(details.locator('summary')).to_be_focused()
    # These actions also occur in the run heading; polling must keep focus here.
    for action in ('job-log','cancel-job'):
        activity_action=page.locator(f'.run-job button[data-action="{action}"]').first
        activity_action.focus()
        page.evaluate('progressState.background=true;progressRerender()')
        expect(activity_action).to_be_focused()
    preview=os.environ.get('BACTOLOGIX_UI_PREVIEWS')
    if preview:
        Path(preview).mkdir(parents=True,exist_ok=True)
        page.screenshot(path=str(Path(preview)/'run-progress-measured.png'),full_page=True)
    page.set_viewport_size({'width':390,'height':844})
    assert_workspace_fits_viewport(page,'Running isolate controls on mobile')
    if preview:
        page.screenshot(path=str(Path(preview)/'run-progress-mobile.png'),full_page=True)

def test_run_heading_stop_queue_controls_and_resource_counts(progress_page):
    from playwright.sync_api import expect
    page=progress_page
    measured(page)
    controls=page.get_by_role('region',name='Run controls')
    for tab in ('overview','activity'):
        page.evaluate('(tab)=>{progressState.runDetailTab=tab;progressRerender()}',tab)
        expect(controls.get_by_role('button',name='Stop run',exact=True)).to_be_enabled()
    before=page.evaluate('acceptanceEvents.length')
    controls.get_by_role('button',name='Stop run',exact=True).evaluate('n=>{n.click();n.click()}')
    requests=page.evaluate('(before)=>acceptanceEvents.slice(before).map(x=>x.value)',before)
    assert len(requests)==1 and requests[0]['kind']=='cancel_job'
    assert requests[0]['payload']['id']==page.evaluate('progressJob.id')
    page.evaluate('progressJob.cancel=1;progressRerender()')
    expect(controls.get_by_role('button',name='Stopping…',exact=True)).to_be_disabled()
    page.evaluate("progressJob.cancel=0;progressJob.status='queued';progressRerender()")
    assert page.evaluate('progressState.pending') is None
    expect(controls.get_by_role('button',name='Cancel queued run',exact=True)).to_be_enabled()
    controls.get_by_role('button',name='Hold job',exact=True).click()
    request=page.evaluate('acceptanceEvents.at(-1).value')
    assert request['kind']=='queue_control' and request['payload']['action']=='hold'
    assert request['payload']['revision']==page.evaluate('acceptanceWorkspace.queue.revision')
    page.evaluate('progressJob.held=true;progressRerender()')
    expect(controls.get_by_role('button',name='Release hold',exact=True)).to_be_enabled()
    controls.get_by_role('button',name='Pause queue',exact=True).click()
    assert page.evaluate('acceptanceEvents.at(-1).value.payload.action')=='pause'
    page.evaluate('acceptanceWorkspace.queue.paused=true;progressRerender()')
    expect(controls.get_by_role('button',name='Resume queue',exact=True)).to_be_enabled()
    page.evaluate('acceptanceWorkspace.queue.update_active=true;progressRerender()')
    expect(controls.get_by_role('button',name='Resume queue',exact=True)).to_be_disabled()
    expect(controls.get_by_role('button',name='Release hold',exact=True)).to_be_disabled()
    page.evaluate("acceptanceWorkspace.queue.update_active=false;progressJob.status='running';progressRerender()")
    measured(page)
    page.get_by_role('button',name='Resource use',exact=True).click()
    page.evaluate('''()=>acceptanceRender({id:Math.random().toString(),ok:true,keep_modal:true,
      usage:{id:progressJob.id,summary:{...progressJob.telemetry,job_status:'running'},points:[]}})''')
    expect(page.locator('.usage-detail .task-progress')).to_contain_text('3 / 12 tasks in current plan')
    expect(page.locator('.usage-detail .task-progress progress')).to_have_attribute('value','3')
