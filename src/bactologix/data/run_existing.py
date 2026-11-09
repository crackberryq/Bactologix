"""Standalone entry point executed by the pipeline's own Python interpreter."""
import os
from pathlib import Path
import runpy
import sys


def main():
    if sys.argv[1:2] != ['snakemake']:
        raise SystemExit('Expected the Snakemake command.')
    guard = Path(__file__).parent / 'env_guard'
    os.environ['BACTOLOGIX_EXISTING_ENVS_ONLY'] = '1'
    os.environ['PYTHONPATH'] = str(guard) + (os.pathsep + os.environ['PYTHONPATH'] if os.environ.get('PYTHONPATH') else '')
    try:
        runpy.run_path(str(guard / 'sitecustomize.py'))['enforce_existing_environments']()
    except Exception as exc:
        raise SystemExit('Bactologix environment guard failed: ' + str(exc)) from exc
    from snakemake.cli import main as snakemake_main
    observer, receipt = None, None
    if os.environ.get('BACTOLOGIX_RUNTIME_CONTEXT'):
        try:
            from snakemake import __version__ as snakemake_version
            observer = runpy.run_path(str(Path(__file__).parent / 'runtime_receipt.py'))
            receipt = observer['begin'](snakemake_version)
        except Exception as exc:
            print('Controller runtime observation unavailable: ' + str(exc), file=sys.stderr)
    sys.argv = sys.argv[1:]
    def finish(error=None):
        if receipt is not None:
            try:
                observer['finish'](receipt, error)
            except Exception as exc:
                print('Controller runtime observation unavailable: ' + str(exc), file=sys.stderr)
    try:
        snakemake_main()
    except BaseException as exc:
        finish(exc)
        raise
    else:
        finish()


if __name__ == '__main__':
    main()
