"""Run-local setup review: input declarations and source-labelled defaults.

This does not inspect sequence quality, infer biological suitability, or launch
tools. Applying a displayed default compiles a new unsaved editor document.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import stat

from . import run_config, setup_advice_source
from .catalog import PIPELINES
from .runs import parse_config, valid_id

RULE_VERSION = 1
MAX_FILES = 5000
FASTQ = re.compile(r"\.(fastq|fq)(\.gz)?$", re.I)
MISSING = object()


def _get(mapping, name, fallback=MISSING):
    value = mapping
    for part in name.split('.'):
        if not isinstance(value, dict) or part not in value:
            return fallback
        value = value[part]
    return value


def _file(path):
    """File availability only; these observations are not content identities."""
    try:
        info = path.stat()
        identity = [str(path), info.st_dev, info.st_ino, info.st_size,
                    info.st_mtime_ns, info.st_ctime_ns]
        if not stat.S_ISREG(info.st_mode):
            return 'unreadable', identity
        if not os.access(path, os.R_OK):
            return 'unreadable', identity
        return ('present' if info.st_size else 'empty'), identity
    except FileNotFoundError:
        return 'missing', [str(path), 'missing']
    except (OSError, RuntimeError, ValueError):
        return 'unreadable', [str(path), 'unreadable']


def _input_inventory(sample, tool):
    root, sid = Path(sample['path']), sample['id']
    rows, identities, platforms, issues = [], [], set(), []

    def files(label, paths, required=True):
        observed = [_file(path) for path in paths]
        identities.extend(identity for _, identity in observed)
        good = sum(status == 'present' for status, _ in observed)
        status = 'present' if observed and good == len(observed) else 'missing'
        if observed and good != len(observed):
            status = next(s for s, _ in observed if s != 'present')
        detail = f'{good} of {len(observed)} file(s) present and nonempty.' if observed else 'No matching files found.'
        if not required:
            detail += ' Optional input; requirements depend on selected features.'
        rows.append({'label': label, 'status': status, 'detail': detail})
        if required and status != 'present':
            issues.append(f'{label}: required input files are missing, empty or unreadable.')

    if tool == 'hybas' and sample.get('input_manifest'):
        try:
            # Present only in the Dev app. Use its declaration validator, not
            # filenames, to determine which read platforms were registered.
            from .hybas_manifest_schema import validate_manifest
            doc = validate_manifest({'schema_version': 1, 'samples': [sample['input_manifest']]}, base_dir=root)
            entry = doc['manifest']['samples'][0]
            if entry['sample_id'] != sid:
                raise ValueError('Manifest sample identity does not match the registration.')
            for lib in entry['libraries']:
                platform = lib['platform']
                platforms.add(platform)
                paths = [Path(group[key]) for group in lib['read_groups']
                         for key in ('r1', 'r2', 'reads') if key in group]
                if len(paths) > MAX_FILES:
                    raise ValueError('Too many declared files for this lightweight review.')
                files(f"{platform} · {lib['library_id']}", paths)
            issues.append('Manifest declarations checked; native validation still checks the selected input strategy and preprocessing.')
        except (ImportError, ValueError, KeyError, TypeError) as exc:
            rows.append({'label': 'Input manifest', 'status': 'unchecked', 'detail': str(exc)})
            issues.append('Manifest inputs could not be reviewed with this app version.')
            platforms.clear()
    elif tool == 'hybas':
        modes = {'hybrid': ('illumina', 'ont'), 'illumina_only': ('illumina',), 'ont_only': ('ont',)}
        for platform in modes.get(sample.get('mode'), ()):
            platforms.add(platform)
            folder = root / 'raw' / ('nanopore' if platform == 'ont' else 'illumina')
            paths = []
            try:
                with os.scandir(folder) as entries:
                    for count, entry in enumerate(entries):
                        if count >= MAX_FILES:
                            raise ValueError('Folder exceeds the lightweight review limit of 5,000 entries.')
                        if FASTQ.search(entry.name):
                            paths.append(Path(entry.path))
                files('ONT reads' if platform == 'ont' else 'Illumina reads', sorted(paths))
            except (OSError, ValueError) as exc:
                rows.append({'label': platform + ' reads', 'status': 'unchecked', 'detail': str(exc)})
                identities.append([str(folder), str(exc)])
                issues.append(platform + ' input folder could not be reviewed.')
        if not platforms:
            issues.append('The registered read mode is not supported by this input review.')
    else:
        required = ('faa',) if tool == 'micpscope' else ('fna', 'faa', 'gff')
        for suffix in required:
            files('Annotation .' + suffix, [root / 'annotation' / (sid + '.' + suffix)])
        for suffix in (('gff', 'fna') if tool == 'micpscope' else ('gbk',)):
            path = root / 'annotation' / (sid + '.' + suffix)
            if suffix == 'fna' and sample.get('micp_handoff'):
                path = root / 'work/final/assembly.final.fasta'
            files('Annotation .' + suffix, [path], required=False)
    return ({'id': sid, 'mode': sample.get('mode', 'Not recorded'), 'inputs': rows},
            identities, platforms, issues)


def _request(store, payload):
    tool = payload.get('tool')
    if tool not in PIPELINES:
        raise ValueError('Choose an installed pipeline to review.')
    ids = payload.get('ids')
    if not isinstance(ids, list) or not 1 <= len(ids) <= 500 or any(not isinstance(sid, str) for sid in ids):
        raise ValueError('Select between 1 and 500 registered samples for setup advice.')
    samples = []
    for sid in sorted(set(ids)):
        valid_id(sid)
        sample = store.get('samples', sid)
        if not sample or sample.get('id') != sid:
            raise ValueError('A selected sample is no longer registered. Refresh the sample list.')
        samples.append(sample)
    config = payload.get('config')
    if not isinstance(config, dict) or config.get('tool') != tool or not isinstance(config.get('text'), str):
        raise ValueError('Review the current settings for this pipeline first.')
    text = run_config.compile_config(tool, config['text'], config.get('changes'))
    from .hardware import run_defaults
    budgets = run_defaults(store, tool)
    cores, memory = payload.get('cores', budgets['cores']), payload.get('memory_mb', budgets['memory_mb'])
    if type(cores) is not int or type(memory) is not int or not 1 <= cores <= 512 or not 512 <= memory <= 2000000:
        raise ValueError('Choose a supported whole-number CPU and memory budget.')
    return tool, samples, text, cores, memory


def review(store, payload):
    tool, samples, text, cores, memory = _request(store, payload)
    cfg = parse_config(text)
    source = setup_advice_source.load(store, tool)
    defaults = source.get('defaults', {}) if source.get('verified') else {}
    profile = source.get('profile') if source.get('verified') else None
    checks, rows, identities, selections, settings = [], [], [], [], []

    def check(level, title, detail):
        checks.append({'level': level, 'title': title, 'detail': detail})

    if not source.get('verified'):
        check('warning', 'Source defaults unavailable', source.get('error') or 'Select a clean, supported pipeline installation and review again.')
    for sample in samples:
        row, identity, platforms, issues = _input_inventory(sample, tool)
        rows.append(row)
        identities.append(identity)
        selections.append(platforms)
        for issue in issues:
            check('warning', sample['id'] + ' · input review', issue)
    complete = all(all(item['status'] == 'present' or 'Optional input;' in item['detail']
                       for item in row['inputs']) and row['inputs'] for row in rows)
    homogeneous = bool(selections) and all(s == selections[0] for s in selections)
    if tool == 'hybas' and not homogeneous:
        check('warning', 'Different read types in this batch', 'Review samples with the same read types together before applying polishing defaults. Each saved draft still requires its own native validation.')
    valid_settings = True
    try:
        run_config.validate(tool, text)
    except (ValueError, TypeError, KeyError) as exc:
        valid_settings = False
        check('warning', 'Settings need review', str(exc))

    def value(name):
        # A source template is not necessarily the workflow's fallback for an
        # omitted setting. Do not turn a template into an effective run value.
        return _get(cfg, name, None)

    def setting(name, detail, applicable=True, allow_apply=False):
        saved, default = _get(cfg, name), _get(defaults, name)
        effective = saved
        applicable = bool(applicable)
        change = None
        if (allow_apply and applicable and complete and homogeneous and profile
                and default is not MISSING and saved != default):
            key = json.dumps(name.split('.'), separators=(',', ':'))
            try:
                changed = run_config.compile_config(tool, text, {key: default})
                run_config.validate(tool, changed)
                change = default
            except (ValueError, TypeError, KeyError):
                pass  # Do not offer a default the current editor cannot accept.
        settings.append({'name': name, 'key': json.dumps(name.split('.'), separators=(',', ':')),
                         'current': None if effective is MISSING else effective,
                         'current_origin': 'Draft setting' if saved is not MISSING else 'Not set; native default not inferred',
                         'default': None if default is MISSING else default,
                         'origin': ('Selected source template: ' + source.get('config_path', '') if default is not MISSING
                                    else 'No verified value in the selected source configuration'),
                         'detail': detail, 'applicable': applicable, 'change': change})

    if tool == 'hybas':
        known = profile in {'hybas9', 'hybas10'} and valid_settings
        if any(sample.get('input_manifest') for sample in samples) and profile != 'hybas10':
            known = False
            check('warning', 'Manifest inputs require a reviewed v2 route', 'This selected source has no reviewed manifest route in the adviser. Select a compatible HybAs 10 installation and review again.')
        native_modes = {frozenset({'illumina'}): 'illumina_only', frozenset({'ont'}): 'ont_only',
                        frozenset({'illumina', 'ont'}): 'hybrid', frozenset({'pacbio'}): 'pacbio_only',
                        frozenset({'illumina', 'pacbio'}): 'illumina_pacbio'}
        requested_mode = value('run_mode') if isinstance(value('run_mode'), str) else None
        requested_polish = value('polish_mode') if isinstance(value('polish_mode'), str) else None
        if profile == 'hybas10' and requested_mode not in {None, 'auto'}:
            for sample, platforms in zip(samples, selections):
                detected = native_modes.get(frozenset(platforms))
                if requested_mode != detected:
                    known = False
                    check('warning', sample['id'] + ' · conflicting run mode',
                          'The explicit run_mode does not match the registered read platforms. Review it before choosing polishing settings; the native workflow rejects conflicting modes.')
        if any('pacbio' in platforms for platforms in selections) and requested_polish in {'racon', 'medaka'}:
            known = False
            check('warning', 'Incompatible PacBio polishing mode', 'The experimental PacBio route rejects explicit Racon or Medaka polishing. Review the source-supported modes before native validation.')
        if not known:
            check('warning', 'Polishing route not reviewed for this source', 'Defaults may be displayed, but this adviser does not infer which stages execute for an unreviewed source version.')
        all_ont = homogeneous and selections[0] == {'ont'}
        any_ont = any('ont' in platforms for platforms in selections)
        any_pacbio = any('pacbio' in platforms for platforms in selections)
        any_short = any('illumina' in platforms for platforms in selections)
        polish = requested_polish
        effective_polish = 'tripolish' if profile == 'hybas10' and polish == 'auto' else polish
        if known:
            for sample, platforms in zip(samples, selections):
                if 'pacbio' in platforms:
                    detail = 'This experimental PacBio route uses Flye polishing; Racon and Medaka do not apply. Illumina Polypolish depends on the selected route and supplied short reads.'
                elif 'ont' in platforms:
                    if effective_polish == 'none':
                        detail = 'Polishing is disabled in this draft.'
                    elif effective_polish in {'racon', 'medaka', 'tripolish'}:
                        rounds = value('racon_rounds')
                        detail = f"Selected mode {polish}: " + (f'configured Racon rounds {rounds}.' if rounds is not None else 'Racon rounds are not explicitly set; native validation resolves the fallback.')
                        if effective_polish in {'medaka', 'tripolish'}:
                            detail += ' Medaka is included; check the model against the sequencing/basecaller metadata.'
                        if effective_polish == 'tripolish':
                            if 'illumina' not in platforms:
                                detail += ' Polypolish has no Illumina reads for this sample.'
                            elif value('run_polypolish') is None:
                                detail += ' The Polypolish toggle is not explicitly set; native validation resolves its fallback.'
                            else:
                                enabled = value('run_polypolish') is True or (profile == 'hybas10' and value('run_polypolish') == 'auto')
                                detail += ' Polypolish is ' + ('enabled' if enabled else 'disabled') + ' for the supplied Illumina reads.'
                    else:
                        detail = 'Polishing mode is not recognized by this reviewed route. Use native validation.'
                elif platforms == {'illumina'}:
                    detail = 'Illumina-only inputs skip Racon and Medaka. In the reviewed route, tripolish can run Polypolish when enabled.'
                else:
                    detail = 'Read platforms could not be established; no polishing route can be inferred.'
                check('info', sample['id'] + ' · polishing route', detail + ' This describes configured routing, not completed execution.')
        setting('polish_mode', 'Workflow starting point, not a sample-specific optimum. Review the route above and compare validation metrics before choosing a strategy.',
                applicable=known and homogeneous and not any_pacbio, allow_apply=True)
        setting('racon_rounds', 'The source default is a starting point. Read availability alone does not establish the best number of rounds; compare independently validated outcomes.',
                applicable=known and homogeneous and any_ont and effective_polish in {'racon', 'medaka', 'tripolish'}, allow_apply=True)
        setting('medaka_model', 'Use the appropriate model for the actual ONT chemistry/basecaller. A blank value delegates model handling to the workflow; it is not proof of a suitable model.',
                applicable=known and any_ont and effective_polish in {'medaka', 'tripolish'})
        setting('run_polypolish', 'This stage needs compatible Illumina reads; its effect is checked in the native route.',
                applicable=known and homogeneous and any_short and not any_pacbio and effective_polish == 'tripolish', allow_apply=True)
        if any_ont:
            check('review', 'ONT model and round count need evidence', 'This review does not measure coverage, read quality or basecaller identity. It cannot select a Medaka model or an optimal round count from a folder name or file size.')
        if all_ont:
            check('info', 'No short-read polishing', 'The selected sample declarations contain only ONT reads, so Illumina-dependent polishing has no short-read input.')
        for name in ('assembly_mem_mb', 'polishing_mem_mb'):
            requested = value(name)
            if type(requested) in {int, float} and requested > memory:
                check('warning', 'Review ' + name, f'This draft requests {requested} MB for this stage with a total run budget of {memory} MB. Check scheduling and the native dry-run before execution.')
        if any(sample.get('aware_provenance') for sample in samples):
            check('review', 'Derived AWARE inputs', 'Draft creation applies the existing derived-input policy. Review its final frozen settings and native dry-run before launch.')
    elif tool == 'basis':
        check('info', 'Annotation analysis', 'BaSIS uses annotated inputs. Assembly polishing is selected in HybAs, not in this run.')
        setting('deep.enabled', 'Screening and follow-up have different scopes. Review the requested scope and report coverage; this adviser does not select a scope for you.')
        setting('deep.maximum_candidates', 'A computing ceiling, not a target and not evidence that remaining candidates are unimportant.')
        setting('deep.aware.preset', 'A workflow policy setting. Input file availability does not establish an appropriate evidence threshold.')
        check('review', 'Review annotation consistency and assessment coverage', 'File presence does not establish that the assembly, proteins and annotations belong together. Review input provenance, database versions and skipped or unsupported modules; screening does not establish deployment safety.')
    else:
        check('info', 'Protein evidence analysis', 'GeneScope uses protein annotations. Assembly polishing is selected in HybAs, not in this run.')
        database = cfg.get('reference_database')
        if not database:
            check('review', 'No application database selected', 'This draft does not explicitly select an application reference database. Choose one in settings if you intend to run a reference search.')
        else:
            check('review', 'Review the selected application database', 'Confirm its identity, release and relevance. Native validation checks its required files; a path alone does not establish database quality.')
        for name in ('reference_min_identity', 'reference_min_coverage', 'reference_max_evalue'):
            setting(name, 'Source search filter, not a validated functional cutoff for this sample. Review the application database and evidence before changing it.')
        check('review', 'Interpret evidence within its scope', 'Similarity and sequence descriptors do not establish function, operating optima, material performance or deployment safety.')
    public_source = {key: val for key, val in source.items() if key not in {'defaults', 'profile'}}
    doc = {'tool': tool, 'rule_version': RULE_VERSION, 'source': public_source, 'samples': rows,
           'checks': checks, 'settings': settings, 'limits': [
               'Input checks cover registered declarations and current file availability, not sequence quality, pairing or biological identity.',
               'Source template values are version-specific starting points, not sample-specific optima or inferred fallback values for omitted settings.',
               'Applying a source value changes only this unsaved draft. Saved workspace defaults and previous runs remain unchanged.',
               'Create the draft, inspect its frozen settings and pass the native dry-run before running.']}
    bound = {'review': doc, 'text': text, 'samples': samples, 'inputs': identities,
             'cores': cores, 'memory_mb': memory,
             'installation': store.get('settings', 'active-' + tool)}
    doc['signature'] = hashlib.sha256(json.dumps(bound, sort_keys=True, ensure_ascii=True).encode()).hexdigest()
    return doc


def apply(store, payload):
    doc = review(store, payload)
    if payload.get('signature') != doc['signature']:
        raise ValueError('Setup advice is out of date. Review the current samples, source and settings again.')
    selected = next((row for row in doc['settings'] if row['name'] == payload.get('setting')), None)
    if not selected or selected['change'] is None or not selected['applicable']:
        raise ValueError('This setting has no applicable workflow default to apply.')
    tool, _, text, _, _ = _request(store, payload)
    text = run_config.compile_config(tool, text, {selected['key']: selected['change']})
    run_config.validate(tool, text)
    updated = dict(payload, config={'tool': tool, 'text': text})
    return {'run_config': run_config.editor(tool, text), 'run_config_view': 'settings',
            'setup_advice': review(store, updated), 'keep_modal': True,
            'message': 'Source starting value applied to this unsaved draft: ' + selected['name'] + '.'}
