"""Run-specific writing aid. No analyses, environment probes or resource downloads.

Configured scope and recorded execution are deliberately separate. Completion of
the workflow is not evidence that every requested BaSIS module completed.
"""
from __future__ import annotations

import base64
import copy
import csv
import hashlib
import html
import io
import json
from pathlib import Path
import zipfile

import yaml

from . import __version__
from .catalog import DATA, PIPELINES
from .run_config import MODULE_NAMES
from .runs import parse_config, tree_digest
from .store import now

LIMIT = 8 * 1024 * 1024
DEFAULTS = json.loads((DATA / 'methods-defaults.json').read_text())
UNKNOWN = 'Not recorded'


def _merge(base, update):
    result = copy.deepcopy(base)
    for key, value in update.items():
        result[key] = _merge(result[key], value) if isinstance(value, dict) and isinstance(result.get(key), dict) else copy.deepcopy(value)
    return result


def _read(root, relative):
    """Only bounded, regular, allowlisted files under a run-owned directory."""
    root, rel = Path(root), Path(relative)
    if rel.is_absolute() or '..' in rel.parts:
        raise ValueError('Invalid Methods evidence path')
    path = root / rel
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError('Linked Methods evidence is not read: ' + relative)
    if not path.exists():
        return None
    if not path.is_file():
        raise ValueError('Methods evidence is not a regular file: ' + relative)
    with path.open('rb') as handle:
        raw = handle.read(LIMIT + 1)
    if len(raw) > LIMIT:
        raise ValueError('Methods evidence exceeds 8 MB: ' + relative)
    return raw


def _value(value):
    if value is None or value == '' or str(value).strip().lower() in {'na', 'n/a', 'unknown', 'unavailable'}:
        return UNKNOWN
    return json.dumps(value, ensure_ascii=False, sort_keys=True) if isinstance(value, (dict, list)) else str(value)


def _pick(row, keys):
    return {key: row[key] for key in keys if key in row}


def collect(run):
    """Read metadata only; never enumerate raw inputs, native results or databases."""
    result = {'files': [], 'warnings': [], 'versions': {}, 'records': [], 'decisions': {}}
    root = Path(run['output'])

    def read(rel, kind='json'):
        try:
            raw = _read(root, rel)
            if raw is None:
                return None
            text = raw.decode('utf-8')
            parsed = json.loads(text) if kind == 'json' else list(csv.DictReader(io.StringIO(text), delimiter='\t'))
            if kind == 'json' and not isinstance(parsed, dict):
                raise ValueError('Expected an object')
            result['files'].append({'path': rel, 'sha256': hashlib.sha256(raw).hexdigest(), 'bytes': len(raw)})
            return parsed
        except (OSError, ValueError, UnicodeError, RecursionError) as exc:
            result['warnings'].append('Could not read ' + rel + ': ' + str(exc))
            return None

    if run['tool'] == 'hybas':
        summary = read('metadata/run_summary.json') or {}
        if summary.get('isolate', run['sample']['id']) != run['sample']['id']:
            result['warnings'].append('HybAs summary sample identity does not match this run.')
            summary = {}
        result['decisions'] = _pick(summary, ('run_mode', 'kraken_decision', 'kraken_decision_mode',
            'kraken_decision_reason', 'executed_stages', 'skipped_stages', 'final_source_stage',
            'busco_lineage', 'medaka_model'))
        for row in read('metadata/tool_versions.tsv', 'tsv') or []:
            if row.get('tool'):
                result['versions'][row['tool']] = _value(row.get('version'))
        # Database file identities are provided by the workflow, not inferred from paths.
        global_root = root.parent / 'provenance/databases'
        try:
            raw = _read(global_root, 'database_hashes.tsv')
            if raw:
                result['database_hashes'] = list(csv.DictReader(io.StringIO(raw.decode('utf-8')), delimiter='\t'))
                result['files'].append({'path': '../provenance/databases/database_hashes.tsv',
                                        'sha256': hashlib.sha256(raw).hexdigest(), 'bytes': len(raw)})
        except (OSError, ValueError, UnicodeError) as exc:
            result['warnings'].append('Database identities unavailable: ' + str(exc))
    else:
        folder = 'safety/deepsafe-report' if run.get('run_type') == 'followup' else 'safety'
        bundle = read(folder + '/evidence.json') or {}
        meta = bundle.get('run', {})
        if isinstance(meta, dict):
            result['native_run'] = _pick(meta, ('version', 'completeness', 'incomplete_modules', 'report_view', 'finished_at'))
        ordinary, deep = bundle.get('records', []), bundle.get('deep_records', [])
        if not isinstance(ordinary, list) or not isinstance(deep, list):
            result['warnings'].append('BaSIS execution record lists are malformed.')
            ordinary, deep = [], []
        rows = deep if run.get('run_type') == 'followup' else ordinary + deep
        seen = set()
        for row in rows:
            if not isinstance(row, dict) or row.get('sample_id') != run['sample']['id']:
                result['warnings'].append('A BaSIS record with an absent or different sample identity was omitted.')
                continue
            module = row.get('module')
            if not isinstance(module, str) or module not in {*MODULE_NAMES, 'deepsafe'} or module in seen:
                result['warnings'].append('A BaSIS record with an unknown or duplicate module was omitted.')
                continue
            seen.add(module)
            result['records'].append(_pick(row, ('module', 'execution_status', 'reason', 'tool_version',
                'database_version', 'database_manifest', 'module_decision', 'complete', 'upstream_incomplete')))
    return result


