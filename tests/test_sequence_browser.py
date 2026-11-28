"""Browser acceptance for composing and revising pipeline sequences."""
import os
from pathlib import Path

import pytest

from bactologix.catalog import PIPELINES
from bactologix.runs import create_run, register_sample
from bactologix.ui_backend import dispatch, snapshot
from test_browser import assert_workspace_fits_viewport, render_component
from test_native import installation, sample, workspace

pytestmark = pytest.mark.skipif(os.environ.get('BACTOLOGIX_BROWSER_TESTS') != '1',
                                reason='Enable the browser acceptance job to run Chromium')


@pytest.fixture
def sequence_runs(workspace, installation, sample, tmp_path, monkeypatch):
    for tool in ('basis', 'micpscope'):
        item = dict(installation, id='installed-' + tool, tool=tool)
        monkeypatch.setitem(PIPELINES[tool], 'commit', item['version']['commit'])
        workspace.put('installs', item['id'], item)
        workspace.put('settings', 'active-' + tool, item['id'])
    other = tmp_path / 'second isolate'
    other.mkdir()
    register_sample(workspace, 'second', str(other), 'illumina_only')
    parents = []
    for label in ('First assembly', 'Revised assembly'):
        parent = create_run(workspace, 'hybas', sample, '{}', 1, 1024, label=label)
        annotation = Path(parent['output'], 'annotation')
        annotation.mkdir(exist_ok=True)
        for extension in ('fna', 'faa', 'gff'):
            (annotation / ('sample.' + extension)).write_text('fixture\n')
        final = Path(parent['output'], 'work/final')
        final.mkdir(parents=True)
        (final / 'assembly.final.fasta').write_text('>fixture\nACGT\n')
        parent['completed'] = parent['created']
        workspace.put('runs', parent['id'], parent)
        parents.append(parent)
    first, revised = parents
    child = create_run(workspace, 'basis', dict(sample, path=first['output']), '{}',
                       1, 1024, parent=first['id'], label='First assembly screening')
    safety = Path(child['output'], 'safety')
    safety.mkdir(exist_ok=True)
    (safety / 'evidence.json').write_text('{}')
    (safety / 'report.html').write_text('<p>Fixture results</p>')
    child['completed'] = child['created']
    workspace.put('runs', child['id'], child)
    return first['id'], revised['id'], child['id']


@pytest.fixture
def sequence_page(workspace, sequence_runs):
    from playwright.sync_api import sync_playwright
    with sync_playwright() as manager:
        browser = manager.chromium.launch(executable_path=os.environ.get('BACTOLOGIX_BROWSER_EXECUTABLE'))
        page = browser.new_page(viewport={'width': 1440, 'height': 960})
        page.set_default_timeout(3500)
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        render_component(page, workspace)
        yield page
        browser.close()
        assert not errors


def control(page, action, identifier=None):
    selector = f'[data-action="{action}"]'
    if identifier is not None:
        selector += f'[data-id="{identifier}"]'
    return page.locator(selector)


def add_isolate(page, sample):
    page.locator('[data-seq-add-isolate]').select_option(sample)
    control(page, 'sequence-add-isolate').click()


def save(page, store):
    control(page, 'save-sequence').click()
    request = page.evaluate('window.acceptanceEvents.at(-1).value')
    assert request['kind'] == 'save_sequence'
    result = dispatch(store, request['kind'], request['payload'])
    page.evaluate('''({workspace,response}) => {
        Object.assign(window.acceptanceWorkspace, workspace);
        window.acceptanceRender(response);
    }''', {'response': {'id': request['id'], 'ok': True, **result}, 'workspace': snapshot(store)})
    return request['payload']


def test_sequence_builder_reuses_matching_outputs_and_reopens_saved_draft(workspace, sequence_page, sequence_runs):
    page = sequence_page
    first, revised, screening = sequence_runs
    page.get_by_role('link', name='Runs', exact=True).click()
    assert not control(page, 'new-sequence').count()
    page.get_by_role('link', name='Pipeline sequences', exact=True).click()
    control(page, 'new-sequence').click()
    assert control(page, 'sequence-next').is_disabled()
    control(page, 'sequence-add-step', 'basis').click()
    control(page, 'sequence-add-step', 'micpscope').click()
    control(page, 'sequence-step-up', 'micpscope').click()
    control(page, 'sequence-remove-step', 'basis').click()
    control(page, 'sequence-add-step', 'basis').click()
    control(page, 'sequence-next').click()
    add_isolate(page, 'sample')
    assert control(page, 'sequence-next').is_disabled()  # Two prior assemblies need an explicit choice.
    page.locator('[data-seq-source]').select_option(first)
    page.locator('[data-seq-reuse="basis"]').select_option(screening)
    page.locator('[data-seq-source]').select_option(revised)
    assert not page.locator('[data-seq-reuse="basis"] option[value="' + screening + '"]').count()
    page.locator('[data-seq-source]').select_option(first)
    assert page.locator('[data-seq-reuse="basis"]').input_value() != screening
    page.locator('[data-seq-reuse="basis"]').select_option(screening)
    add_isolate(page, 'second')
    control(page, 'sequence-remove-isolate').last.click()
    control(page, 'sequence-next').click()
    page.get_by_text('Pipeline settings', exact=True).click()
    yaml_box = page.locator('[data-seq-config="micpscope"]')
    custom_config = '# Reviewed sequence draft settings\n' + yaml_box.input_value()
    yaml_box.fill(custom_config)
    control(page, 'sequence-back').click()
    control(page, 'sequence-next').click()

    payload = save(page, workspace)
    assert payload['configs']['micpscope'] == custom_config
    assert payload['steps'] == ['micpscope', 'basis']
    assert payload['isolates'] == [{'sample': 'sample', 'source': first, 'reuse': {'basis': screening}}]
    plan, = workspace.all('sequences')
    assert plan['rows'][0]['source'] == first and not workspace.jobs()

    control(page, 'edit-sequence', plan['id']).click()
    control(page, 'sequence-next').click()
    assert page.locator('[data-seq-source]').input_value() == first
    assert page.locator('[data-seq-reuse="basis"]').input_value() == screening
    control(page, 'sequence-next').click()
    page.get_by_text('Pipeline settings', exact=True).click()
    assert page.locator('[data-seq-config="micpscope"]').input_value() == custom_config
    edited = save(page, workspace)
    assert edited['id'] == plan['id'] and edited['revision'] == plan['revision']
    assert len(workspace.all('sequences')) == 1


