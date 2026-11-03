import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import yaml
from bactologix import database_builder as b
from bactologix.store import Store

class DatabaseBuilderTests(unittest.TestCase):
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
