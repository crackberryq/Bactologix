import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from bactologix import hybas_refresh, report_refresh
from bactologix.store import Store


@pytest.fixture
def sample(tmp_path, monkeypatch):
    store = Store(tmp_path / 'workspace')
    iso = tmp_path / 'outputs/test'
    source = tmp_path / 'src'
    run = dict(id='run-test', tool='hybas', sample={'id': iso.name}, output=str(iso), commit='a' * 40)
    install = dict(id='hybas-current', ready=True, tool='hybas', source=str(source), controller='/controller',
                   version=dict(ref='new', commit='b' * 40))
    store.put('runs', run['id'], run)
    store.put('installs', install['id'], install)
    store.put('settings', 'active-hybas', install['id'])
    monkeypatch.setattr(report_refresh, 'supported', lambda *args: True)
    monkeypatch.setattr(report_refresh, 'preflight', lambda *args: None)
    monkeypatch.setattr('bactologix.engine.find_conda', lambda *_: '/conda')
    def file(path, content):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    file(iso/'metadata/run_summary.json', json.dumps({'isolate': 'test', 'workflow_version': 'old'}))
    file(iso/'reports/polishing_trajectory/stages.tsv', 'stage\tfasta\ndraft\t../../work/final/assembly.final.fasta\n')
    file(iso/'work/final/assembly.final.fasta', '>test\nACGT\n')
    for name, option in [('hybas_research_report', '--audit-from'), ('polishing_validation', '--render-only'),
                         ('polishing_trajectory', '--render-from')]:
        file(source/'workflow/scripts'/f'{name}.py', '# ' + option)
    file(source/'workflow/lib/hybas_report.py', '')
    file(source/'workflow/envs/polishing_validation.yml', 'dependencies: [bwa, samtools]\n')
    return store, run, iso, source


def test_missing_measurements_require_local_environment_and_explicit_mode(sample):
    store, run, iso, source = sample
    def inspect():
        return report_refresh.inspect(store, run['id'], target='validation')
    original = iso / 'reports/polishing_validation'
    original.mkdir()
    (original/'validation.json').write_text('interrupted attempt')
    plan = inspect()
    assert not plan['available'] and 'environment' in plan['reasons'][0]
    assert any('partial results' in note for note in plan['notes'])
    recipe = source/'workflow/envs/polishing_validation.yml'
    env = store.home/'envs/polishing-validation'/hashlib.sha256(recipe.read_bytes()).hexdigest()
    (env/'bin').mkdir(parents=True)
    (env/'conda-meta').mkdir()
    (env/'conda-meta/history').touch()
    env.with_suffix('.ready').touch()
    for tool in ('python', 'bwa', 'samtools', 'dnadiff'):
        path = env/'bin'/tool
        path.touch()
        path.chmod(0o755)
    plan = inspect()
    assert plan['available'] and plan['mode'] == 'measure-validation'
    assert plan['validation_env'] == str(env)
    (iso/'work/final/assembly.final.fasta').write_text('>test\nGT\n')
    with pytest.raises(ValueError, match='changed'):
        report_refresh.request(store, run['id'], plan['evidence_sha256'], plan['renderer_sha256'],
                               'validation', plan['inputs_sha256'])


def test_research_edition_uses_saved_tables_and_registers_companions(sample):
    store, run, iso, _ = sample
    val = iso/'reports/polishing_validation'
    val.mkdir()
    for file in hybas_refresh.VALIDATION_FILES:
        (val/file).write_text('retained')
    plan = report_refresh.inspect(store, run['id'], target='research')
    assert plan['available'] and plan['mode'] == 'report-only'
    calls = []
    def command(argv, **kwargs):
        calls.append(argv)
        script = Path(argv[7]).stem
        dest = Path(argv[argv.index('--outdir' if script.startswith('hybas') else '--out') + 1])
        dest.mkdir()
        (dest/('hybas_report.html' if script.startswith('hybas') else 'index.html')).write_text('<html></html>')
    ctx = SimpleNamespace(job={'id': 'edition', 'payload': plan}, store=store, command=command,
                          write=lambda _: None, check_cancel=lambda: None)
    report_refresh.execute(ctx)
    assert len(calls) == 3 and all('snakemake' not in args for args in calls)
    assert '--render-only' in calls[0] and '--render-from' in calls[1] and '--audit-from' in calls[2]
    assert {r['report_target'] for r in store.all('reports')} == {'validation','research','trajectory'}
    assert not (iso/'reports/hybas_research').exists()
    assert (val/'validation.json').read_text() == 'retained'


def test_trajectory_edition_links_to_its_own_density_page(sample):
    store, run, iso, source = sample
    validation = iso / 'reports/polishing_validation'
    validation.mkdir()
    for file in hybas_refresh.VALIDATION_FILES:
        (validation / file).write_text('retained')
    script = source / 'workflow/scripts/polishing_trajectory.py'
    script.write_text('# --render-from --validation-dir')
    plan = report_refresh.inspect(store, run['id'], target='trajectory')
    calls = []
    def command(args, cwd):
        calls.append(args)
        dest = Path(args[args.index('--out') + 1])
        dest.mkdir()
        (dest / 'index.html').write_text('<html></html>')
    ctx = SimpleNamespace(job={'id': 'targeted', 'payload': plan}, store=store, command=command,
                          write=lambda _: None, check_cancel=lambda: None)
    report_refresh.execute(ctx)
    assert '--render-only' in calls[0]
    assert Path(calls[1][calls[1].index('--validation-dir') + 1]) == Path(store.get('reports', 'targeted-validation')['path']).parent
    assert store.get('reports','targeted')['report_target'] == 'trajectory'
