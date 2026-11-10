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
import re
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
VALIDATION_STATUS = ('Experimental pipeline: scientific validation of the assembled workflow for its intended application '
                     'has not been established. This document describes saved settings and available execution evidence; '
                     'workflow completion does not establish analytical accuracy, biological function or deployment safety.')
SOFTWARE_LABELS = {'micpscope': 'GeneScope', 'basis': 'BaSIS', 'hybas': 'HybAs',
                   'diamond': 'DIAMOND', 'python': 'Python', 'biopython': 'Biopython',
                   'snakemake': 'Snakemake', 'controller_python': 'Controller Python',
                   'vfdb': 'DIAMOND (VFDB search)', 'bacmet': 'DIAMOND (BacMet search)'}


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


def _software_version(name, raw):
    value = _value(raw)
    # Keep original measurements in evidence; simplify only the displayed form.
    if name == 'diamond':
        value = re.sub(r'^diamond\s+(?:version\s+)?v?(?=\d)', '', value, flags=re.I)
    return value


def _reused(row):
    return row.get('reused') is True or row.get('cache_hit') is True


def _scope_gaps(row, native=None):
    gaps = []
    decision = row.get('module_decision') or {}
    summary = row.get('completion_summary') or {}
    if row.get('complete') is False:
        gaps.append('native complete=false')
    if row.get('scope_incomplete') or isinstance(decision, dict) and decision.get('scope_incomplete'):
        gaps.append('native scope incomplete')
    if row.get('upstream_incomplete'):
        gaps.append('upstream assessment incomplete')
    if isinstance(summary, dict):
        if summary.get('selected_execution') == 'incomplete':
            gaps.append('selected execution incomplete')
        if summary.get('deferred_candidates'):
            gaps.append('deferred candidates')
    identifiers = (native or {}).get('incomplete_modules', [])
    if row.get('module') in identifiers or str(row.get('sample_id')) + ':' + str(row.get('module')) in identifiers:
        gaps.append('listed among native incomplete modules')
    return gaps


def _module_complete(row, native=None):
    return row.get('execution_status') == 'completed' and not _scope_gaps(row, native)


def _databases(evidence, origin):
    rows = []
    for row in evidence.get('database_hashes', []):
        if isinstance(row, dict):
            rows.append(origin + ': ' + '; '.join(str(k).replace('_', ' ') + '=' + _value(v) for k, v in row.items()))
    reference = evidence.get('metadata', {}).get('reference_search', {}).get('database', {})
    if reference:
        rows.append(origin + ' · application database: ' + '; '.join(
            k.replace('_', ' ') + '=' + _value(reference[k]) for k in
            ('id', 'name', 'version', 'checksums_sha256', 'manifest_sha256') if k in reference))
    for row in evidence.get('records', []):
        parts = []
        if row.get('database_version'):
            parts.append('reported version=' + _value(row['database_version']))
        manifest = row.get('database_manifest')
        if isinstance(manifest, dict):
            parts.extend(k.replace('_', ' ') + '=' + _value(manifest[k]) for k in
                         ('name', 'version', 'release', 'sha256', 'manifest_sha256', 'checksums_sha256') if k in manifest)
        elif isinstance(manifest, str) and manifest:
            parts.append('manifest reference=' + manifest)
        if parts:
            rows.append(origin + ' · ' + MODULE_NAMES.get(row.get('module'), row.get('module', 'Module')) + ': ' + '; '.join(parts))
    return rows


def collect(run):
    """Retain metadata only; never open raw inputs or installed databases."""
    result = {'files': [], 'warnings': [], 'versions': {}, 'records': [], 'decisions': {}}
    root = Path(run['output'])

    if run['tool'] == 'micpscope':
        from .genescope_metadata import read as read_metadata
        from .evidence_identity import validate_native
        try:
            loaded = read_metadata(root)
            if loaded is None:
                result['warnings'].append('GeneScope results are missing: micpscope/results.json.')
                return result
            data, identity = loaded
            if not data.get('sample_id'):
                raise ValueError('GeneScope results have no sample identity.')
            checked = validate_native(run, data)
            if 'sample identity' not in checked['checks'] or 'sample identity' in checked['missing']:
                raise ValueError('GeneScope results have no verified sample identity.')
            provenance = data.get('provenance', {})
            for source in (data, provenance):
                for key in ('run_id', 'bactologix_run_id'):
                    if key in source and source[key] != run['id']:
                        raise ValueError('GeneScope results name a different run.')
            reference = data.get('reference_search', {})
            if reference.get('input_sha256') and provenance.get('input_sha256') != reference['input_sha256']:
                raise ValueError('GeneScope reference metadata names a different protein input.')
            expected_database = parse_config(run['config']).get('reference_database_sha256')
            actual_database = reference.get('database', {}).get('checksums_sha256')
            if expected_database and actual_database and actual_database != expected_database:
                raise ValueError('GeneScope reference database identity does not match the saved run.')
            result.update(metadata=data, identity=checked)
            result['files'].append(identity)
            result['versions'] = {'biopython': _value(provenance.get('biopython_version')),
                                  'micpscope': _value(data.get('software_version')),
                                  'python': _value(provenance.get('python_version'))}
            if reference.get('tool_version'):
                result['versions']['diamond'] = _value(reference['tool_version'])
        except (OSError, ValueError, UnicodeError, RecursionError) as exc:
            result['warnings'].append('Could not collect GeneScope Methods metadata: ' + str(exc))
        return result
    if run['tool'] == 'hybas':
        from .hybas_methods import collect as collect_hybas
        return collect_hybas(run, _read)
    from .basis_methods import collect as collect_basis
    return collect_basis(run)