def test_mobile_sequence_builder_starts_from_registered_isolates(workspace, sequence_page):
    page = sequence_page
    page.set_viewport_size({'width': 390, 'height': 844})
    page.locator('#nav-toggle').click()
    page.get_by_role('link', name='Pipeline sequences', exact=True).click()
    assert_workspace_fits_viewport(page, 'sequence list on mobile')
    control(page, 'new-sequence').click()
    control(page, 'sequence-add-step', 'hybas').click()
    control(page, 'sequence-add-step', 'basis').click()
    assert_workspace_fits_viewport(page, 'ordered pipelines on mobile')
    control(page, 'sequence-next').click()
    add_isolate(page, 'second')
    assert_workspace_fits_viewport(page, 'isolate sources on mobile')
    control(page, 'sequence-next').click()
    assert_workspace_fits_viewport(page, 'draft review on mobile')
    payload = save(page, workspace)
    assert payload['steps'] == ['hybas', 'basis']
    assert payload['isolates'][0]['sample'] == 'second' and not payload['isolates'][0].get('source')
    assert workspace.all('sequences')[0]['rows'][0]['runs'] == [] and not workspace.jobs()


def test_edit_remaining_isolates_after_pilot_from_registered_reads(
        workspace, sequence_page, sequence_runs, tmp_path):
    from bactologix import sequences

    page = sequence_page
    third = tmp_path / 'third isolate'
    third.mkdir()
    register_sample(workspace, 'third', str(third), 'illumina_only')
    plan = sequences.save_inputs(workspace, [{'sample': 'sample'}, {'sample': 'second'}],
                                 ['hybas'], True)
    # A finished pilot has a generated run, while its chosen input is still the
    # original registered reads. Resume must preserve this distinction.
    plan['state'] = 'review'
    pilot = plan['rows'][0]
    pilot.update(started=True, state='done', index=1, runs=[sequence_runs[0]])
    workspace.put('sequences', plan['id'], plan)
    page.evaluate('''workspace => {
        Object.assign(window.acceptanceWorkspace, workspace);
        window.acceptanceRender(null);
    }''', snapshot(workspace))

    page.get_by_role('link', name='Pipeline sequences', exact=True).click()
    control(page, 'edit-sequence', plan['id']).click()
    control(page, 'sequence-remove-isolate', 'second').click()
    add_isolate(page, 'third')
    control(page, 'sequence-next').click()
    control(page, 'save-sequence').click()
    request = page.evaluate('window.acceptanceEvents.at(-1).value')
    assert request['kind'] == 'edit_sequence_isolates'
    assert request['payload']['revision'] == plan['revision']
    dispatch(workspace, request['kind'], request['payload'])
    updated = workspace.get('sequences', plan['id'])
    assert updated['rows'][0] == pilot
    assert [r['sample'] for r in updated['rows']] == ['sample', 'third']


def test_add_batch_and_remove_isolate_on_later_page(workspace, sequence_page, tmp_path):
    page = sequence_page
    for index in range(12):
        folder = tmp_path / f'batch-sample-{index:02d}'
        folder.mkdir()
        register_sample(workspace, folder.name, str(folder), 'illumina_only')
    page.evaluate('''workspace => {
        Object.assign(window.acceptanceWorkspace, workspace);
        window.acceptanceRender(null);
    }''', snapshot(workspace))
    page.get_by_role('link', name='Pipeline sequences', exact=True).click()
    control(page, 'new-sequence').click()
    control(page, 'sequence-add-step', 'hybas').click()
    control(page, 'sequence-next').click()
    control(page, 'sequence-add-all').click()
    assert control(page, 'sequence-remove-isolate').count() == 10
    control(page, 'sequence-isolate-next').click()
    control(page, 'sequence-remove-isolate', 'batch-sample-11').click()
    control(page, 'sequence-next').click()
    payload = save(page, workspace)
    selected = [row['sample'] for row in payload['isolates']]
    assert len(selected) == 13 and 'batch-sample-11' not in selected
    assert set(selected) == {s['id'] for s in workspace.all('samples')} - {'batch-sample-11'}
