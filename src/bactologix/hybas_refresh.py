"""Plan targeted HybAs editions and run the selected installation's renderers."""
import csv
import hashlib
import json
import os
from pathlib import Path

LABELS = {'research': 'HybAs research report', 'trajectory': 'Polishing trajectory',
          'validation': 'Polishing validation'}
PAGES = {'research': 'hybas_research/hybas_report.html', 'trajectory': 'polishing_trajectory/index.html',
         'validation': 'polishing_validation/index.html'}
VALIDATION_FILES = ('validation.json', 'windows.tsv', 'projected_edits.tsv', 'hotspots.tsv', 'stage_to_final.tsv', 'site_support.tsv')


def signature(paths):
    """Include path, target and change time without streaming multi-GB read libraries."""
    stamp = []
    for path in sorted(set(paths)):
        if path.is_file():
            stat = path.stat()
            stamp.append([str(path), str(path.resolve()), stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns])
    return hashlib.sha256(json.dumps(stamp).encode()).hexdigest()


def validation_env(store, install):
    spec = (Path(install['source']) / 'workflow/envs/polishing_validation.yml').read_bytes()
    digest = hashlib.sha256(spec).hexdigest()
    standalone = store.home / 'envs/polishing-validation' / digest
    candidates = [standalone] if standalone.with_suffix('.ready').is_file() else []
    cache = Path(install.get('rule_cache') or store.home / 'envs/hybas')
    for recipe in sorted(cache.glob('*.yaml')):
        prefix = recipe.with_suffix('')
        if prefix.with_suffix('.env_setup_done').is_file() and recipe.read_bytes() == spec:
            candidates.append(prefix)
    for prefix in candidates:
        if (prefix / 'conda-meta/history').is_file() and all(
                os.access(prefix / 'bin' / tool, os.X_OK) for tool in ('bwa', 'samtools', 'dnadiff', 'python')):
            return str(prefix)
    raise ValueError('Validation measurements need the installed Polishing validation environment. Prepare it under Install environments and try again.')


def inputs(iso, trajectory, stages):
    paths = [iso / 'work/final/assembly.final.fasta', iso / 'annotation' / (iso.name + '.gff'),
             iso / 'trimmed/R1.trimmed.fq.gz', iso / 'trimmed/R2.trimmed.fq.gz', stages]
    paths.extend(trajectory.glob('*.edits.tsv'))
    if stages.is_file():
        with stages.open() as stream:
            for row in csv.DictReader(stream, delimiter='\t'):
                stage = row['stage']
                if not stage or Path(stage).name != stage or stage in ('.', '..'):
                    raise ValueError('Invalid stage in the retained trajectory table.')
                paths.extend([trajectory / row['fasta'], trajectory / stage / 'proteins.faa'])
    return paths


