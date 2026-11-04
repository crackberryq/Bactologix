"""Application package identity and compatibility, independent of analysis code."""
import re

SAFE = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,79}')


def application(spec):
    schema = spec.get('schema_version')
    if schema not in {'1.0', '2.0'}:
        raise ValueError('Unsupported application specification schema; expected 1.0 or 2.0')
    app = spec.get('application')
    if app is None and schema == '1.0':
        app = dict(id=spec.get('name'), name=spec.get('name'), version=spec.get('version'),
                   description=spec.get('description', 'Legacy reference package'))
    if not isinstance(app, dict) or set(app) != {'id', 'name', 'version', 'description'}:
        raise ValueError('application requires id, name, version and description')
    for key in ('id', 'version'):
        if not isinstance(app[key], str) or not SAFE.fullmatch(app[key]) or '..' in app[key]:
            raise ValueError('Application ' + key + ' must be a safe identifier (1–80 characters)')
    for key, maximum in (('name', 160), ('description', 4000)):
        if not isinstance(app[key], str) or not app[key].strip() or len(app[key]) > maximum or any(ord(c)<32 for c in app[key]):
            raise ValueError('Application ' + key + ' must be nonempty plain text')
    if schema == '2.0' and any(key in spec for key in ('name', 'version', 'description')):
        raise ValueError('Put identity fields inside application, not at the top level')
    return dict(app)


def archive_name(spec):
    app = application(spec)
    return app['id'] + '-' + app['version'] + '.zip'

