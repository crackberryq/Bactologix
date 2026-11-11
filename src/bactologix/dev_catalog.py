"""Exact development snapshots approved by the official development registry."""
import base64
import copy
import json
from pathlib import Path
from .store import now

REGISTRY = 'repos/crackberryq/Bactologix/contents/compatibility/development-v1.json?ref=development'
BUNDLED = Path(__file__).parent / 'data/development-v1.json'


def validate(doc):
    from .release_contract import reason
    from .catalog import PIPELINES
    if doc.get('schema_version') != 1 or doc.get('channel') != 'dev' or not isinstance(doc.get('builds'), list):
        raise ValueError('Invalid development registry; previous catalog retained.')
    seen=set()
    for row in doc['builds']:
        tool=row.get('tool')
        if tool not in PIPELINES:
            raise ValueError('Development pipeline needs an application adapter update: '+str(tool))
        issue=reason(tool,row)
        if issue or row.get('development') is not True:
            raise ValueError(issue or 'Build is not designated for development.')
        key=(tool,row['commit'])
        if key in seen:raise ValueError('Duplicate development build.')
        seen.add(key)
    return doc


def document(store):
    return validate(store.get('dev_catalog','registry') or json.loads(BUNDLED.read_text()))


def approval(store, tool, commit):
    return next((copy.deepcopy(r) for r in document(store)['builds'] if r['tool']==tool and r['commit']==commit), None)


def versions(store, tool):
    rows=[dict(id='dev:'+r['commit'], kind='development', ref=r['tag'], name=r.get('name',r['tag']),
               commit=r['commit'], url=f"https://github.com/{r['repository']}/tree/{r['commit']}",
               preferred=r.get('preferred',False), prerelease=True, supported=True,
               compatibility_reason='Experimental development snapshot; exact adapter contract and source checksums.')
          for r in document(store)['builds'] if r['tool']==tool]
    recommended=next((r['ref'] for r in rows if r['preferred']), rows[0]['ref'] if rows else None)
    return dict(checkedAt=store.get('dev_catalog','checked_at'),versions=rows,live=bool(store.get('dev_catalog','registry')),
                latest_release=recommended,recommended_release=recommended)


def refresh(store, tool):
    from .catalog import api
    response=api(REGISTRY)
    doc=validate(json.loads(base64.b64decode(response['content'])))
    old=document(store)
    previous={(r['tool'],r['commit']):r for r in old['builds']}
    for row in doc['builds']:
        original=previous.get((row['tool'],row['commit']))
        if original:
            # Display preference may change; immutable compatibility claims may not.
            without_label=lambda r:{k:v for k,v in r.items() if k not in {'name','preferred'}}
            if without_label(original)!=without_label(row):raise ValueError('An existing development build contract changed.')
    # Retain old snapshots so saved runs remain supported after catalog refresh.
    known={(r['tool'],r['commit']) for r in doc['builds']}
    doc['builds'].extend(dict(r,preferred=False) for k,r in previous.items() if k not in known)
    store.put('dev_catalog','registry',doc)
    store.put('dev_catalog','checked_at',now())
    return versions(store,tool)
