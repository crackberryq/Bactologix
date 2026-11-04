"""Coordinated app/pipeline updates under the existing exclusive update lease."""
from __future__ import annotations
import argparse
import os
import re
import subprocess
import sys
from packaging.version import Version

from . import __version__, maintenance, release_contract, updates
from .catalog import PIPELINES, supported
from .engine import Context, install
from .store import Store, now


def latest_application(store):
    catalog = updates.fetch_releases()
    store.put('app_catalog', 'releases', catalog)
    rows = sorted(catalog['releases'], key=lambda r: Version(r['version']), reverse=True)
    if not rows:
        raise ValueError('No published Bactologix release was found. Retry when GitHub is available.')
    latest = rows[0]
    if Version(latest['version']) > Version(__version__) and not latest['available']:
        raise ValueError('The latest Bactologix release has no verified installable wheel yet. Retry after publication finishes.')
    # A development/newer installed application is never downgraded automatically.
    return latest


def execute(ctx):
    state = {'job': ctx.job['id'], 'status': 'running', 'started': now(),
             'components': {'application': {'status': 'checking'},
                            **{t: {'status': 'pending'} for t in PIPELINES}}}
    ctx.store.put('settings', 'all-update', state)
    try:
        release = latest_application(ctx.store)
        if Version(release['version']) > Version(__version__):
            original = ctx.job['payload']
            try:
                ctx.job['payload'] = {**original, 'release': release}
                updates.install_update(ctx)
            finally:
                ctx.job['payload'] = original
            app_status = 'updated'
        else:
            app_status = 'current'
            ctx.write('Bactologix is already current; checking all pipelines.')
        state['components']['application'] = {'status': app_status, 'version': str(max(Version(__version__), Version(release['version'])))}
        ctx.store.put('settings', 'all-update', state)
        # Import adapters from the installed wheel, never from modules loaded before pip.
        argv = [sys.executable, '-m', 'bactologix.update_all', '--home', str(ctx.store.home),
                '--job', ctx.job['id'], '--lock-fds', ','.join(map(str, ctx.lock_fds))]
        subprocess.run(argv, stdin=subprocess.DEVNULL, stdout=ctx.log, stderr=subprocess.STDOUT,
                       env=dict(os.environ, PYTHONNOUSERSITE='1', PYTHONUNBUFFERED='1'),
                       pass_fds=ctx.lock_fds, check=True)
        completed = ctx.store.get('settings', 'all-update', {})
        if completed.get('job') != ctx.job['id'] or completed.get('status') != 'complete':
            raise RuntimeError('The pipeline updater did not record a complete result. Inspect the update log.')
    except Exception as exc:
        current = ctx.store.get('settings', 'all-update', state)
        if current.get('status') != 'partial':
            current.update(status='failed', finished=now(), error=str(exc))
            for component in current.get('components', {}).values():
                if component.get('status') in {'checking', 'installing'}:
                    component.update(status='failed', error=str(exc))
            ctx.store.put('settings', 'all-update', current)
        raise


def pipeline_updates(ctx):
    state = ctx.store.get('settings', 'all-update')
    if not state or state.get('job') != ctx.job['id']:
        raise ValueError('Missing coordinated update record.')
    failed = []
    for tool, spec in PIPELINES.items():
        row = {'status': 'checking'}
        state['components'][tool] = row
        ctx.store.put('settings', 'all-update', state)
        previous = ctx.store.get('settings', 'active-' + tool)
        original = ctx.job['payload']
        try:
            ctx.write('Checking latest published release: ' + spec['name'])
            catalog = release_contract.refresh(ctx.store, tool)
            releases = sorted((r for r in catalog['versions'] if r['kind'] == 'release'),
                              key=lambda r: r.get('publishedAt') or '', reverse=True)
            if not releases:
                raise ValueError('No published pipeline release found; no moving branch was installed.')
            selected = releases[0]
            # HybAs AWARE 9 is a reviewed immutable snapshot newer than its
            # historical 8.x release tags. Never downgrade it to that older line.
            def numbered(label):
                match = re.search(r'(?i)(?:^|[_-])v?(\d+\.\d+\.\d+(?:(?:a|b|rc)\d+)?)', label)
                return Version(match.group(1)) if match else None
            pinned_version = numbered(spec.get('preferred', ''))
            published_version = numbered(selected['ref'])
            if pinned_version is not None and published_version is not None and pinned_version > published_version:
                selected = {'kind': 'pinned', 'ref': spec['preferred'] + ' @ ' + spec['commit'][:8], 'commit': spec['commit']}
                ctx.write('Using the newer reviewed snapshot: ' + selected['ref'])
            row.update(ref=selected['ref'], commit=selected['commit'])
            if not supported(tool, selected['commit'], ctx.store):
                raise ValueError('Latest release ' + selected['ref'] + ' needs a compatible reviewed runner. Previous installation retained.')
            row['status'] = 'installing'
            ctx.store.put('settings', 'all-update', state)
            ctx.job['payload'] = {'tool': tool, 'version': selected}
            install(ctx)
            key = tool + '-' + selected['commit']
            record = ctx.store.get('installs', key)
            if not record or not record.get('ready'):
                raise RuntimeError('Pipeline runner setup did not complete.')
            # Recheck even previously completed installations: an environment may be missing.
            rows = maintenance.recipes(record)
            ctx.job['payload'] = maintenance.environment_request(ctx.store, key, [r['id'] for r in rows], 'install')
            maintenance.execute_environments(ctx)
            ctx.store.put('settings', 'active-' + tool, key)
            row['status'] = 'ready'
            ctx.write(spec['name'] + ': ' + selected['ref'] + ' ready. Previous sources and runs retained.')
        except Exception as exc:
            ctx.store.put('settings', 'active-' + tool, previous)
            row.update(status='failed', error=str(exc))
            failed.append(tool)
            ctx.write(spec['name'] + ': update incomplete — ' + str(exc))
        finally:
            ctx.job['payload'] = original
            ctx.store.put('settings', 'all-update', state)
    state.update(status='partial' if failed else 'complete', finished=now())
    ctx.store.put('settings', 'all-update', state)
    if failed:
        raise RuntimeError('Update incomplete for: ' + ', '.join(failed) + '. Inspect component results; retry Update everything after resolving the errors.')
    ctx.write('Bactologix and all pipelines are current. No saved runs or databases were rewritten.')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--home', required=True)
    parser.add_argument('--job', required=True)
    parser.add_argument('--lock-fds', required=True)
    args = parser.parse_args()
    store = Store(args.home)
    job = store.job(args.job)
    if not job or job['kind'] != 'app_update' or not job['payload'].get('update_all') or job['status'] != 'running':
        raise SystemExit('This helper requires an active coordinated update job.')
    fds = tuple(int(fd) for fd in args.lock_fds.split(',') if fd)
    if len(fds) != 2:
        raise SystemExit('Both update locks must be inherited.')
    for fd in fds:
        os.fstat(fd)
    ctx = Context(store, job, *fds)
    try:
        pipeline_updates(ctx)
    finally:
        ctx.log.close()
