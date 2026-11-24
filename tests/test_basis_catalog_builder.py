import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from bactologix import basis_catalog_builder as builder
from bactologix.store import Store


class BasisCatalogTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.store = Store(Path(temp.name) / 'workspace')
        self.store.channel = 'dev'
        self.source = self.store.home / 'sources' / 'basis'
        item = {'tool': 'basis', 'id': 'b', 'source': str(self.source), 'version': {'commit': 'a' * 40}}
        self.store.put('installs', 'b', item)
        self.store.put('settings', 'active-basis', 'b')
        self.spec = Path(temp.name) / 'my catalog.json'
        self.spec.write_text('{}')
        self.contract = {'files_sha256': {builder.SCRIPT: '1' * 64, builder.TEMPLATE: '2' * 64}}
        for patcher in (patch.object(builder, 'approval', return_value=self.contract),
                        patch.object(builder, '_source', return_value=self.source)):
            patcher.start()
            self.addCleanup(patcher.stop)

    def execute(self, payload, jid, receipt):
        ctx = Mock(store=self.store, job={'id': jid, 'payload': payload})

        def run(args, cwd, stdout):
            stdout.write(json.dumps(receipt).encode())
        ctx.command.side_effect = run
        builder.execute(ctx)
        return ctx.command.call_args.args[0]

    def test_submit_build_and_verify_use_pinned_spec_and_preserve_approved_package_identity(self):
        request = builder.request(self.store, 'build', str(self.spec))
        dest = self.store.home / 'resources/basis-catalogs/job-1'
        receipt = dict(schema='basis-reference-catalog-status-v1', name='Archive', version='1',
                       approved=1, pending=1, excluded=0, placement_registry=None,
                       output=str(dest), catalog_sha256='a' * 64)
        self.store.put('settings', 'active-basis', 'other')
        args = self.execute(request, 'job-1', receipt)
        self.assertEqual(args[-5:], ['build', str(self.spec), str(dest), '--expect-spec-sha256',
                                    hashlib.sha256(b'{}').hexdigest()])
        self.store.put('settings', 'active-basis', 'b')
        verify = builder.request(self.store, 'verify', build_id='job-1')
        self.assertEqual(self.execute(verify, 'job-2', receipt)[-2:], ['verify', str(dest)])
        with self.assertRaisesRegex(ValueError, 'identity differs'):
            self.execute(verify, 'job-3', {**receipt, 'catalog_sha256': 'b' * 64})
        self.assertIsNone(self.store.get(builder.RESULTS, 'job-3'))

    def test_legacy_or_missing_build_cannot_be_presented_as_usable(self):
        with self.assertRaisesRegex(ValueError, 'completed catalog build'):
            builder.request(self.store, 'verify', build_id='job-missing')
        with patch.object(builder, 'approval', return_value={}):
            with self.assertRaisesRegex(ValueError, 'Update and activate'):
                builder.request(self.store, 'check', str(self.spec))
        self.store.channel = 'stable'
        self.assertIsNone(builder.panel(self.store))
        with self.assertRaisesRegex(ValueError, 'Activate a BaSIS Dev'):
            builder.request(self.store, 'check', str(self.spec))

    def test_controls_are_wired_to_setup_and_jobs(self):
        directory = Path(__file__).resolve().parents[1] / 'src/bactologix'
        ui = (directory / 'ui/workspace.js').read_text()
        self.assertIn("t==='basis'?basisCatalogBuilder()", ui)
        self.assertIn("request('basis_catalog_job'", ui)
        self.assertIn("enqueue(store, 'basis_catalog', payload)", (directory / 'ui_backend.py').read_text())

    def test_selected_registry_tracks_consumed_bytes_and_retains_other_settings(self):
        dest = self.store.home / 'resources/basis-catalogs/job-1'
        (dest / 'files').mkdir(parents=True)
        reg = dest / 'files/ref-study.json'
        reg.write_bytes(b'{}')
        data = {'records': [{'kind': 'placement_registry', 'status': 'approved', 'path': 'files/ref-study.json',
                             'sha256': hashlib.sha256(b'{}').hexdigest()}]}
        manifest = dest / 'catalog.json'
        manifest.write_text(json.dumps(data))
        self.store.put(builder.RESULTS, 'job-1', {'action': 'build', 'output': str(dest),
                       'placement_registry': str(reg), 'catalog_sha256': hashlib.sha256(manifest.read_bytes()).hexdigest()})
        deep = {'enabled': True, 'family_placement': False}
        identities = builder.select_registry(self.store, 'job-1', deep)
        self.assertEqual([r['path'] for r in identities], [str(manifest), str(reg)])
        self.assertEqual(deep, {'enabled': True, 'family_placement': False,
                                'placement_registry': str(reg), 'placement_allow_download': False})
        with self.assertRaisesRegex(ValueError, 'conflicts'):
            builder.select_registry(self.store, 'job-1', {'placement_registry': '/other'})
        reg.write_text('modified')
        with self.assertRaisesRegex(ValueError, 'checksum'):
            builder.select_registry(self.store, 'job-1', {})

    def test_explicit_citation_lookup_downloads_the_returned_pending_draft(self):
        from types import SimpleNamespace
        doc = {'records': [{'status': 'pending'}]}
        with patch.object(builder.subprocess, 'run', return_value=SimpleNamespace(returncode=0, stdout=json.dumps(doc))) as run:
            result = builder.citation_draft(self.store, '10.1234/example')
        self.assertEqual(run.call_args.args[0][-3:], ['draft-doi', '--', '10.1234/example'])
        self.assertEqual(json.loads(result['text']), doc)
        self.assertFalse(self.store.all(builder.RESULTS))
