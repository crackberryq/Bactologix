"""Installation intent and progress, distinct from recorded runner readiness."""

def mode(store, tool, commit):
    items = [i for i in store.all('installs') if i['tool'] == tool]
    selected = next((i for i in items if i['version']['commit'] == commit), None)
    if selected and selected.get('setup_mode') == 'first_install' and not selected.get('setup_completed'):
        return 'first_install'
    if not any(i.get('setup_completed') or (i.get('ready') and not i.get('setup_mode')) for i in items):
        return 'first_install'
    return 'update'


def overview(store, tool):
    items = [i for i in store.all('installs') if i['tool'] == tool]
    if not items:
        return {'state': 'First installation', 'first_install': True,
                'message': 'Install the source, its pinned runner and tool environments. Databases and run validation follow separately.'}
    incomplete = [i for i in items if (i.get('setup_mode') == 'first_install' and not i.get('setup_completed')) or not i.get('ready')]
    if incomplete:
        return {'state': 'First setup incomplete', 'first_install': False,
                'message': 'Resume setup for the downloaded version. Existing environments and outputs are retained.'}
    return {'state': 'Existing installation', 'first_install': False,
            'message': 'Updates retain existing runners and matching tool environments. Missing dependencies can be installed explicitly.'}