def configure(store, run, install, plan):
    target = plan['target']
    iso = Path(run['output']).resolve()
    if iso.name != run['sample']['id']:
        raise ValueError('The isolate output directory does not match the selected sample.')
    scripts = Path(install['source']) / 'workflow/scripts'
    required = {'research': ('hybas_research_report.py', b'--audit-from'),
                'trajectory': ('polishing_trajectory.py', b'--render-from'),
                'validation': ('polishing_validation.py', b'--render-only')}
    for name in (LABELS if target == 'research' else [target]):
        module, option = required[name]
        if not (scripts / module).is_file() or option not in (scripts / module).read_bytes():
            raise ValueError('Update and select the HybAs installation that supports regenerating this report.')
    reports = iso / 'reports'
    trajectory = reports / 'polishing_trajectory'
    stages = trajectory / 'stages.tsv'
    native = reports / 'polishing_validation'
    retained = inputs(iso, trajectory, stages)
    validation_source = signature(retained)
    validation = native
    missing = [name for name in VALIDATION_FILES if not (native / name).is_file()]
    if missing:
        # Retrospective measurements belong to the same run and exact retained input snapshot.
        prior = sorted(store.all('report_refreshes'), key=lambda r: str(r.get('created_at', '')), reverse=True)
        for record in prior:
            if (record.get('run_id') == run['id'] and record.get('mode') == 'measure-validation'
                    and record.get('validation_source') == validation_source):
                candidate = Path(record['path']).parent
                if all((candidate / file).is_file() for file in VALIDATION_FILES):
                    validation = candidate
                    missing = []
                    break
    if missing and (native / 'validation.json').is_file():
        plan['notes'].append('The original validation folder has partial results. Missing files include ' + ', '.join(missing) + '.')
    ready_for_validation = stages.is_file() and retained[0].is_file()
    saved = not missing and ready_for_validation
    if target == 'validation' and not saved:
        if not ready_for_validation:
            raise ValueError('Validation needs the retained stages.tsv and final assembly FASTA.')
        plan['validation_env'] = validation_env(store, install)
        plan['mode'] = 'measure-validation'
        plan['notes'].append('Generate missing validation measurements from the retained stage assemblies. This runs local alignments and maps available Illumina reads. Temporary and published files use workspace disk space.')
    elif target == 'trajectory' and not stages.is_file():
        raise ValueError('The retained trajectory stages.tsv is missing. A report edition needs measured polishing stages.')
    if target == 'research' and not saved:
        plan['notes'].append('Validation measurements are unavailable. Use Regenerate report next to Polishing validation to measure them, then regenerate this research report to include them.')
    paths = retained + list(trajectory.glob('*.tsv')) + list(trajectory.glob('*.json')) + [trajectory / 'trajectory.svg']
    if target in ('validation', 'research') and saved:
        paths.extend(validation.glob('*.tsv'))
        paths.extend(validation.glob('*.json'))
    if target == 'research':
        for pattern in ('metadata/*.tsv', 'metadata/*.json', 'reports/hybas_research/aware*.json',
                        'reports/quast/report.txt', 'reports/busco/**/short_summary*.txt',
                        'reports/cds_audit/busco/final/**/short_summary*.txt',
                        'reports/*/qc_steps/*/report.txt', 'reports/intermediate_qc_FULL/*/report.txt',
                        'reports/FINAL_AUDIT_RESULTS/QC_ASSEMBLY/qc_steps/*/report.txt'):
            paths.extend(iso.glob(pattern))
    plan.update(target_label=LABELS[target], validation_source=validation_source, inputs_sha256=signature(paths),
                trajectory=str(trajectory) if stages.is_file() else '', validation=str(validation) if saved else '')


def render(ctx, run, plan, edition, runner):
    target = plan['target']
    iso = Path(run['output']).resolve()
    scripts = Path(plan['source']) / 'workflow/scripts'
    edition.mkdir()
    pages = {}
    def command(name, script, args, prefix=None):
        path = edition / PAGES[name]
        ctx.command([*runner(prefix or plan['controller']), str(scripts / script), *map(str, args)], cwd=edition)
        if not path.is_file() or not path.stat().st_size:
            raise ValueError('The renderer did not produce ' + str(path))
        pages[name] = path

    if target in ('research', 'trajectory') and plan['trajectory']:
        command('trajectory', 'polishing_trajectory.py', ['--render-from', plan['trajectory'], '--out',
                                                         edition / 'polishing_trajectory'])
    if target in ('research', 'validation') and (plan['validation'] or plan['mode'] == 'measure-validation'):
        args = ['--isolate-dir', iso, '--out', edition / 'polishing_validation']
        if plan['validation']:
            args += ['--render-only', '--tables', plan['validation']]
        command('validation', 'polishing_validation.py', args, plan.get('validation_env'))
    if target == 'research':
        args = ['--base-dir', iso.parent, '--isolate', iso.name, '--outdir', edition / 'hybas_research',
                '--audit-from', iso / 'reports/hybas_research',
                '--trajectory-dir', edition / 'polishing_trajectory', '--validation-dir', edition / 'polishing_validation']
        command('research', 'hybas_research_report.py', args)
    return pages
