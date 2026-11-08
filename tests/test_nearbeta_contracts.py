"""Real regular-release approvals and dry-run DAGs; no analytical accuracy claim."""
import copy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import pytest
import yaml
from bactologix import catalog, release_contract
from bactologix.runs import check_run_files, command, create_run, template, tree_digest, validate_launch
from bactologix.store import Store


@pytest.fixture
def reviewed_source():
    location = os.environ.get('BACTOLOGIX_NEARBETA_SOURCE')
    tool = os.environ.get('BACTOLOGIX_NEARBETA_TOOL')
    if not location and not tool:
        pytest.skip('Exact near-beta source checkout not supplied')
    assert location and tool in {'hybas', 'basis', 'micpscope'}
    source = Path(location).resolve()
    commit = subprocess.check_output(['git', '-C', str(source), 'rev-parse', 'HEAD'], text=True).strip()
    assert not subprocess.check_output(['git', '-C', str(source), 'status', '--porcelain'], text=True).strip()
    registry = json.loads((Path(__file__).parents[1] / 'compatibility/releases-v1.json').read_text())
    rows = [row for row in registry['releases'] if row['tool'] == tool and row['commit'] == commit]
    assert len(rows) == 1, f'Expected one regular release approval for {tool} at {commit}'
    row = rows[0]
    assert release_contract.reason(tool, row) is None
    assert row['channel'] in {'stable', 'prerelease'} and '.dev' not in row['tag']
    return tool, source, row


def approved_workspace(tmp_path, reviewed_source):
    tool, source, row = reviewed_source
    store = Store(tmp_path / 'regular workspace with spaces')
    assert release_contract.approval(store, tool, row['commit']) is None
    store.put('release_approvals', tool + ':' + row['commit'], row)
    assert release_contract.approval(store, tool, row['commit']) == row
    assert catalog.supported(tool, row['commit'], store)
    release_contract.preflight(store, tool, row['commit'], source)
    record = {'id': 'reviewed-nearbeta', 'tool': tool, 'source': str(source),
              'controller': sys.prefix, 'ready': True,
              'version': {'commit': row['commit'], 'ref': row['tag']}}
    store.put('installs', record['id'], record)
    store.put('settings', 'active-' + tool, record['id'])
    # Resolve a real executable; the Conda launcher itself is not executed.
    store.put('settings', 'conda', sys.executable)
    return store


def synthetic_sample(tmp_path, tool):
    root = tmp_path / 'harmless software fixture'
    sid = 'synthetic'
    if tool == 'hybas':
        reads = root / 'raw/illumina'
        reads.mkdir(parents=True)
        for mate in (1, 2):
            (reads / f'{sid}_R{mate}.fastq').write_text('@software-fixture\nACGT\n+\nIIII\n')
    else:
        annotation = root / 'annotation'
        annotation.mkdir(parents=True)
        for suffix, content in {
            'fna': '>contig1\nATGAAATTTTAA\n',
            'faa': '>synthetic_cds\nMKF\n',
            'gff': ('##gff-version 3\n##sequence-region contig1 1 12\n'
                    'contig1\tfixture\tCDS\t1\t12\t.\t+\t0\tID=synthetic_cds;locus_tag=synthetic_cds;transl_table=11\n'),
        }.items():
            (annotation / f'{sid}.{suffix}').write_text(content)
    return {'id': sid, 'path': str(root), 'mode': 'illumina_only'}


def test_regular_registry_source_accepts_generated_immutable_draft(tmp_path, reviewed_source):
    tool, source, review = reviewed_source
    store = approved_workspace(tmp_path, reviewed_source)
    config = yaml.safe_load(template(tool))
    if tool == 'hybas':
        config.update(run_kraken=False, kraken_decision_mode='off', run_busco=False,
                      run_prokka=False, run_multiqc=False, polish_mode='none')
    submitted = yaml.safe_dump(config, sort_keys=False)
    run = create_run(store, tool, synthetic_sample(tmp_path, tool), submitted, cores=1, memory_mb=16000)
    assert run['commit'] == review['commit']
    check_run_files(run)
    validate_launch(store, run['id'], 'dry-run')
    frozen = copy.deepcopy(run)
    config_path = Path(run['folder']) / 'config.yaml'
    yaml_before = config_path.read_bytes()
    workflow_before = tree_digest(Path(run['folder']) / 'workflow')
    assert hashlib.sha256(yaml_before).hexdigest() == run['config_sha256']
    assert all(item.get('sha256') for item in run['inputs'])
    argv = command(store, run, 'dry-run')
    args = argv[argv.index('snakemake') + 1:]
    assert '--dry-run' in args
    args.remove('--use-conda')
    index = args.index('--conda-prefix')
    del args[index:index + 2]
    result = subprocess.run([sys.executable, '-m', 'snakemake', *args], text=True,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=120)
    assert result.returncode == 0, result.stdout[-15000:]
    assert 'Job stats' in result.stdout or 'Job statistics' in result.stdout, result.stdout
    assert config_path.read_bytes() == yaml_before
    assert store.get('runs', run['id']) == frozen
    assert tree_digest(Path(run['folder']) / 'workflow') == workflow_before
    assert not list(Path(run['output']).rglob('report.html'))
    check_run_files(run)
    release_contract.preflight(store, tool, review['commit'], Path(run['folder']) / 'workflow')