def capture(store, run, job_id):
    """Retain completion evidence per attempt before shared outputs can change."""
    receipt = {'schema': 1, 'run_id': run['id'], 'job_id': job_id, 'completed': run['completed'],
               'config_sha256': run['config_sha256'], 'workflow_sha256': run['workflow_sha256'],
               'commit': run['commit'], 'identification': run.get('identification'), 'captured_at': now(),
               'evidence': collect(run)}
    from .methods_runtime import evidence as runtime_evidence
    runtime = runtime_evidence(store, run, job_id)
    if runtime:
        receipt['runtime'] = runtime
    with store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        old = db.execute("SELECT body FROM items WHERE kind='methods_evidence' AND id=?", (run['id'],)).fetchone()
        if old:
            previous = json.loads(old['body'])
            if previous.get('run_id') == run['id'] and isinstance(previous.get('job_id'), str):
                db.execute("INSERT INTO items VALUES ('methods_attempt_evidence',?,?) ON CONFLICT(kind,id) DO NOTHING",
                           (previous['job_id'], old['body']))
        existing = db.execute("SELECT body FROM items WHERE kind='methods_attempt_evidence' AND id=?", (job_id,)).fetchone()
        if existing:
            saved = json.loads(existing['body'])
            if {k: v for k, v in saved.items() if k != 'captured_at'} != {k: v for k, v in receipt.items() if k != 'captured_at'}:
                raise ValueError('Completion evidence for this attempt is already recorded and cannot be replaced.')
            receipt = saved
        encoded = json.dumps(receipt)
        db.execute("INSERT INTO items VALUES ('methods_attempt_evidence',?,?) ON CONFLICT(kind,id) DO NOTHING", (job_id, encoded))
        db.execute("INSERT INTO items VALUES ('methods_evidence',?,?) ON CONFLICT(kind,id) DO UPDATE SET body=excluded.body",
                   (run['id'], encoded))
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
    reviewed = {}
    if run.get('identification', {}).get('decision_path'):
        from .identification import sha
        decision = run['identification']
        if sha(decision['decision_path']) != decision['decision_sha256']:
            raise ValueError('Reviewed identification decision changed; Methods export is blocked.')
        selected = json.loads(Path(decision['decision_path']).read_text())
        reviewed = {'kraken_decision_mode': selected['mode'], 'target_taxid': selected['taxid'] if selected['mode'] == 'manual' else ''}
        warnings.append('Target selection was finalized after identification. The original YAML is retained; effective settings include the recorded decision '+decision['decision_sha256']+'.')
    saved_only = run['commit'] in DEFAULTS.get('saved_settings_sources', {}).get(run['tool'], [])
    overrides = DEFAULTS.get('source_overrides', {}).get(run['tool'], {})
    known = saved_only or run['commit'] in [DEFAULTS['sources'].get(run['tool']),
        *DEFAULTS.get('equivalent_sources', {}).get(run['tool'], []), *overrides]
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
        warnings.append('No source-matched workflow description is available for this source. Only explicit saved settings and recorded execution are exported.')
        return cfg, dict(cfg, **reviewed), False
    if saved_only:
        return cfg, dict(cfg, **reviewed), True
    effective = _merge(DEFAULTS['defaults'][run['tool']], overrides.get(run['commit'], {}))
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
    effective.update(reviewed)
    return cfg, effective, True


