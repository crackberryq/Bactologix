import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import yaml
from bactologix import database_builder as b
from bactologix.store import Store

class DatabaseBuilderTests(unittest.TestCase):
    def test_bundled_starter_resolves_installed_paths(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);store=Store(root/'app');source=root/'source'
            refs=source/'database_builder/references';refs.mkdir(parents=True)
            (refs/'proteins.faa').write_text('>p\nACDE\n')
            spec={'schema_version':'1.0','name':'starter','version':'1','proteins':[{'id':'p','path':'proteins.faa','record':'p'}],'annotations':{'literature_records':[{'id':'evidence'}]}}
            path=refs/'micp-starter.yaml';path.write_text(yaml.safe_dump(spec));original=path.read_text()
            store.put('installs','micp',{'id':'micp','source':str(source)})
            store.put('settings','active-micpscope','micp')
            starter=b.starter(store)
            loaded,assets=b.validate(store,starter['text'])
            self.assertEqual(loaded['proteins'][0]['path'],str(refs/'proteins.faa'))
            self.assertEqual(starter['literature_records'],1)
            self.assertEqual(path.read_text(),original)
            self.assertEqual(len(assets),1)

    def test_snapshot_preserves_inputs(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);store=Store(root/'app');source=root/'source';source.mkdir();(source/'DatabaseBuilder.smk').write_text('rule all:\n    input: []\n')
            protein=root/'p.faa';protein.write_text('>p\nACDE\n')
            item={'id':'micp','source':str(source),'tool':'micpscope','ready':True,'version':{'commit':'a'*40}}
            store.put('installs','micp',item);store.put('settings','active-micpscope','micp')
            spec={'schema_version':'1.0','name':'test','version':'1','proteins':[{'id':'p','path':str(protein),'record':'p'}]}
            with patch.object(b.maintenance,'installation',return_value=item),patch.object(b.maintenance,'check_deployment_path'):
                result=b.request(store,yaml.safe_dump(spec))
            frozen=yaml.safe_load((Path(result['folder'])/'references.yaml').read_text())['proteins'][0]['path']
            protein.write_text('changed')
            self.assertEqual(Path(frozen).read_text(),'>p\nACDE\n')
            self.assertNotEqual(frozen,str(protein))
            self.assertEqual(store.get('database_builds',result['id'])['status'],'queued')
    def test_no_empty_or_unsafe_build(self):
        with tempfile.TemporaryDirectory() as td:
            store=Store(td)
            for text in ('[]','schema_version: "1.0"\nproteins: []','schema_version: "1.0"\nname: ../bad\nversion: "1"\nproteins: [{}]'):
                with self.assertRaises(ValueError):b.validate(store,text)