def capture(store, run, job_id):
    """Keep execution metadata before shared follow-up output can be replaced."""
    receipt = {'schema': 1, 'run_id': run['id'], 'job_id': job_id, 'completed': run['completed'],
               'config_sha256': run['config_sha256'], 'workflow_sha256': run['workflow_sha256'],
               'commit': run['commit'], 'captured_at': now(),
               'evidence': collect(run)}
    store.put('methods_evidence', run['id'], receipt)
    return receipt


def _configuration(run, warnings):
    text = run['config']
    digest = hashlib.sha256(text.encode()).hexdigest()
    if digest != run['config_sha256']:
        raise ValueError('The saved configuration record changed. Methods export is blocked.')
    disk = _read(run['folder'], 'config.yaml')
    if disk is not None and hashlib.sha256(disk).hexdigest() != digest:
        raise ValueError('The frozen configuration file changed. Restore it before exporting Methods.')
    if disk is None:
        warnings.append('Frozen YAML is unavailable on disk; the verified saved configuration record is used.')
    cfg = parse_config(text)
    known = run['commit'] == DEFAULTS['sources'].get(run['tool'])
    source = Path(run['folder']) / 'workflow'
    if source.exists():
        try:
            if tree_digest(source) != run['workflow_sha256']:
                warnings.append('The workflow copy changed; inferred defaults and method descriptions are unavailable.')
                known = False
        except OSError:
            known = False
    else:
        known = False
    if not known:
        warnings.append('No verified Methods template is available for this source. Only explicit saved settings and recorded execution are exported.')
        return cfg, cfg, False
    effective = copy.deepcopy(DEFAULTS['defaults'][run['tool']])
    raw = _read(source, 'config.yaml')
    configured = _merge(parse_config(raw.decode('utf-8')) if raw else {}, cfg)
    effective = _merge(effective, configured)
    if run['tool'] == 'hybas':
        if 'spades_memory_gb' not in configured:
            effective['spades_memory_gb'] = max(1, int(effective['assembly_mem_mb']) // 1024)
        for key in ('run_kraken', 'run_busco', 'run_prokka', 'run_multiqc', 'run_polypolish'):
            value = effective[key]
            if isinstance(value, str):
                value = value.strip().lower()
                if value in {'true', '1', 'yes', 'y', 'on', 'false', '0', 'no', 'n', 'off'}:
                    effective[key] = value in {'true', '1', 'yes', 'y', 'on'}
                else:
                    warnings.append('An invalid boolean prevents method-specific prose: ' + key)
                    return cfg, cfg, False
        for key in ('kraken_decision_mode', 'polish_mode'):
            effective[key] = str(effective[key]).strip().lower()
    if run['tool'] == 'basis':
        threads = configured.get('threads', 4)
        for module, section in effective['modules'].items():
            section['threads'] = configured.get('modules', {}).get(module, {}).get('threads', threads)
        if isinstance(effective.get('deep'), bool):
            effective['deep'] = _merge(DEFAULTS['defaults']['basis']['deep'], {'enabled': effective['deep']})
        effective['deep']['threads'] = configured.get('deep', {}).get('threads', threads) if isinstance(configured.get('deep', {}), dict) else threads
        effective['modules']['pathogenfinder2']['enabled'] &= bool(effective.get('extend', True))
        effective['execution'].update(cores=threads, memory_mb=run['memory_mb'])
        if run.get('run_type') == 'followup':
            rel = 'safety/metadata/config.json'
            expected = next((r.get('sha256') for r in run.get('followup_sources', [])
                             if r.get('path') == str(Path(run['output']) / rel)), None)
            raw = _read(run['output'], rel)
            if not raw or hashlib.sha256(raw).hexdigest() != expected:
                warnings.append('The inherited screening configuration cannot be verified. Only explicit follow-up settings are exported.')
                return cfg, cfg, False
            original = json.loads(raw)
            effective = _merge({'deep': original['deep']}, cfg)
            effective['deep'].update(threads=threads, memory_mb=run['memory_mb'])
    return cfg, effective, True


def _hybas(cfg, run):
    mode = run['sample']['mode']
    paragraphs, tools = [], ['snakemake', 'unicycler', 'quast']
    paragraphs.append('The workflow was configured for ' + {'hybrid': 'hybrid Illumina and Oxford Nanopore',
        'ont_only': 'Oxford Nanopore-only', 'illumina_only': 'Illumina-only'}.get(mode, mode) +
        ' input. Assembly was configured with Unicycler and assembly quality assessment with QUAST.')
    if mode in {'hybrid', 'illumina_only'}:
        paragraphs.append('Illumina read processing was configured with fastp and quality reporting with FastQC.')
        tools += ['fastp', 'fastqc']
    if mode in {'hybrid', 'ont_only'}:
        paragraphs.append(f"Oxford Nanopore read filtering was configured with Filtlong (keep_percent={cfg['keep_percent']}; minimum read length 1,000 bp), with NanoPlot quality reporting.")
        tools += ['filtlong', 'nanoplot']
    decision = str(cfg['kraken_decision_mode']).lower()
    if decision == 'off':
        paragraphs.append('Kraken2 profiling and its read-selection decision layer were disabled.')
    elif not cfg.get('run_kraken') or decision not in {'manual', 'auto', 'aware'}:
        paragraphs.append('The saved Kraken2 flags conflict; native validation is required before these methods can be used.')
    else:
        paragraphs.append(f'Kraken2 profiling was configured in {decision.upper()} decision mode. Database identity and decision parameters are listed in the settings supplement; automatic decisions require the execution record below.')
        tools += ['kraken2', 'krakentools']
    polish, stages = cfg['polish_mode'], []
    if mode in {'hybrid', 'ont_only'} and polish != 'none':
        stages += [f"Racon ({cfg['racon_rounds']} rounds)"] if cfg['racon_rounds'] else []
        if cfg['racon_rounds']:
            tools.append('racon')
        if polish in {'medaka', 'tripolish'}:
            stages.append('Medaka')
            tools.append('medaka')
    if mode in {'hybrid', 'illumina_only'} and polish == 'tripolish' and cfg['run_polypolish']:
        stages.append('Polypolish')
        tools.append('polypolish')
    paragraphs.append('The requested polishing mode was ' + polish + '; compatible planned stages were ' +
                      (', '.join(stages) if stages else 'none') + '. Executed or skipped stages are reported separately when recorded.')
    if 'medaka' in tools:
        paragraphs.append('The Medaka model setting was ' + (_value(cfg.get('medaka_model')) if cfg.get('medaka_model') else 'automatic selection (actual model requires execution evidence)') + '.')
    paragraphs.append(f"The minimum assembly FASTA length setting was {cfg['min_fasta_length']} bp.")
    for key, tool, sentence in (
        ('run_busco', 'busco', f"BUSCO completeness assessment was requested using lineage {cfg['busco_lineage']}."),
        ('run_prokka', 'prokka', 'Genome annotation with Prokka was requested.'),
        ('run_multiqc', 'multiqc', 'Quality-control report aggregation with MultiQC was requested.')):
        if cfg[key]:
            paragraphs.append(sentence)
            tools.append(tool)
    return paragraphs, tools


def _basis(cfg, run):
    if run.get('run_type') == 'followup':
        paragraphs = ['This draft requests a reviewed BaSIS follow-up using the saved screening evidence from parent run ' +
                      str(run.get('parent')) + '. Screening tools are not described as newly executed by this follow-up.']
        tools = ['snakemake']
    else:
        excluded = cfg.get('sample_module_overrides', {}).get(run['sample']['id'], {})
        selected = [key for key, value in cfg['modules'].items() if value.get('enabled') and key not in excluded]
        tools = ['snakemake', *selected]
        paragraphs = ['Annotated assembly inputs were configured for analysis with BaSIS AWARE. The requested modules were ' +
                      (', '.join(MODULE_NAMES.get(k, k) for k in selected) or 'none') +
                      '. Per-module execution status and applicability are reported separately; enabled modules are not assumed to have completed.']
        if excluded:
            paragraphs.append('Sample-specific exclusions were configured for ' + ', '.join(MODULE_NAMES.get(k, k) for k in excluded) + '.')
    deep = cfg.get('deep', {})
    aware = deep.get('aware', {})
    paragraphs.append('The BaSIS AWARE policy was ' + ('enabled with preset ' + str(aware.get('preset')) if aware.get('enabled') else 'disabled') +
                      '. Its saved overrides are included in the settings supplement.')
    paragraphs.append('DeepSafe follow-up was ' + ('requested; its configured options are included in the supplement and completion must be checked against recorded evidence.'
                      if deep.get('enabled') else 'disabled for this run.'))
    if deep.get('enabled'):
        tools.append('deepsafe')
    return paragraphs, tools


def _flatten(value, prefix=''):
    for key, item in value.items():
        name = prefix + str(key)
        if isinstance(item, dict) and item:
            yield from _flatten(item, name + '.')
        else:
            yield name, item


def build(store, run):
    warnings = []
    explicit, effective, known = _configuration(run, warnings)
    jobs = [j for j in store.jobs() if j['kind'] == 'workflow' and j['payload'].get('run_id') == run['id'] and j['payload'].get('action') == 'run']
    latest = jobs[0] if jobs else None
    complete = bool(run.get('completed')) and (not latest or latest['status'] == 'succeeded')
    state = 'Completed workflow' if complete else 'Incomplete execution' if jobs else 'Planned methods'
    receipt = store.get('methods_evidence', run['id'])
    evidence, origin = {}, 'No completed execution record'
    if complete:
        if (receipt and receipt.get('run_id') == run['id']
                and (not latest or receipt.get('job_id') == latest['id'])
                and all(receipt.get(k) == run.get(k) for k in ('completed', 'config_sha256', 'workflow_sha256', 'commit'))):
            evidence, origin = receipt['evidence'], 'Completion-time snapshot'
        elif run.get('run_type') == 'followup' and store.get('followup_output_owner', run.get('parent')) != run['id']:
            warnings.append('This follow-up no longer owns the shared output. Historical execution evidence was not captured.')
        else:
            evidence, origin = collect(run), 'Legacy output metadata read at export'
            warnings.append('No completion-time Methods snapshot exists. Available legacy metadata was read at export; verify its association with this run.')
    warnings.extend(evidence.get('warnings', []))
    if evidence.get('native_run', {}).get('completeness') == 'incomplete':
        warnings.append('The native report records incomplete requested scope. Review the module execution statuses before describing the analyses as completed.')
    if not complete:
        warnings.append('Planned settings only: this export does not claim that analyses completed. Download again after completion.')
    if known:
        paragraphs, tools = (_hybas if run['tool'] == 'hybas' else _basis)(effective, run)
    else:
        paragraphs, tools = ['The saved configuration is provided in the supplement. Method-specific prose is unavailable for this source version.'], []
    versions = dict(evidence.get('versions', {}))
    records = evidence.get('records', [])
    for row in records:
        versions[row.get('module', '')] = _value(row.get('tool_version'))
    software = [{'tool': name, 'version': _value(versions.get(name)), 'scope': 'Configured; execution must be verified'} for name in dict.fromkeys(tools)]
    for row in software:
        record = next((r for r in records if r.get('module') == row['tool']), None)
        if record:
            row['scope'] = _value(record.get('execution_status'))
    if any(row['version'] == UNKNOWN for row in software):
        warnings.append('Some tool versions are not recorded. Environment recipe constraints and currently installed packages are not substituted for versions used by this run.')
    warnings.append('Review database release dates, model identities and tool/database citations before submission. A directory name alone is not a database version.')
    intro = f"The analysis was configured using {PIPELINES[run['tool']]['name']} (source commit {run['commit']}) through Bactologix. The saved computing allocation was {run['cores']} CPU cores and {run['memory_mb']} MB RAM."
    parameters = []
    supplied = dict(_flatten(explicit))
    for key, value in _flatten(effective):
        parameter_origin = ('Saved run' if key in supplied else 'Inherited screening configuration'
                  if run.get('run_type') == 'followup' else 'Pinned workflow default')
        parameters.append({'setting': key, 'value': _value(value), 'origin': parameter_origin})
    execution = []
    if evidence.get('decisions'):
        execution += [key.replace('_', ' ').capitalize() + ': ' + _value(value) for key, value in evidence['decisions'].items()]
    if evidence.get('native_run'):
        execution.append('Native report scope: ' + _value(evidence['native_run'].get('completeness')))
    for row in records:
        execution.append(f"{MODULE_NAMES.get(row.get('module'), row.get('module'))}: execution={_value(row.get('execution_status'))}; database={_value(row.get('database_version'))}; reason={_value(row.get('reason'))}.")
    if not execution:
        execution = ['No per-step execution metadata is available for this export.']
    if complete and evidence.get('decisions'):
        decisions = evidence['decisions']
        actual = []
        if 'kraken_decision' in decisions:
            actual.append('Kraken decision: ' + _value(decisions['kraken_decision']))
        if 'executed_stages' in decisions:
            actual.append('executed polishing stages: ' + (', '.join(map(str, decisions['executed_stages'])) or 'none'))
        if 'medaka_model' in decisions and 'medaka' in decisions.get('executed_stages', []):
            actual.append('Medaka model: ' + _value(decisions['medaka_model']))
        if actual:
            paragraphs.append('The retained execution record reports ' + '; '.join(actual) + '.')
    if complete and records:
        completed_modules = [MODULE_NAMES.get(r.get('module'), r.get('module')) for r in records
                             if r.get('execution_status') == 'completed' and r.get('complete') is not False
                             and not r.get('upstream_incomplete')]
        paragraphs.append('The retained module records report completion of ' + (', '.join(completed_modules) or 'no modules') +
                          '. Incomplete, excluded and unavailable modules are listed in the execution supplement.')
    recorded = [r['tool'] + ' ' + r['version'] for r in software if r['version'] != UNKNOWN]
    if recorded:
        paragraphs.append('Recorded software versions: ' + '; '.join(recorded) + '.')
    document = {'id': run['id'], 'state': state, 'generated_at': now(), 'generator_version': __version__,
        'created_with_bactologix': run.get('bactologix_version', UNKNOWN),
        'pipeline': PIPELINES[run['tool']]['name'], 'source_commit': run['commit'],
        'config_sha256': run['config_sha256'], 'workflow_sha256': run['workflow_sha256'],
        'sample_id': run['sample']['id'], 'parent': run.get('parent'), 'completed': run.get('completed') if complete else None,
        'paragraphs': [intro, *paragraphs], 'execution': execution, 'software': software,
        'parameters': parameters, 'warnings': list(dict.fromkeys(warnings)), 'evidence_origin': origin,
        'evidence': evidence, 'citation_url': f"https://github.com/{PIPELINES[run['tool']]['repo']}/tree/{run['commit']}"}
    document['text'] = render_text(document)
    return document


def render_text(doc, markdown=False):
    heading = lambda title: ('## ' if markdown else '') + title
    lines = [('## ' if markdown else '') + 'Methods', '', *[p + '\n' for p in doc['paragraphs']],
             heading('Export status and review'), '', doc['state'] + ' · ' + doc['id'],
             'Evidence source: ' + doc['evidence_origin'], *['- ' + w for w in doc['warnings']], '',
             heading('Recorded execution'), '', *doc['execution'], '', heading('Software versions'), '',
             *[f"{r['tool']}: {r['version']} ({r['scope']})" for r in doc['software']], '',
             heading('Reproducibility'), '', 'Pipeline source: ' + doc['citation_url'],
             'Frozen configuration SHA256: ' + doc['config_sha256'],
             'Workflow copy SHA256: ' + doc['workflow_sha256'],
             'Draft created with Bactologix: ' + doc['created_with_bactologix'],
             'Methods generated by Bactologix ' + doc['generator_version'] + ' at ' + doc['generated_at'], '',
             heading('Settings supplement'), '', *[f"{r['setting']} = {r['value']} [{r['origin']}]" for r in doc['parameters']]]
    return '\n'.join(lines) + '\n'


def _html(doc):
    body = ''.join('<p>' + html.escape(p) + '</p>' for p in doc['paragraphs'])
    return ('<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width">'
            '<title>Methods — ' + html.escape(doc['id']) + '</title><style>body{font:16px/1.6 system-ui;max-width:880px;margin:40px auto;padding:0 24px;color:#172b30}pre{white-space:pre-wrap;overflow-wrap:anywhere;font:13px/1.5 monospace}h1{font-size:28px}@media print{body{margin:0}}</style>'
            '<h1>Methods</h1><p><strong>' + html.escape(doc['state']) + '</strong> · ' + html.escape(doc['id']) + '</p>' + body +
            '<h2>Review and reproducibility supplement</h2><pre>' + html.escape(doc['text'].split('Export status and review\n', 1)[1]) + '</pre></html>')


def download(store, run, format):
    if format not in {'txt', 'md', 'html', 'zip'}:
        raise ValueError('Choose Text, Markdown, HTML or the reproducibility ZIP.')
    doc = build(store, run)
    name = run['id'] + '-methods.' + format
    if format != 'zip':
        text = _html(doc) if format == 'html' else render_text(doc, format == 'md')
        return {'name': name, 'text': text, 'type': {'txt': 'text/plain', 'md': 'text/markdown', 'html': 'text/html'}[format]}
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr('methods.txt', doc['text'])
        archive.writestr('methods.md', render_text(doc, True))
        archive.writestr('methods.html', _html(doc))
        archive.writestr('config.yaml', run['config'])
        archive.writestr('methods-provenance.json', json.dumps({k: v for k, v in doc.items() if k != 'text'}, indent=2, ensure_ascii=False) + '\n')
        table = io.StringIO()
        writer = csv.writer(table, delimiter='\t', lineterminator='\n')
        writer.writerow(['setting', 'value', 'origin'])
        for row in doc['parameters']:
            writer.writerow(["'" + v if v.startswith(('=', '+', '-', '@', '\t', '\r', '\n')) else v for v in row.values()])
        archive.writestr('settings.tsv', table.getvalue())
        citation = _read(Path(run['folder']) / 'workflow', 'CITATION.cff')
        if citation:
            archive.writestr('citations/pipeline-source-CITATION.cff', citation)
        app_citation = yaml.safe_load((DATA / 'CITATION.cff').read_text())
        if run.get('bactologix_version'):
            app_citation['version'] = run['bactologix_version']
        else:
            app_citation.pop('version', None)
        archive.writestr('citations/Bactologix-CITATION.cff', yaml.safe_dump(app_citation, sort_keys=False, allow_unicode=True))
        archive.writestr('README.txt', 'Review Methods and unresolved items before publication.\nThe YAML and supplement contain local paths and sample identifiers.\nSoftware versions are recorded evidence, not current environment probes.\nCite the pipeline source and tools/databases used; add verified publication references.\nThe workflow configuration and execution behavior were not changed by this export.\n')
    return {'name': name, 'base64': base64.b64encode(buffer.getvalue()).decode(), 'type': 'application/zip'}