def _hybas(cfg, run):
    if run['commit'] in DEFAULTS.get('saved_settings_sources', {}).get('hybas', []):
        paragraphs = ['The experimental HybAs 10 workflow was configured using the saved input and processing settings. Assembly and polishing routes depend on the input platform and configuration; available native records identify the assembler and stages separately.']
        if 'polish_mode' in cfg:
            paragraphs.append('The saved polishing mode was ' + _value(cfg['polish_mode']) + '. Requested settings are provided in the supplement; they do not establish which stages executed.')
        return paragraphs, ['snakemake']
    mode = run['sample']['mode']
    paragraphs, tools = [], ['snakemake', 'unicycler', 'quast', 'seqkit']
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
            tools += ['minimap2', 'racon']
        if polish in {'medaka', 'tripolish'}:
            stages.append('Medaka')
            tools.append('medaka')
    if mode in {'hybrid', 'illumina_only'} and polish == 'tripolish' and cfg['run_polypolish']:
        stages.append('Polypolish')
        tools += ['bwa', 'samtools', 'polypolish']
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
    paragraphs.append('The BaSIS AWARE policy was ' + ('enabled with preset ' + str(aware.get('preset')) +
                      ' and scope ' + str(aware.get('scope', 'all-supported')) if aware.get('enabled') else 'disabled') +
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


def _genescope(cfg):
    paragraphs = ['The workflow was configured to calculate protein sequence descriptors with Biopython, calculate the aliphatic index in GeneScope from residue counts, and generate a source-labelled evidence report. These descriptors do not determine pH or temperature optima, shelf life, material performance or deployment safety.']
    tools = ['micpscope', 'biopython', 'python', 'snakemake']
    if cfg.get('reference_database'):
        paragraphs.append('An optional DIAMOND protein reference search was configured against the selected application database. Its saved search filters are listed in the settings supplement; similarity candidates do not establish function, and configuration alone does not establish that a search executed.')
        tools.append('diamond')
    return paragraphs, tools


def build(store, run):
    warnings = []
    explicit, effective, known = _configuration(run, warnings)
    jobs = [j for j in store.jobs() if j['kind'] == 'workflow' and j['payload'].get('run_id') == run['id'] and j['payload'].get('action') in {'run', 'continue'}]
    latest = jobs[0] if jobs else None
    complete = bool(run.get('completed')) and (not latest or latest['status'] == 'succeeded')
    state = 'Completed workflow' if complete else 'Incomplete execution' if jobs else 'Planned methods'
    from .aware_methods import describe
    stage = describe(run, complete, _read)
    if stage:
        state = stage['state']
    receipt = store.get('methods_evidence', run['id'])
    evidence, origin = {}, 'No completed execution record'
    recovery = None
    if complete:
        if (receipt and receipt.get('run_id') == run['id']
                and (not latest or receipt.get('job_id') == latest['id'])
                and all(receipt.get(k) == run.get(k) for k in ('completed', 'config_sha256', 'workflow_sha256', 'commit'))):
            evidence, origin = receipt['evidence'], 'Completion-time snapshot'
            from .methods_recovery import supplement
            recovery = supplement(store, run, receipt, latest, collect)
            if recovery:
                origin += ('; supplemental metadata read from current output at export'
                           if recovery['status'] in {'recovered', 'unverified'} else
                           '; supplemental metadata recovery attempted at export')
        elif run.get('run_type') == 'followup' and store.get('followup_output_owner', run.get('parent')) != run['id']:
            warnings.append('This follow-up no longer owns the shared output. Historical execution evidence was not captured.')
        else:
            evidence, origin = collect(run), 'Legacy output metadata read at export'
            warnings.append('No completion-time Methods snapshot exists. Available legacy metadata was read at export; verify its association with this run.')
    if recovery:
        # Preserve the failed original snapshot in provenance, but do not repeat
        # its false sample-mismatch diagnosis in the current review notes.
        warnings.extend(w for w in evidence.get('warnings', [])
                        if 'Methods evidence exceeds 8 MB:' not in w
                        and w != 'GeneScope results are missing or belong to a different sample.')
        warnings.append('The original completion snapshot could not collect ' + PIPELINES[run['tool']]['name'] + ' metadata because of the former 8 MB limit. It is retained unchanged in the reproducibility ZIP; supplemental metadata is identified separately.')
        warnings.extend(recovery['warnings'])
    else:
        warnings.extend(evidence.get('warnings', []))
    if evidence.get('native_run', {}).get('completeness') == 'incomplete':
        warnings.append('The native report records incomplete requested scope. Review the module execution statuses before describing the analyses as completed.')
    if not complete and not stage:
        warnings.append('This attempt is not recorded as completed. Any available controller events are shown separately; saved settings alone do not establish execution. Download again after completion.')
    if run['tool'] == 'micpscope' and known:
        paragraphs, tools = _genescope(effective)
    elif known:
        paragraphs, tools = (_hybas if run['tool'] == 'hybas' else _basis)(effective, run)
    else:
        paragraphs, tools = ['The saved configuration is provided in the supplement. Method-specific prose is unavailable for this source version.'], []
    if stage:
        if not complete:
            paragraphs, tools = stage['paragraphs'], stage['tools']
        else:
            paragraphs.extend(stage['paragraphs'])
        origin = stage['origin'] if not complete else origin + '; ' + stage['origin']
        evidence['aware_stage'] = stage['evidence']
        evidence.setdefault('files', []).extend(stage['files'])
        if complete and evidence.get('decisions', {}).get('final_source_stage'):
            paragraphs.append('The retained execution record identifies the final assembly source stage as ' + str(evidence['decisions']['final_source_stage']) + '. Executed polishing stages were ' + (', '.join(evidence['decisions'].get('executed_stages', [])) or 'none recorded') + '.')
    if run['tool'] == 'hybas' and (run.get('experimental') or effective.get('aware_mode', 'legacy') != 'legacy' or '10.' in str(run.get('version', {})) or '10.' in str(effective.get('workflow_version', ''))):
        paragraphs.insert(0, 'HybAs 10 is experimental: input modes, polishing paths and AWARE+ features have not completed analytical validation. AWARE+ mode: ' + str(effective.get('aware_mode', 'legacy')) + '.')
    if run.get('application_channel') == 'dev':
        paragraphs.insert(0, 'This analysis was configured in Bactologix Dev, an independent experimental development installation. Software checks do not establish analytical validation.')
    provenance = run['sample'].get('aware_provenance')
    if provenance:
        paragraphs.append('Inputs were derived from AWARE parent sample ' + provenance['parent_sample'] + ', group ' + provenance['group'] + '. The source bundle SHA-256 was ' + provenance['bundle_sha256'] + '.')
        paragraphs.append('Kraken reclassification of the retained inputs was ' + ('enabled in this draft.' if explicit.get('aware_reclassify_derived') else 'disabled; the draft reused the retained read group.'))
    transition = run.get('followup_transition')
    if transition:
        from .followup import verify_transition
        verify_transition(run)
        paragraphs.append('The saved screening source was ' + transition['screening_commit'] +
                              '; the explicitly selected follow-up source was ' + transition['commit'] +
                              '. The recorded AWARE scope was ' + transition['scope'] + '.')
    conflicted = (run['tool'] == 'hybas' and
        'HybAs summary sample identity does not match this run.' in evidence.get('warnings', []))
    if conflicted:
        warnings.append('Conflicting sample metadata in this historical snapshot is excluded from the Methods description. The original snapshot remains in the provenance ZIP.')
    retrospective = origin.startswith('Legacy output metadata read at export')
    from .methods_runtime import evidence as runtime_evidence
    runtime = runtime_evidence(store, run, latest['id'], include_incomplete=True) if latest else None
    if runtime:
        warnings.extend(runtime.get('warnings', []))
    versions = {} if conflicted else dict(evidence.get('versions', {}))
    supplementary = (recovery or {}).get('evidence', {})
    supplementary_versions = supplementary.get('versions', {})
    scope = dict(evidence.get('version_scopes', {}))
    if run['tool'] == 'hybas':
        # Older snapshots did not retain the inventory/runtime distinction.
        for name in versions:
            scope.setdefault(name, 'Recorded environment package; execution not established')
    records = [] if conflicted else evidence.get('records', [])
    for row in records:
        name = row.get('module', '')
        if name and row.get('tool_version'):
            versions.setdefault(name, _value(row.get('tool_version')))
            scope.setdefault(name, 'Native module metadata; execution=' + _value(row.get('execution_status')))
            if _reused(row) and 'reused' not in scope[name].lower():
                scope[name] += '; reused result, not a new execution in this attempt'
    runtime_versions = (runtime or {}).get('versions', {})
    tools = list(dict.fromkeys(['snakemake', *tools, *versions, *supplementary_versions, *runtime_versions]))
    software = []
    for name in tools:
        raw = versions.get(name)
        origin_label = scope.get(name, 'Native output metadata; per-step execution not established')
        if _value(raw) == UNKNOWN:
            origin_label = 'Configured; execution must be verified'
        if retrospective and _value(raw) != UNKNOWN:
            origin_label = 'Current output metadata; execution attribution unverified; ' + origin_label
        if _value(raw) == UNKNOWN and name in supplementary_versions:
            raw = supplementary_versions[name]
            origin_label = ('Supplemental output metadata; matched completed attempt' if recovery['status'] == 'recovered'
                            else 'Current output metadata; execution attribution unverified')
            origin_label += '; ' + supplementary.get('version_scopes', {}).get(name, 'Native output metadata')
        if name in runtime_versions:
            if _value(raw) != UNKNOWN and _software_version(name, raw) != _software_version(name, runtime_versions[name]):
                warnings.append('The native output and controller receipt record different ' + name + ' versions; both original records are retained in the provenance ZIP.')
            raw = runtime_versions[name]
            origin_label = (runtime or {}).get('scopes', {}).get(name, 'Recorded in the workflow controller process')
        software.append({'tool': name, 'label': SOFTWARE_LABELS.get(name, name),
                         'version': _software_version(name, raw), 'raw_version': _value(raw), 'scope': origin_label})
    missing_versions = [row['label'] for row in software if row['version'] == UNKNOWN]
    if missing_versions:
        warnings.append('Versions not recorded: ' + ', '.join(missing_versions) + '. Current installations and environment constraints are not used to fill historical gaps.')
    if 'snakemake' not in runtime_versions:
        warnings.append('No attributable Snakemake controller version was recorded for this execution attempt. '
                        'Updating Bactologix cannot reconstruct a missing historical version; the currently installed '
                        'Snakemake version is not substituted. New executions can record their own controller version.')
    warnings.append('Review database release dates, model identities and tool/database citations before submission. A directory name alone is not a database version.')
    intro = (f"This run was configured as a sequence of computational steps orchestrated with Snakemake through Bactologix. "
             f"The workflow implementation was {PIPELINES[run['tool']]['name']} (source commit {run['commit']}). "
             f"The saved computing allocation was {run['cores']} CPU cores and {run['memory_mb']} MB RAM.")
    parameters = []
    supplied = dict(_flatten(explicit))
    for key, value in _flatten(effective):
        parameter_origin = ('Saved run' if key in supplied else 'Inherited screening configuration'
                  if run.get('run_type') == 'followup' else 'Pinned workflow default')
        parameters.append({'setting': key, 'value': _value(value), 'origin': parameter_origin})
    execution = []
    if evidence.get('decisions') and not conflicted:
        execution += [key.replace('_', ' ').capitalize() + ': ' + _value(value) for key, value in evidence['decisions'].items()]
    if evidence.get('native_run'):
        execution.append('Native report scope: ' + _value(evidence['native_run'].get('completeness')))
    if evidence.get('native_run', {}).get('incomplete_modules'):
        execution.append('Native incomplete module identifiers: ' + ', '.join(evidence['native_run']['incomplete_modules']))
    for row in records:
        gaps = _scope_gaps(row, evidence.get('native_run'))
        execution.append('Assessment scope for ' + MODULE_NAMES.get(row.get('module'), row.get('module')) + ': ' +
                         ('incomplete (' + '; '.join(gaps) + ')' if gaps else 'complete' if _module_complete(row) else 'not established'))
        execution.append(f"{MODULE_NAMES.get(row.get('module'), row.get('module'))}: execution={_value(row.get('execution_status'))}; database={_value(row.get('database_version'))}; reused={_value(row.get('reused', row.get('cache_hit')))}; reason={_value(row.get('reason'))}.")
    if not execution:
        execution = ['No per-step execution metadata is available for this export.']
    elif retrospective:
        execution.insert(0, 'The following native records were read from current outputs; their execution attribution is unverified.')
    if runtime:
        execution.append('Workflow controller invocation was recorded from ' + _value(runtime.get('started_at')) +
                         ' to ' + _value(runtime.get('finished_at')) + '; outcome=' + _value(runtime.get('status')) +
                         '. The controller version identifies the orchestration process; individual steps require their own evidence.')
        trace = runtime.get('rule_trace')
        if trace is not None:
            execution = [line for line in execution if line != 'No per-step execution metadata is available for this export.']
            execution.append('Controller rule-event coverage: ' + _value(trace.get('coverage')) + '. '
                             'These are scheduler-declared dependencies and reported outcomes, not observations of file bytes or every internal executable.')
            if not trace.get('jobs'):
                execution.append('No rule events were recorded for this invocation; earlier attempts are not substituted.')
            for row in trace.get('jobs', []):
                execution.append('Rule ' + row['rule'] + ': controller status=' + row['status'] +
                                 '; declared inputs=' + str(len(row['inputs'])) + '; declared outputs=' + str(len(row['outputs'])) + '.')
    if supplementary.get('records'):
        execution.append('Supplemental native module metadata (' + ('matched completed attempt' if recovery['status'] == 'recovered'
                          else 'execution attribution unverified') + '):')
        for row in supplementary['records']:
            execution.append(MODULE_NAMES.get(row.get('module'), row.get('module', 'Module')) +
                             ': native execution status=' + _value(row.get('execution_status')) +
                             '; complete scope=' + ('recorded' if _module_complete(row, supplementary.get('native_run')) else 'not established') +
                             ('; reused result' if _reused(row) else '') + '.')
    if complete and evidence.get('decisions') and not conflicted:
        decisions = evidence['decisions']
        actual = []
        if 'kraken_decision' in decisions:
            actual.append('Kraken decision: ' + _value(decisions['kraken_decision']))
        if 'executed_stages' in decisions:
            actual.append('executed polishing stages: ' + (', '.join(map(str, decisions['executed_stages'])) or 'none'))
        if 'medaka' in decisions.get('executed_stages', []):
            actual.append('effective Medaka model: ' + _value(decisions.get('medaka_model_effective')))
        if decisions.get('assembler'):
            actual.append('recorded assembler: ' + _value(decisions['assembler']))
        if actual:
            label = 'Current output metadata (execution attribution unverified) reports ' if retrospective else 'The retained native record reports '
            paragraphs.append(label + '; '.join(actual) + '.')
    if complete and records:
        completed_modules = [MODULE_NAMES.get(r.get('module'), r.get('module')) + (' (reused result)' if _reused(r) else '') for r in records
                             if _module_complete(r, evidence.get('native_run'))]
        label = ('Current output records (execution attribution unverified) mark these modules complete: '
                 if retrospective else 'The retained module records report completion of ')
        paragraphs.append(label + (', '.join(completed_modules) or 'no modules') +
                          '. Incomplete, excluded and unavailable modules are listed in the execution supplement.')
    if run.get('variant'):
        lineage = run['variant']
        paragraphs.append(f"This run branches from {lineage['parent']}. Eligible existing results were retained as isolated copies; affected workflow steps were scheduled independently.")
        execution.append(f"Result inheritance: {lineage['checkpoints']} checkpoint groups offered for reuse. Native cache candidates may be recomputed if their execution identity differs.")
        warnings.append('Inherited checkpoints are not new tool executions. Parent runtime and resource usage are not attributed to this variant. Inspect variant.json for source records and copied-result identities.')
    recorded = [r['label'] + ' ' + r['version'] for r in software
                if r['version'] != UNKNOWN and r['tool'] not in supplementary_versions
                and r['tool'] not in runtime_versions
                and 'environment package' not in r['scope'].lower()]
    if recorded:
        label = ('Versions reported by current output metadata (execution attribution unverified): '
                 if retrospective else 'Recorded software versions: ')
        paragraphs.append(label + '; '.join(recorded) + '.')
    recovered_versions = [r['label'] + ' ' + r['version'] for r in software
                          if r['version'] != UNKNOWN and r['tool'] in supplementary_versions and r['tool'] not in runtime_versions]
    if recovered_versions:
        attribution = ('matched to the recorded completed attempt' if recovery['status'] == 'recovered'
                       else 'execution attribution unverified')
        paragraphs.append('Versions reported by supplemental ' + PIPELINES[run['tool']]['name'] + ' output metadata (' + attribution + '): ' + '; '.join(recovered_versions) + '.')
    if runtime_versions:
        paragraphs.append('The workflow controller recorded ' + '; '.join(
            r['label'] + ' ' + r['version'] for r in software if r['tool'] in runtime_versions) + ' for this invocation.')
    databases = [] if conflicted else _databases(evidence, 'Current output metadata; attribution unverified' if retrospective else 'Native metadata')
    databases += _databases(supplementary, 'Supplemental metadata; ' + ('matched attempt' if (recovery or {}).get('status') == 'recovered' else 'attribution unverified'))
    from .runs import input_identity
    document = {'id': run['id'], 'state': state, 'generated_at': now(), 'generator_version': __version__,
        'input_identity': input_identity(run), 'input_snapshots': run.get('inputs', []),
        'created_with_bactologix': run.get('bactologix_version', UNKNOWN),
        'pipeline': PIPELINES[run['tool']]['name'], 'source_commit': run['commit'],
        'config_sha256': run['config_sha256'], 'workflow_sha256': run['workflow_sha256'],
        'followup_transition': run.get('followup_transition'),
        'sample_id': run['sample']['id'], 'parent': run.get('parent'), 'completed': run.get('completed') if complete else None,
        'completed_stage': stage['stage'] if stage else ('assembly' if complete and run['tool']=='hybas' else None),
        'stage_narrative': list(stage['paragraphs']) if stage else [],
        'validation_status': VALIDATION_STATUS,
        'paragraphs': [VALIDATION_STATUS, intro, *paragraphs], 'execution': execution, 'software': software, 'databases': databases,
        'parameters': parameters, 'warnings': list(dict.fromkeys(warnings)), 'evidence_origin': origin,
        'evidence': evidence, 'supplemental_evidence': recovery, 'runtime_evidence': runtime,
        'citation_url': f"https://github.com/{PIPELINES[run['tool']]['repo']}/tree/{run['commit']}"}
    from . import methods_flow, methods_narrative
    document['flow'] = methods_flow.build(run, document, effective, known_source=known)
    document['flow_svg'] = methods_flow.svg(document['flow']).decode('utf-8')
    document['workflow_steps'] = document['flow']['tool_steps']
    document['narrative'] = methods_narrative.build(run, document, effective, known_source=known)
    document['warnings'] = list(dict.fromkeys([*document['warnings'], *document['flow'].get('warnings', []),
                                             *document['narrative'].get('warnings', [])]))
    document['text'] = render_text(document)
    return document


def _flow_lines(doc, markdown=False):
    heading = lambda value: ('## ' if markdown else '') + value
    graph = doc.get('flow', {})
    lines = [heading('Run data flow and tool roles'), '', graph.get('caption', ''),
             graph.get('coverage', {}).get('explanation', ''), '']
    controller = next((row for row in doc['software'] if row['tool'] == 'snakemake'), None)
    if controller:
        lines += ['Snakemake ' + controller['version'] + ': coordinates rule dependencies and task scheduling; '
                  'it does not validate the scientific interpretation. Version evidence: ' + controller['scope'] + '.', '']
    for row in doc.get('workflow_steps', []):
        lines += [row['label'] + ' — ' + row.get('status_label', row['status']),
                  'What: ' + row['what'], 'Why: ' + row['why'],
                  'Inputs: ' + ('; '.join(row.get('declared_inputs', row['inputs'])) or 'Not recorded'),
                  'Outputs: ' + ('; '.join(row.get('declared_outputs', row['outputs'])) or 'Not recorded'),
                  'Version: ' + row.get('version', UNKNOWN) + ' (' + row.get('version_scope', 'Not recorded') + ')',
                  'Evidence: ' + row['evidence'], '']
    if not doc.get('workflow_steps'):
        lines += ['Tool-level steps are not available for this source and execution record.', '']
    lines += ['The HTML and SVG exports include the run-specific diagram. The reproducibility ZIP also includes '
              'methods-flow.json with explicit node and relationship evidence.', '']
    return lines


def render_text(doc, markdown=False):
    heading = lambda title: ('## ' if markdown else '') + title
    recovered = doc.get('supplemental_evidence')
    supplemental = []
    if recovered:
        supplemental = ['Supplemental metadata read at: ' + _value(recovered.get('read_at')),
                        'Supplemental metadata status: ' + recovered['status']]
        supplemental += ['Supplemental output SHA256: ' + f['sha256']
                         for f in recovered.get('evidence', {}).get('files', [])]
    narrative = doc.get('narrative', {})
    lines = [('## ' if markdown else '') + 'Methods', '',
             *[p + '\n' for p in narrative.get('paragraphs', doc['paragraphs'])],
             heading('Methods evidence and validation'), '',
             narrative.get('status', ''), doc['validation_status'], '',
             *_flow_lines(doc, markdown),
             heading('Export status and review'), '', doc['state'] + ' · ' + doc['id'],
             'Evidence source: ' + doc['evidence_origin'], *['- ' + w for w in doc['warnings']], '',
             heading('Configuration and evidence notes'), '', *[p + '\n' for p in doc['paragraphs']],
             heading('Recorded execution'), '', *doc['execution'], '', heading('Software versions'), '',
             *[f"{r.get('label', r['tool'])}: {r['version']} ({r['scope']})" for r in doc['software']], '',
             heading('Database identities'), '', *(doc.get('databases') or ['No additional database identity metadata is recorded.']), '',
             heading('Reproducibility'), '', 'Pipeline source: ' + doc['citation_url'],
             'Frozen configuration SHA256: ' + doc['config_sha256'],
             'Workflow copy SHA256: ' + doc['workflow_sha256'],
             *supplemental,
             'Recorded input identity: ' + doc.get('input_identity', 'Not recorded') + '. Export does not recheck current input contents.',
             'Draft created with Bactologix: ' + doc['created_with_bactologix'],
             'Methods generated by Bactologix ' + doc['generator_version'] + ' at ' + doc['generated_at'], '',
             heading('Settings supplement'), '', *[f"{r['setting']} = {r['value']} [{r['origin']}]" for r in doc['parameters']]]
    return '\n'.join(lines) + '\n'


def _html(doc):
    body = ''.join('<p>' + html.escape(p) + '</p>' for p in doc.get('narrative', {}).get('paragraphs', doc['paragraphs']))
    esc = lambda value: html.escape(str(value), quote=True)
    steps = ''.join('<tr><th scope="row">' + esc(row['label']) + '<br><small>' + esc(row.get('status_label', row['status'])) + '</small></th>'
                    '<td>' + esc(row['what']) + '<p><strong>Purpose:</strong> ' + esc(row['why']) + '</p></td>'
                    '<td><strong>Inputs:</strong> ' + esc('; '.join(row.get('declared_inputs', row['inputs'])) or 'Not recorded') +
                    '<p><strong>Outputs:</strong> ' + esc('; '.join(row.get('declared_outputs', row['outputs'])) or 'Not recorded') + '</p></td>'
                    '<td>' + esc(row.get('version', UNKNOWN)) + '<p>' + esc(row['evidence']) + '</p></td></tr>'
                    for row in doc.get('workflow_steps', []))
    flow = ('<section aria-label="Run data flow"><h2>Run data flow</h2><p>' + esc(doc['flow']['caption']) +
            '</p><p>' + esc(doc['flow']['coverage']['explanation']) + '</p><div class="diagram">' + doc['flow_svg'] +
            '</div><details class="full-size"><summary>View diagram at full size</summary><div class="diagram" tabindex="0" role="region" aria-label="Full-size run diagram">' + doc['flow_svg'] + '</div></details><h2>Tools and purpose</h2><div class="table-wrap"><table><thead><tr><th>Step and status</th>'
            '<th>What and why</th><th>Data flow</th><th>Version and evidence</th></tr></thead><tbody>' + steps + '</tbody></table></div></section>')
    return ('<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width">'
            '<title>Methods — ' + html.escape(doc['id']) + '</title><style>body{font:16px/1.6 system-ui;max-width:1100px;margin:40px auto;padding:0 24px;color:#172b30}pre{white-space:pre-wrap;overflow-wrap:anywhere;font:13px/1.5 monospace}h1{font-size:28px}.diagram,.table-wrap{overflow:auto}svg{max-width:100%;height:auto}.full-size .diagram{max-height:85vh}.full-size svg{max-width:none}summary{cursor:pointer;padding:12px 0}.diagram:focus{outline:2px solid #16786d}table{border-collapse:collapse;width:100%;font-size:13px}td,th{border:1px solid #d5dfdf;padding:12px;vertical-align:top;text-align:left;overflow-wrap:anywhere}td p{margin-bottom:0}@media print{.full-size{display:none}body{margin:0}tr{break-inside:avoid}.diagram,.table-wrap{overflow:visible}}</style>'
            '<h1>Methods</h1><p><strong>' + html.escape(doc['state']) + '</strong> · ' + html.escape(doc['id']) + '</p>' + body +
            '<section aria-label="Methods evidence and validation"><h2>Methods evidence and validation</h2><p>' +
            esc(doc.get('narrative', {}).get('status', '')) + '</p><p>' + esc(doc['validation_status']) + '</p></section>' + flow +
            '<h2>Review and reproducibility supplement</h2><pre>' + html.escape(doc['text'].split('Export status and review\n', 1)[1]) + '</pre></html>')


def render_manuscript(doc):
    """Readable Methods only; retain evidence qualifiers inside each paragraph."""
    narrative = doc.get('narrative', {})
    return ('Methods\n\n' + '\n\n'.join(narrative.get('paragraphs', doc['paragraphs'])) +
            '\n\n' + narrative.get('status', '') + '\n' + doc['validation_status'] + '\n')


def download(store, run, format):
    if format not in {'txt', 'md', 'html', 'zip', 'svg', 'json', 'manuscript'}:
        raise ValueError('Choose Methods only, Text, Markdown, HTML, SVG, diagram JSON or the reproducibility ZIP.')
    doc = build(store, run)
    name = run['id'] + '-methods.' + format
    if format == 'manuscript':
        return {'name': run['id'] + '-methods-narrative.txt', 'text': render_manuscript(doc), 'type': 'text/plain'}
    if format in {'svg', 'json'}:
        return {'name': run['id'] + '-methods-flow.' + format,
                'text': doc['flow_svg'] if format == 'svg' else json.dumps(doc['flow'], indent=2, ensure_ascii=False) + '\n',
                'type': 'image/svg+xml' if format == 'svg' else 'application/json'}
    if format != 'zip':
        text = _html(doc) if format == 'html' else render_text(doc, format == 'md')
        return {'name': name, 'text': text, 'type': {'txt': 'text/plain', 'md': 'text/markdown', 'html': 'text/html'}[format]}
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr('methods-narrative.txt', render_manuscript(doc))
        archive.writestr('methods.txt', doc['text'])
        archive.writestr('methods.md', render_text(doc, True))
        archive.writestr('methods.html', _html(doc))
        archive.writestr('methods-flow.svg', doc['flow_svg'])
        archive.writestr('methods-flow.json', json.dumps(doc['flow'], indent=2, ensure_ascii=False) + '\n')
        archive.writestr('config.yaml', run['config'])
        archive.writestr('methods-provenance.json', json.dumps({k: v for k, v in doc.items() if k not in {'text', 'flow_svg'}}, indent=2, ensure_ascii=False) + '\n')
        table = io.StringIO()
        writer = csv.writer(table, delimiter='\t', lineterminator='\n')
        writer.writerow(['setting', 'value', 'origin'])
        for row in doc['parameters']:
            writer.writerow(["'" + v if v.startswith(('=', '+', '-', '@', '\t', '\r', '\n')) else v for v in row.values()])
        archive.writestr('settings.tsv', table.getvalue())
        if doc.get('completed_stage') in {'identification', 'aware', 'assembly'} and doc['evidence'].get('aware_stage'):
            for item in doc['evidence'].get('files', []):
                if not item['path'].startswith(('reports/aware/', 'reports/identification/')):
                    continue
                raw = _read(run['output'], item['path'])
                if raw is None or hashlib.sha256(raw).hexdigest() != item['sha256']:
                    raise ValueError('Stage evidence changed during Methods download.')
                archive.writestr('evidence/' + item['path'], raw)

        citation = _read(Path(run['folder']) / 'workflow', 'CITATION.cff')
        if citation:
            archive.writestr('citations/pipeline-source-CITATION.cff', citation)
        app_citation = yaml.safe_load((DATA / 'CITATION.cff').read_text())
        if run.get('bactologix_version'):
            app_citation['version'] = run['bactologix_version']
        else:
            app_citation.pop('version', None)
        archive.writestr('citations/Bactologix-CITATION.cff', yaml.safe_dump(app_citation, sort_keys=False, allow_unicode=True))
        archive.writestr('README.txt', VALIDATION_STATUS + '\nReview Methods and unresolved items before publication.\nThe YAML, diagram and supplement contain local paths and sample identifiers.\nSoftware versions are recorded evidence, not current environment probes.\nThe diagram distinguishes recorded rule events from source-matched configured dependencies; neither establishes scientific validity.\nCite the pipeline source and tools/databases used; add verified publication references.\nThe workflow configuration and execution behavior were not changed by this export.\n')
    return {'name': name, 'base64': base64.b64encode(buffer.getvalue()).decode(), 'type': 'application/zip'}


