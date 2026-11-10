"""Preservation-only releases retain their reviewed scientific descriptions."""
import hashlib
import json
from pathlib import Path

import pytest

from bactologix import hybas_methods, methods, setup_advice_source
from bactologix.runs import tree_digest
from bactologix.store import Store


RELEASES = [
    ('hybas', '08c315479f5493ce0b1f44ba26da3c7f2e0d748b',
     '79c5b7f342e02bd5b60bf7847fc5f4fb441e1fd4'),
    ('hybas', 'a99e00b041a184f36cfdf8a2ab5b61f0f255a02d',
     '87c1ab81c8652bd10db47954193e27ad802cfc86'),
    ('hybas', '87c1ab81c8652bd10db47954193e27ad802cfc86',
     '585abb34ed4401da64b37a9fd629ba8adc236396'),
    ('basis', '6128b06279fec2f51015a76f798d0364a4b81602',
     '749e178cc8c1f74b1997e11eca0a624822f92aee'),
    ('basis', '749e178cc8c1f74b1997e11eca0a624822f92aee',
     '7c293d36a633a726424d544c6ffc39824537ebe4'),
    ('micpscope', 'f46cc9feeb30cd89b36876b7d341ed6750e19ce3',
     '92ea05df6e9c156ecba13315993d4cd12142fcfc'),
    ('micpscope', '92ea05df6e9c156ecba13315993d4cd12142fcfc',
     '28bf38c44eb07fe8e4865f67d59ef6b4028f51dc'),
]


def saved_run(tmp_path, tool, commit):
    store = Store(tmp_path / 'workspace')
    folder = store.home / 'runs' / 'run-source-check'
    workflow = folder / 'workflow'
    workflow.mkdir(parents=True)
    (workflow / 'Hybas.smk').write_text('# Inert source identity fixture.\n')
    output = folder / 'outputs' / 'isolate'
    output.mkdir(parents=True)
    config = 'threads: 3\nmemory_mb: 5120\n'
    (folder / 'config.yaml').write_text(config)
    return store, {'id': 'run-source-check', 'tool': tool, 'commit': commit,
                   'sample': {'id': 'isolate', 'mode': 'hybrid'},
                   'folder': str(folder), 'output': str(output), 'config': config,
                   'config_sha256': hashlib.sha256(config.encode()).hexdigest(),
                   'workflow_sha256': tree_digest(workflow), 'cores': 3,
                   'memory_mb': 5120, 'inputs': []}


@pytest.mark.parametrize('tool,previous,current', RELEASES)
def test_preservation_release_retains_configured_methods_and_tool_flow(tmp_path, tool, previous, current):
    store, run = saved_run(tmp_path, tool, previous)
    earlier = methods.build(store, run)
    run['commit'] = current
    updated = methods.build(store, run)
    assert not any('No verified Methods template' in warning for warning in updated['warnings'])
    assert updated['paragraphs'] == [paragraph.replace(previous, current) for paragraph in earlier['paragraphs']]
    assert updated['parameters'] == earlier['parameters']
    assert updated['flow']['tool_steps'] == earlier['flow']['tool_steps']
    assert updated['flow']['edges'] == earlier['flow']['edges']
    assert updated['source_commit'] == current
    assert not any('retention' in row.get('tool', '') or 'preserv' in row.get('tool', '')
                   for row in updated['flow']['tool_steps'])


@pytest.mark.parametrize('tool,previous,current', [row for row in RELEASES if row[0] == 'hybas'])
def test_retention_hybas_keeps_profile_and_measured_runtime_scope(tmp_path, tool, previous, current):
    _, run = saved_run(tmp_path, tool, current)
    metadata = Path(run['output']) / 'metadata'
    metadata.mkdir()
    (metadata / 'run_summary.json').write_text(json.dumps({'isolate': 'isolate',
        'executed_stages': [], 'skipped_stages': {}}))
    (metadata / 'tool_versions.tsv').write_text('tool\tversion\nsnakemake\t9.27.1\nunicycler\t0.5.1\n')
    checksum = hashlib.sha256(Path(run['folder'], 'workflow/Hybas.smk').read_bytes()).hexdigest()
    (metadata / 'workflow_provenance.tsv').write_text('field\tvalue\nconfig_sha256\t' +
        run['config_sha256'] + '\nsource_sha256\t' + checksum + '\n')
    evidence = methods.collect(run)
    assert evidence['version_scopes']['snakemake'] == 'Recorded workflow runtime version'
    assert evidence['version_scopes']['unicycler'] == hybas_methods.PACKAGE_SCOPE
    assert setup_advice_source.HYBAS_PROFILES[current] == setup_advice_source.HYBAS_PROFILES[previous]
    run['commit'] = 'f' * 40
    unknown = methods.collect(run)
    assert 'execution not established' in unknown['version_scopes']['snakemake']
