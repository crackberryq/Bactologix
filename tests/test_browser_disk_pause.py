"""Real-browser storage pause controls using software-only job snapshots."""
import os
from pathlib import Path

import pytest

from test_browser import assert_workspace_fits_viewport
from test_browser_run_progress import progress_page, measured
from test_maintenance import installed

pytestmark = pytest.mark.skipif(os.environ.get('BACTOLOGIX_BROWSER_TESTS') != '1',
                               reason='Enable browser acceptance explicitly')


def pause(page, **changes):
    page.evaluate('''changes=>{
      progressJob.disk_safety={id:progressJob.id,run_id:progressJob.payload.run_id,
        state:'paused',generation:3,pause_kind:'processes_suspended',reason:'low_space',
        volumes:[{label:'Windows D:',path:'/mnt/d',available_bytes:8*1024**3},
          {label:'Workspace output',path:'/home/jimi/workspace/runs/output',available_bytes:22*1024**3}],
        errors:[],checked_at:new Date().toISOString(),can_resume:false,resume_requested:false,
        thresholds:{pause_bytes:10*1024**3,resume_bytes:15*1024**3,warning_bytes:20*1024**3},...changes};
      progressRerender();
    }''', changes)


def test_pause_visible_static_and_survives_polling(progress_page):
    from playwright.sync_api import expect
    page = progress_page
    pause(page)
    bar = page.locator('#main > .run-progress')
    panel = page.locator('#main > .disk-safety-panel')
    controls = page.get_by_role('region', name='Run controls')
    expect(bar).to_contain_text('Paused — low disk space')
    expect(bar.locator('.is-moving')).to_have_count(0)
    assert bar.locator('.run-progress-track span').evaluate('n=>getComputedStyle(n).animationName') == 'none'
    for text in ('Windows D:', '/mnt/d', '8 GiB', 'Pause at or below 10 GiB', 'at least 15 GiB',
                 'still hold memory', 'Stop run before Bactologix export or cleanup'):
        expect(panel).to_contain_text(text)
    expect(controls.get_by_role('button', name='Resume run', exact=True)).to_be_disabled()
    expect(controls.get_by_role('button', name='Stop run', exact=True)).to_be_enabled()
    expect(page.locator('#queue-status')).to_contain_text('1 storage-paused · 0 running')
    expect(page.get_by_role('region', name='Execution status')).not_to_contain_text('Running')
    measured(page)
    expect(page.locator('.job-progress')).not_to_contain_text('Receiving updates')
    expect(page.locator('.job-progress')).to_contain_text('Steps at pause')
    page.evaluate('progressState.background=true;progressRerender()')
    expect(bar).to_contain_text('Paused — low disk space')
    assert page.locator('.app-shell').evaluate('n=>getComputedStyle(n).opacity') == '1'
    page.get_by_role('button', name='Back to runs', exact=True).click()
    expect(page.locator('[data-run-row]')).to_contain_text('Paused — low disk space')
    expect(page.locator('[data-run-row]')).not_to_contain_text('Running')


def test_resume_generation_once_and_stop_available(progress_page):
    from playwright.sync_api import expect
    page = progress_page
    pause(page, can_resume=True)
    controls = page.get_by_role('region', name='Run controls')
    button = controls.get_by_role('button', name='Resume run', exact=True)
    expect(button).to_be_enabled()
    before = page.evaluate('acceptanceEvents.length')
    button.evaluate('n=>{n.click();n.click()}')
    events = page.evaluate('(before)=>acceptanceEvents.slice(before).map(x=>x.value)', before)
    assert len(events) == 1 and events[0]['kind'] == 'resume_disk_paused_job'
    assert events[0]['payload'] == {'job_id': page.evaluate('progressJob.id'), 'generation': 3}
    pause(page, can_resume=True, resume_requested=True)
    expect(controls.get_by_role('button', name='Resume requested…', exact=True)).to_be_disabled()
    expect(controls.get_by_role('button', name='Stop run', exact=True)).to_be_enabled()
    controls.get_by_role('button', name='Stop run', exact=True).click()
    assert page.evaluate('acceptanceEvents.at(-1).value.kind') == 'cancel_job'


def test_resume_disables_on_stale_or_offline_snapshot(progress_page):
    from playwright.sync_api import expect
    page = progress_page
    pause(page, can_resume=True)
    button = page.get_by_role('region', name='Run controls').get_by_role('button', name='Resume run', exact=True)
    page.context.set_offline(True)
    expect(button).to_be_disabled()
    expect(page.locator('#main > .run-progress')).to_contain_text('Paused — low disk space')
    page.context.set_offline(False)
    pause(page, can_resume=True)
    expect(button).to_be_enabled()
    page.clock.fast_forward(11000)
    expect(button).to_be_disabled()
    page.evaluate('progressJob.cancel=1;progressRerender()')
    expect(page.locator('#main > .run-progress')).to_contain_text('Stopping')


def test_before_launch_distinct_from_suspended_tools(progress_page):
    from playwright.sync_api import expect
    page = progress_page
    pause(page, pause_kind='before_launch', reason='monitor_unavailable', errors=['Windows host drive could not be checked.'])
    panel = page.locator('#main > .disk-safety-panel')
    expect(panel).to_contain_text('Waiting for storage')
    expect(page.locator('#queue-status')).to_contain_text('1 waiting for storage · 0 running')
    expect(panel).to_contain_text('next command has not started')
    expect(panel).not_to_contain_text('processes are suspended')
    expect(panel).to_contain_text('Windows host drive could not be checked')
    pause(page, reason='monitor_unavailable')
    expect(panel).to_contain_text('Paused — storage check unavailable')


def test_activity_page_and_mobile_layout(progress_page):
    from playwright.sync_api import expect
    page = progress_page
    pause(page, can_resume=True)
    page.get_by_role('link', name='Activity', exact=True).click()
    card = page.locator('.job-card')
    expect(card).to_contain_text('Paused — low disk space')
    expect(card.get_by_role('button', name='Resume run', exact=True)).to_be_enabled()
    expect(card.get_by_role('button', name='Stop', exact=True)).to_be_enabled()
    page.set_viewport_size({'width': 390, 'height': 844})
    assert_workspace_fits_viewport(page, 'storage pause mobile')
    if folder := os.environ.get('BACTOLOGIX_UI_PREVIEWS'):
        Path(folder).mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(Path(folder) / 'storage-pause-mobile.png'), full_page=True)
