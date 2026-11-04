import json
from pathlib import Path
import pytest
import yaml
from bactologix.application_package import application
from bactologix.database_builder import validate
from bactologix.databases import panel
from bactologix.store import Store


def test_custom_name_and_selected_database_are_metadata_driven(tmp_path):
    store=Store(tmp_path/'app');protein=tmp_path/'p.faa';protein.write_text('>p\nACDE\n')
    app=dict(id='custom',name='Custom engineering',version='2.0',description='A different application')
    spec=dict(schema_version='2.0',application=app,proteins=[dict(id='p',path=str(protein),record='p')])
    parsed,_=validate(store,yaml.safe_dump(spec));assert application(parsed)==app
    database=tmp_path/'database';database.mkdir()
    (database/'reference-manifest.json').write_text(json.dumps({'schema_version':'1.0','specification':spec}))
    store.put('configs','micpscope',yaml.safe_dump({'reference_database':str(database)}))
    row=panel(store,'micpscope')['rows'][0]
    assert row['name']=='Custom engineering · 2.0'
    assert row['application']==app and row['description']==app['description']
    spec['application']['id']='../escape'
    with pytest.raises(ValueError):validate(store,yaml.safe_dump(spec))


def test_legacy_package_still_readable():
    assert application({'schema_version':'1.0','name':'old-name','version':'1'})['name']=='old-name'
    with pytest.raises(ValueError):application({'schema_version':'9.0'})
