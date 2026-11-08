#!/usr/bin/env python3
"""Build a paired testing bundle from immutable Git commits; never publish or activate it."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import tomllib


def git(repo, *args):
    return subprocess.check_output(['git','-C',str(repo),*args],text=True).strip()


def export(repo, commit, prefix, output):
    if not re.fullmatch(r'[0-9a-f]{40}',commit) or git(repo,'rev-parse',commit+'^{commit}') != commit:
        raise ValueError('Use an existing immutable 40-character commit SHA')
    subprocess.run(['git','-C',str(repo),'archive','--format=tar.gz','--prefix='+prefix+'/', '-o',str(output),commit],check=True)


def package(hybas, hybas_commit, app, app_commit, output):
    output=Path(output).resolve()
    if output.exists():raise ValueError('Output exists; choose a new testing bundle directory')
    output.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.testing-pair-',dir=output.parent) as temporary:
        stage=Path(temporary)/'bundle';stage.mkdir()
        export(hybas,hybas_commit,'hybas',stage/'hybas-source.tar.gz')
        export(app,app_commit,'bactologix',stage/'bactologix-source.tar.gz')
        source=Path(temporary)/'source';source.mkdir()
        with tarfile.open(stage/'bactologix-source.tar.gz') as archive:
            # Git source archives contain only tracked files. The data filter also
            # rejects escaping paths and unsafe links during extraction.
            archive.extractall(source,filter='data')
        subprocess.run([sys.executable,'-m','build','--wheel','--outdir',str(stage)],cwd=source/'bactologix',check=True)
        version=tomllib.loads((source/'bactologix/pyproject.toml').read_text())['project']['version']
        doc=dict(schema='bactologix-testing-pair-v1',status='development testing; not a release approval',
            hybas=dict(repository='crackberryq/Bactomics_HybAs',commit=hybas_commit,tree=git(hybas,'rev-parse',hybas_commit+'^{tree}')),
            bactologix=dict(repository='crackberryq/Bactologix',commit=app_commit,tree=git(app,'rev-parse',app_commit+'^{tree}'),version=version),
            files=[dict(path=p.name,sha256=hashlib.sha256(p.read_bytes()).hexdigest(),size=p.stat().st_size) for p in sorted(stage.iterdir())])
        (stage/'testing-pair.json').write_text(json.dumps(doc,indent=2)+'\n')
        (stage/'README.txt').write_text('Bactologix / HybAs paired development testing bundle\n\n'
            'Verify SHA256SUMS before using these files. Source commits are frozen in testing-pair.json.\n'
            'Install the application wheel in a separate Python virtual environment using pip install <wheel filename>.\n'
            'Use a separate Bactologix --home directory for testing. Do not point it at a production workspace.\n'
            'Extract hybas-source.tar.gz for native workflow testing; follow docs/TESTING_FREEZE.md and docs/INSTALL.md.\n'
            'This package does not install Conda environments or databases automatically.\n'
            'Managed execution of this new HybAs commit still requires a successful exact-commit adapter review and an official prerelease compatibility entry. This bundle does not bypass that gate.\n')
        (stage/'SHA256SUMS').write_text(''.join(hashlib.sha256(p.read_bytes()).hexdigest()+'  '+p.name+'\n' for p in sorted(stage.iterdir())))
        stage.rename(output)
    return doc


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('hybas','hybas-commit','app','app-commit','output'):parser.add_argument('--'+name,required=True)
    args=vars(parser.parse_args());print(json.dumps(package(**args),indent=2))
