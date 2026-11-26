"""User-triggered final-result copies for locally synced isolate folders."""
import os
from pathlib import Path
import re
import shutil
import subprocess
from tempfile import NamedTemporaryFile
from uuid import uuid4
from zipfile import ZipFile, ZIP_DEFLATED

from . import methods, results
from .email_results import write_reports
from .runs import absolute_path, report_candidates, run_status


def inventory(store, run):
    if run_status(run, store.jobs(), False) != 'completed':
        raise ValueError('Choose a completed run before copying its final results.')
    root = Path(run['output'])
    native = {rel for _, rel in report_candidates(store, run) if (root / rel).is_file()}
    editions = {}
    for row in store.all('reports'):
        if row.get('run') == run['id'] and row.get('refresh'):
            base = store.home / 'report-refreshes' / row['producing_job']
            path = Path(row['path'])
            if path.is_relative_to(base) and path.is_file():
                editions.setdefault(base, set()).add(path.relative_to(base).as_posix())
    final = []
    if run['tool'] == 'hybas':
        fasta = 'work/final/assembly.final.fasta'
        if not (root / fasta).is_file() or not (root / fasta).stat().st_size:
            raise ValueError('Final assembly FASTA is missing. Check the run outputs before delivery.')
        final.append(fasta)
        for ext in ('faa', 'gff', 'gbk', 'ffn', 'fna'):
            rel = f"annotation/{run['sample']['id']}.{ext}"
            if (root / rel).is_file():
                final.append(rel)
    if not (final or native or editions):
        raise ValueError('No final results are available to copy.')
    return root, native, editions, final


def preview(store, run):
    _, native, editions, final = inventory(store, run)
    sample = run['sample']
    folder = Path(sample['path'])
    default = '' if sample.get('input_manifest') or folder.is_relative_to(store.home) else str(folder)
    files = sorted(native) + final + ['Methods.txt']
    for base, paths in editions.items():
        files.extend('editions/' + base.name + '/' + p for p in sorted(paths))
    return {'id': run['id'], 'folder': run.get('delivery_folder') or default, 'files': files}


def destination(store, folder):
    if not folder:
        raise ValueError('Choose the local synced isolate folder.')
    if re.match(r'^[A-Za-z]:[\\/]', folder):
        if not shutil.which('wslpath'):
            raise ValueError('This installation needs a locally mounted folder path.')
        translated = subprocess.run(['wslpath', '-u', folder], capture_output=True, text=True, timeout=5)
        if translated.returncode:
            raise ValueError('Could not locate this Windows folder from WSL.')
        folder = translated.stdout.strip()
    parent = absolute_path(folder)
    if not parent.is_dir() or parent.is_relative_to(store.home.resolve()):
        raise ValueError('Choose an existing isolate folder outside the Bactologix workspace.')
    return parent


def deliver(store, run, folder):
    parent = destination(store, folder)
    root, native, editions, final = inventory(store, run)
    target = parent / 'Bactologix Results'
    target.mkdir(exist_ok=True)
    if target.is_symlink():
        raise ValueError('The results directory must be a real folder.')
    filename = f"{run['sample']['id']}-{run['tool']}-{run['id']}-{uuid4().hex[:12]}.zip"
    with NamedTemporaryFile(dir=target, suffix='.partial', delete=False) as temp:
        partial = Path(temp.name)
    try:
        with ZipFile(partial, 'w', ZIP_DEFLATED, allowZip64=True) as archive:
            write_reports(archive, root, native)
            for base, paths in editions.items():
                write_reports(archive, base, paths, 'editions/' + base.name + '/')
            for name in final:
                with results._file(root / name) as source, archive.open(name, 'w', force_zip64=True) as out:
                    before = os.fstat(source.fileno())
                    shutil.copyfileobj(source, out, 1024 * 1024)
                    after = os.fstat(source.fileno())
                    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
                        raise ValueError('An output changed during copying. Review and retry.')
            archive.writestr('Methods.txt', methods.render_text(methods.build(store, run)))
        with ZipFile(partial) as archive:
            if archive.testzip():
                raise ValueError('ZIP verification failed. Retry the copy.')
        partial.rename(target / filename)
    finally:
        partial.unlink(missing_ok=True)
    return str(target / filename)

def after_success(ctx, run_id):
    """Delivery failures are reported independently of workflow completion."""
    run = ctx.store.get('runs', run_id)
    try:
        path = deliver(ctx.store, run, run['delivery_folder'])
        receipt = {'state': 'saved', 'path': path}
        ctx.write('Verified results ZIP saved locally: ' + path)
    except Exception as exc:
        receipt = {'state': 'failed', 'reason': str(exc)}
        ctx.write('Analysis completed. Results copy failed: ' + str(exc) +
                  '. Retry from Results when the isolate folder is available.')
    ctx.store.put('result_deliveries', run_id, {**receipt, 'job_id': ctx.job['id']})
