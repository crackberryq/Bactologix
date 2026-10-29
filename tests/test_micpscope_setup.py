"""Software-only contract checks for MICP Scope and first-install handling."""
import csv
import io
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from bactologix.catalog import PIPELINES, versions, supported
from bactologix.controllers import controller_requirements
from bactologix.engine import prepare_first_install
from bactologix import maintenance, setup_state, run_config
from bactologix.runs import create_run, check_run_files, template, report_candidates, command
from bactologix.store import Store
from bactologix.ui_backend import snapshot, installation_status


class MICPscopeTests(unittest.TestCase):
    def test_named_micpscope_release_preserves_supported_snapshots(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Store(directory)
            rows = versions(store, 'micpscope')['versions']
            self.assertTrue(rows[0]['name'].startswith('micpscope-v0.1.0a4'))
            self.assertNotIn('main', rows[0]['ref'])
            for commit in PIPELINES['micpscope']['previous_commits']:
                self.assertTrue(supported('micpscope', commit))
                self.assertTrue(any(row['commit'] == commit for row in rows))

    def test_fresh_workspace_all_three_pipelines(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Store(directory)
            view = snapshot(store)
            self.assertEqual(set(view['pipelines']), {'hybas', 'basis', 'micpscope'})
            for tool in view['pipelines']:
                self.assertTrue(view['setup_overview'][tool]['first_install'])
                self.assertEqual(setup_state.mode(store, tool, 'a'*40), 'first_install')
                run_config.editor(tool, template(tool))
            self.assertEqual(view['databases']['micpscope']['rows'], [])

    def test_setup_failure_remains_first_install_and_restores_payload(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Store(directory)
            record = {'id': 'test', 'tool': 'micpscope', 'source': directory,
                      'version': {'commit': PIPELINES['micpscope']['commit']},
                      'ready': True, 'setup_mode': 'first_install'}
            store.put('installs', record['id'], record)
            class Context:
                def write(self, message): pass
            ctx = Context(); ctx.store = store; ctx.job = {'payload': {'tool': 'micpscope'}}
            original = ctx.job['payload']
            with patch.object(maintenance, 'recipes', return_value=[{'id': 'properties'}]), \
                 patch.object(maintenance, 'environment_request', return_value={'action': 'install'}), \
                 patch.object(maintenance, 'execute_environments', side_effect=RuntimeError('deployment failed')):
                with self.assertRaises(RuntimeError): prepare_first_install(ctx, record)
            self.assertIs(ctx.job['payload'], original)
            self.assertEqual(setup_state.mode(store, 'micpscope', record['version']['commit']), 'first_install')
            self.assertNotIn('setup_completed', store.get('installs', 'test'))
            self.assertEqual(installation_status(record, [])['setup_state'], 'Setup incomplete')
            with patch.object(maintenance, 'recipes', return_value=[{'id': 'properties'}]), \
                 patch.object(maintenance, 'environment_request', return_value={'action': 'install'}), \
                 patch.object(maintenance, 'execute_environments') as prepare:
                prepare_first_install(ctx, record)
            prepare.assert_called_once()
            self.assertEqual(store.get('installs', 'test')['setup_phase'], 'complete')
            self.assertEqual(setup_state.mode(store, 'micpscope', 'b'*40), 'update')

    def test_download_only_is_not_an_existing_ready_install(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Store(directory)
            for tool in PIPELINES:
                store.put('installs', tool, {'id': tool, 'tool': tool, 'version': {'commit': 'a'*40}, 'ready': False})
                self.assertEqual(setup_state.mode(store, tool, 'a'*40), 'first_install')
                store.put('installs', tool, {'id': tool, 'tool': tool, 'version': {'commit': 'a'*40}, 'ready': True})
                self.assertEqual(setup_state.mode(store, tool, 'b'*40), 'update')

    def test_successful_update_remains_existing_without_older_records(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Store(directory)
            item = {'id': 'update', 'tool': 'micpscope', 'ready': True,
                    'setup_mode': 'update', 'version': {'commit': 'a'*40}}
            store.put('installs', item['id'], item)
            self.assertEqual(setup_state.mode(store, 'micpscope', 'b'*40), 'update')
            item['ready'] = False
            store.put('installs', item['id'], item)
            self.assertEqual(setup_state.mode(store, 'micpscope', 'a'*40), 'update')
            self.assertEqual(setup_state.overview(store, 'micpscope')['state'], 'Setup incomplete')

    def test_frozen_protein_inputs_and_snake_command(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); store = Store(root/'workspace')
            source = root/'source'; source.mkdir()
            (source/'MICPscope.smk').write_text('rule all:\n    input: []\n')
            (source/'environment.yml').write_text('dependencies: [python=3.11, snakemake-minimal=9.6.2]\n')
            subprocess.run(['git','init',str(source)],check=True,capture_output=True)
            subprocess.run(['git','-C',str(source),'add','.'],check=True)
            subprocess.run(['git','-C',str(source),'-c','user.name=Test','-c','user.email=test@example.invalid','commit','-m','Fixture'],check=True,capture_output=True)
            sha=subprocess.check_output(['git','-C',str(source),'rev-parse','HEAD'],text=True).strip()
            controller=root/'runner';controller.mkdir()
            item={'id':'micp-fixture','tool':'micpscope','source':str(source),'controller':str(controller),'ready':True,'version':{'commit':sha,'ref':'fixture'}}
            store.put('installs',item['id'],item);store.put('settings','active-micpscope',item['id'])
            annotation=root/'sample/annotation';annotation.mkdir(parents=True)
            (annotation/'demo.faa').write_text('>synthetic\nAVIL\n')
            (annotation/'demo.evidence.csv').write_text('sample_id,subject_id,evidence_type,property,value,unit,source,notes\n')
            sample={'id':'demo','path':str(annotation.parent),'mode':'hybrid'}
            spec={**PIPELINES['micpscope'],'commit':sha}
            with patch.dict(PIPELINES,{'micpscope':spec}):
                run=create_run(store,'micpscope',sample,template('micpscope'),1,2048)
                check_run_files(run)
                self.assertEqual(len(run['inputs']),2)
                rows=list(csv.DictReader(io.StringIO((Path(run['folder'])/'samples.tsv').read_text()),delimiter='\t'))
                self.assertEqual(rows[0]['proteins'],str(Path(run['output'])/'annotation/demo.faa'))
                self.assertTrue(rows[0]['evidence'].endswith('.evidence.csv'))
                with patch('bactologix.engine.find_conda',return_value='/conda'):
                    argv=command(store,run,'prepare')
                    self.assertIn('--conda-create-envs-only',argv)
                    self.assertTrue(any(arg.endswith('MICPscope.smk') for arg in argv))
                self.assertEqual(report_candidates(store,run),[('MICP Scope report','micpscope/report.html')])
                (annotation/'demo.evidence.csv').write_text('changed')
                with self.assertRaises(ValueError):check_run_files(run)

    def test_missing_recorded_runner_requires_setup(self):
        with tempfile.TemporaryDirectory() as directory:
            record = {'id': 'missing', 'tool': 'micpscope', 'source': directory,
                      'version': {'commit': PIPELINES['micpscope']['commit']},
                      'controller': str(Path(directory)/'missing-runner'), 'ready': True}
            status = installation_status(record, [])
            self.assertEqual(status['setup_state'], 'Setup incomplete')
            self.assertIn('runner folder is missing', status['setup_message'])

    def test_native_micpscope_workflow(self):
        import os
        import json
        import sys
        import shutil
        source_path = os.environ.get('BACTOLOGIX_MICPSCOPE_SOURCE')
        if not source_path:
            self.skipTest('Pinned MICP Scope checkout not supplied')
        source = Path(source_path).resolve()
        sha = subprocess.check_output(['git', '-C', str(source), 'rev-parse', 'HEAD'], text=True).strip()
        self.assertEqual(sha, PIPELINES['micpscope']['commit'])
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workflow = root / 'workflow'
            shutil.copytree(source, workflow, ignore=shutil.ignore_patterns('.git', '__pycache__'))
            cmd = [sys.executable, '-m', 'snakemake', '--snakefile', str(workflow/'MICPscope.smk'), '--directory', str(workflow), '--cores', '1']
            for flags in (['--dry-run'], [], ['--dry-run']):
                result = subprocess.run(cmd + flags, capture_output=True, text=True, timeout=120)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            output = workflow/'results/demo/micpscope'
            data = json.loads((output/'results.json').read_text())
            self.assertEqual(data['sample_id'], 'demo')
            self.assertEqual(data['proteins'][1]['status'], 'not_calculated')
            self.assertTrue((output/'report.html').is_file())
            self.assertTrue((output/'benchmarks/properties.tsv').is_file())


if __name__ == '__main__':
    unittest.main()

