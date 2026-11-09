"""Portable evidence-based sample history; never infer execution from a file.

Edges record lineage and containment, not guessed native task dependencies.
"""
from __future__ import annotations

from collections import defaultdict, deque
import hashlib
import html
import json
import math
import textwrap

from . import __version__
from .store import now

SCHEMA = 'bactologix-sample-history-v1'
CAPTION = ('Arrows show recorded run lineage and contained attempts or evidence. '
           'Statuses describe computational records; they do not establish scientific validity. '
           'Inherited outputs and report rendering are separate from new analyses. '
           'Detailed native task dependencies and unrecorded historical steps remain unknown.')
LIMITATIONS = [
    'A complete native task-by-task execution DAG was not retained by older application versions.',
    'A successful workflow attempt does not establish that every requested module completed.',
    'Native summaries cover their reported scope. Missing steps are not reconstructed from settings, filenames or progress logs.',
    'Legacy Methods snapshots may cover only the most recently captured successful execution. Other attempts retain their recorded status.',
]
PIPELINES = {'hybas': 'HybAs', 'basis': 'BaSIS', 'micpscope': 'GeneScope'}
ACTIONS = {'run': 'Analysis attempt', 'continue': 'Continuation attempt', 'dry-run': 'Configuration check',
           'prepare': 'Environment preparation', 'check-environments': 'Environment check'}
STATES = {'succeeded', 'completed', 'executed', 'failed', 'cancelled', 'interrupted', 'queued', 'running',
          'skipped', 'excluded', 'not_requested', 'not_applicable', 'unavailable', 'incomplete', 'unknown',
          'draft', 'registered', 'awaiting_review', 'stage_complete', 'reused', 'planned'}


def _text(value):
    return ''.join(c for c in str(value if value is not None else '') if ord(c) >= 32 or c in '\n\t')


def _id(kind, value):
    return kind + ':' + str(value)


def _state(value):
    return value if isinstance(value, str) and value in STATES else 'unknown'


def _hash(value, length=64):
    return isinstance(value, str) and len(value) == length and set(value) <= set('0123456789abcdef')


def _json(value):
    return json.dumps(value, indent=2, ensure_ascii=False, sort_keys=True) + '\n'


def _run_state(run, attempts):
    executions = [j for j in attempts if j.get('kind') == 'workflow' and j.get('payload', {}).get('action') in {'run', 'continue'}]
    latest = executions[0] if executions else None
    if latest and latest.get('status') in {'failed', 'cancelled', 'interrupted', 'running', 'queued'}:
        return latest['status'], latest
    if run.get('identification', {}).get('state') == 'awaiting_review':
        return 'awaiting_review', latest
    if run.get('aware', {}).get('state') == 'complete' and not run.get('completed'):
        return 'stage_complete', latest
    if latest and latest.get('status') == 'succeeded' and run.get('completed'):
        return 'completed', latest
    return ('unknown' if run.get('completed') or latest else 'draft'), latest


def _levels(nodes, edges):
    by_id = {n['id']: n for n in nodes}
    incoming = dict.fromkeys(by_id, 0)
    outgoing = defaultdict(list)
    for edge in edges:
        incoming[edge['target']] += 1
        outgoing[edge['source']].append(edge['target'])
    queue, count = deque(key for key in by_id if incoming[key] == 0), 0
    while queue:
        key = queue.popleft()
        count += 1
        for target in outgoing[key]:
            by_id[target]['level'] = max(by_id[target].get('level', 0), by_id[key].get('level', 0) + 1)
            incoming[target] -= 1
            if incoming[target] == 0:
                queue.append(target)
    if count != len(nodes):
        raise ValueError('Recorded sample history contains a cycle.')
    for node in nodes:
        node.setdefault('level', 0)


def build(store, sample_id):
    """JSON graph from one inventory snapshot; jobs retain newest-first order.

    Nodes expose id,label,kind,status,run_id,started,finished,evidence,details,level.
    Edges expose source,target,relation. Warnings and partial coverage are explicit.
    """
    from . import isolate_inventory
    inventory = isolate_inventory.build(store, sample_id)
    sample = inventory.get('sample') or {'id': sample_id}
    warnings = [str(w) for w in inventory.get('warnings', [])]
    runs, jobs = {}, {}
    for run in inventory.get('runs', []):
        if not isinstance(run, dict) or not isinstance(run.get('id'), str) or run['id'] in runs:
            warnings.append('An invalid or duplicate run record was omitted.')
            continue
        runs[run['id']] = run
    for job in inventory.get('jobs', []):
        if not isinstance(job, dict) or not isinstance(job.get('id'), str) or job['id'] in jobs:
            warnings.append('An invalid or duplicate attempt record was omitted.')
            continue
        if not isinstance(job.get('payload'), dict) or job['payload'].get('run_id') not in runs:
            warnings.append('An attempt without an included run was omitted.')
            continue
        jobs[job['id']] = job
    records = [r for r in inventory.get('records', []) if isinstance(r, dict)]
    record_by_id = {(r.get('kind'), r.get('id')): r.get('body') for r in records}
    nodes, edges, outgoing, edge_keys, by_id = [], [], defaultdict(set), set(), {}

    def node(key, label, kind, status, evidence, details=None, run_id=None, started=None, finished=None):
        if key not in by_id:
            item = dict(id=key, label=_text(label), kind=kind, status=_state(status), evidence=_text(evidence),
                        details=details or {}, run_id=run_id, started=started, finished=finished)
            nodes.append(item)
            by_id[key] = item
        return by_id[key]

    def edge(source, target, relation):
        if source not in by_id or target not in by_id:
            warnings.append('An edge references a missing record: ' + str(source) + ' → ' + str(target))
            return False
        key = (source, target, relation)
        if key in edge_keys:
            return True
        pending, seen = [target], set()
        while pending:
            current = pending.pop()
            if current == source:
                warnings.append('A cyclic lineage edge was omitted: ' + source + ' → ' + target)
                return False
            if current not in seen:
                seen.add(current)
                pending.extend(outgoing[current])
        edge_keys.add(key)
        outgoing[source].add(target)
        edges.append(dict(source=source, target=target, relation=_text(relation)))
        return True

    sample_key = _id('sample', sample.get('id', sample_id))
    node(sample_key, sample.get('id', sample_id), 'sample', 'registered', 'Selected registration or retained sample record',
         {'sample_id': sample.get('id', sample_id), 'input_folder': sample.get('path'), 'read_mode': sample.get('mode')})
    ownership = {r.get('id'): r.get('body') for r in records if r.get('kind') == 'followup_output_owner'}
    for rid, run in runs.items():
        attempts = [j for j in jobs.values() if j['payload']['run_id'] == rid]
        status, latest = _run_state(run, attempts)
        details = {key: run.get(key) for key in ('id', 'tool', 'created', 'completed', 'commit', 'config_sha256',
                   'workflow_sha256', 'bactologix_version', 'parent', 'run_type', 'output')}
        details.update(latest_execution_id=latest.get('id') if latest else None,
                       latest_execution_status=latest.get('status') if latest else None,
                       step_coverage='Partial native summaries; complete task history is not recorded', input_snapshots=run.get('inputs', []))
        if status == 'unknown':
            details['status_reason'] = 'Overall execution identity is incomplete; legacy completion or file presence is insufficient.'
        if run.get('run_type') == 'followup':
            details.update(shared_output_owner=ownership.get(run.get('parent')), shared_output_available=ownership.get(run.get('parent')) == rid)
            if not details['shared_output_available']:
                warnings.append(rid + ': shared follow-up outputs are no longer attributed to this run; retained history remains visible.')
        if run.get('variant'):
            details.update(result_inheritance=run['variant'], inheritance_manifest_sha256=run.get('variant_manifest_sha256'))
        node(_id('run', rid), PIPELINES.get(run.get('tool'), str(run.get('tool', 'Pipeline'))) + ' · ' + str(run.get('label') or rid),
             'run', status, 'Saved run and recorded execution attempts', details, rid,
             latest.get('started') if latest else None, latest.get('finished') if latest else None)
    lineage_targets = set()
    for item in inventory.get('edges', []):
        if not isinstance(item, dict):
            warnings.append('An invalid lineage edge was omitted.')
            continue
        source, target = item.get('source'), item.get('target')
        if edge(_id('run', source), _id('run', target), item.get('relation') or 'recorded lineage'):
            lineage_targets.add(target)
    for rid in runs:
        if rid not in lineage_targets:
            edge(sample_key, _id('run', rid), 'included in selected history')
    for jid, job in jobs.items():
        rid, action = job['payload']['run_id'], job['payload'].get('action')
        rendering = job.get('kind') == 'report_refresh'
        label = 'Report rendering' if rendering else ACTIONS.get(action, str(action or job.get('kind', 'Recorded job')))
        details = dict(job_id=jid, action=action, job_kind=job.get('kind'), status=job.get('status'), created=job.get('created'),
                       started=job.get('started'), finished=job.get('finished'), error=job.get('error'), step_coverage='not_recorded')
        observed = record_by_id.get(('report_evidence', jid))
        if isinstance(observed, dict) and observed.get('job_id') == jid and observed.get('run', {}).get('id') == rid:
            details['observed_outputs'] = observed.get('after', {}).get('files', {})
            details['output_attribution'] = 'Content observed during this attempt; this does not prove a native producer.'
        edition = record_by_id.get(('report_refreshes', jid))
        if rendering and isinstance(edition, dict):
            details['report_edition'] = edition
        node(_id('attempt', jid), label + ' · ' + jid, 'rendering' if rendering else 'attempt', job.get('status'),
             'Stored rendering job; separate from analysis' if rendering else 'Stored job record', details, rid, job.get('started'), job.get('finished'))
        edge(_id('run', rid), _id('attempt', jid), 'report rendering' if rendering else 'recorded attempt')

    def stage(jid, name, status, evidence, detail):
        key = _id('stage', jid + ':' + hashlib.sha256(str(name).encode()).hexdigest()[:16])
        if key in by_id and by_id[key]['status'] == 'planned' and _state(status) != 'planned':
            detail = dict(detail, planned=True)
            by_id[key].update(status=_state(status), evidence=evidence, details=detail)
        if key in by_id and by_id[key]['status'] != _state(status):
            warnings.append('Conflicting stage states retained for ' + jid + ': ' + str(name))
            by_id[key]['details']['conflicting_states'] = [by_id[key]['status'], _state(status)]
            by_id[key]['status'] = 'unknown'
        node(key, name, 'stage', status, evidence, detail, jobs[jid]['payload']['run_id'])
        edge(_id('attempt', jid), key, 'reported stage evidence')
        by_id[_id('attempt', jid)]['details']['step_coverage'] = 'partial'
        return by_id[key]

    receipts = sorted((r for r in records if r.get('kind') in {'methods_attempt_evidence', 'methods_evidence'}),
                      key=lambda r: r.get('kind') != 'methods_attempt_evidence')
    seen_receipts = set()
    for record in receipts:
        receipt = record.get('body')
        if not isinstance(receipt, dict):
            continue
        rid, jid = receipt.get('run_id'), receipt.get('job_id')
        run, job = runs.get(rid), jobs.get(jid)
        if not run or jid in seen_receipts:
            continue
        seen_receipts.add(jid)
        if (receipt.get('schema') != 1 or not job or job['payload'].get('run_id') != rid
                or job.get('kind') != 'workflow' or job['payload'].get('action') not in {'run', 'continue'}
                or record.get('id') != (jid if record['kind'] == 'methods_attempt_evidence' else rid)
                or any(not run.get(k) or receipt.get(k) != run.get(k) for k in ('config_sha256', 'workflow_sha256', 'commit'))
                or not _hash(receipt.get('config_sha256')) or not _hash(receipt.get('workflow_sha256')) or not _hash(receipt.get('commit'), 40)
                or not isinstance(receipt.get('evidence'), dict)):
            warnings.append(rid + ': a Methods snapshot lacks matching run/attempt/source identity and was omitted as stage evidence.')
            continue
        evidence = receipt['evidence']
        base = dict(run_id=rid, job_id=jid, captured_at=receipt.get('captured_at'), source_commit=receipt.get('commit'),
                    config_sha256=receipt.get('config_sha256'), workflow_sha256=receipt.get('workflow_sha256'),
                    evidence_files=evidence.get('files', []), recorded_versions=evidence.get('versions', {}), receipt_kind=record['kind'])
        by_id[_id('attempt', jid)]['details']['methods_receipt'] = dict(base, native_run=evidence.get('native_run'))
        for warning in evidence.get('warnings', []):
            warnings.append(rid + ': ' + str(warning))
        decisions = evidence.get('decisions') or {}
        planned = decisions.get('planned_stages', []) if isinstance(decisions, dict) else []
        executed = decisions.get('executed_stages', []) if isinstance(decisions, dict) else []
        skipped = decisions.get('skipped_stages', {}) if isinstance(decisions, dict) else {}
        executed = [name for name in executed if isinstance(name, str) and name] if isinstance(executed, list) else []
        for name in executed:
            stage(jid, name, 'executed', 'Native stage summary in a matching saved Methods snapshot',
                  dict(base, recorded_state='executed', scope='Reported execution; quality improvement is not established'))
        if isinstance(skipped, dict):
            for name, reason in skipped.items():
                if not isinstance(name, str) or not name:
                    continue
                item = stage(jid, name, 'skipped', 'Native skip reason in a matching saved Methods snapshot', dict(base, reason=reason))
                if name in executed:
                    warnings.append(rid + ': conflicting executed/skipped evidence for ' + name)
                    item['status'] = 'unknown'
                    item['details']['conflicting_skipped_reason'] = reason
        if isinstance(planned, list):
            for name in planned:
                if isinstance(name, str) and name and name not in executed and (not isinstance(skipped, dict) or name not in skipped):
                    stage(jid, name, 'planned', 'Plan retained in a matching saved Methods snapshot',
                          dict(base, scope='Planned stage; its execution is not established by this snapshot'))
        native_rows = evidence.get('records', [])
        if isinstance(native_rows, list):
            for row in native_rows:
                if not isinstance(row, dict) or not isinstance(row.get('module'), str):
                    continue
                state = _state(row.get('execution_status'))
                if state == 'completed' and (row.get('complete') is False or row.get('upstream_incomplete')):
                    state = 'incomplete'
                stage(jid, row['module'], state, 'Native module record in a matching saved Methods snapshot', dict(base, **row))
    for rid, run in runs.items():
        for field, name, checksum in [('identification', 'Identification stage', 'evidence_sha256'), ('aware', 'AWARE stage', 'bundle_sha256')]:
            receipt = run.get(field) or {}
            jid = receipt.get('job')
            if not receipt.get(checksum):
                continue
            if (jid not in jobs or jobs[jid]['payload']['run_id'] != rid or jobs[jid].get('kind') != 'workflow'
                    or jobs[jid]['payload'].get('action') not in {'run', 'continue'} or not receipt.get('completed') or not _hash(receipt[checksum])):
                warnings.append(rid + ': retained ' + field + ' receipt has no matching attempt identity.')
                continue
            stage(jid, name, 'completed', 'Stage-completion receipt retained by Bactologix',
                  dict(receipt, scope='This stage only; downstream completion is separate'))
    _levels(nodes, edges)
    nodes = [n for _, n in sorted(enumerate(nodes), key=lambda pair: (pair[1]['level'], pair[0]))]
    for index, item in enumerate(nodes, 1):
        item['number'] = index
    return dict(schema=SCHEMA, title='Recorded analysis history · ' + str(sample.get('id', sample_id)), sample=sample,
                generated_at=now(), generator_version=__version__, inventory_signature=inventory.get('signature'), caption=CAPTION,
                coverage=dict(state='partial', label='Recorded history; detailed step coverage is partial'),
                nodes=nodes, edges=edges, warnings=list(dict.fromkeys(warnings)), missing=inventory.get('missing', []), limitations=LIMITATIONS)


def _wrap(value, width, lines=None):
    result = textwrap.wrap(_text(value), width=width, break_long_words=True, break_on_hyphens=False) or ['']
    if lines and len(result) > lines:
        result = result[:lines]
        result[-1] = result[-1][:-1] + '…'
    return result


def _colors(status):
    if status in {'failed', 'cancelled', 'interrupted', 'incomplete'}:
        return '#fff1ef', '#a33832'
    if status in {'completed', 'succeeded', 'executed'}:
        return '#eaf6f2', '#156d61'
    if status in {'unknown', 'awaiting_review', 'stage_complete', 'unavailable'}:
        return '#fff7e5', '#8d5c16'
    return '#f1f5f7', '#445e6a'


def svg(graph):
    """Standalone vector graph; accessible titles retain unabridged labels."""
    width, card, gap, positions, y = 1120, 320, 16, {}, 150
    grouped = defaultdict(list)
    for item in graph['nodes']:
        grouped[item['level']].append(item)
    for level in sorted(grouped):
        for i, item in enumerate(grouped[level]):
            positions[item['id']] = (24 + (i % 3) * (card + gap), y + (i // 3) * 130)
        y += math.ceil(len(grouped[level]) / 3) * 130 + 35
    height = y + 90
    esc = lambda s: html.escape(_text(s), quote=True)
    rows = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" role="img" aria-labelledby="title desc">',
            '<title id="title">' + esc(graph['title']) + '</title><desc id="desc">' + esc(CAPTION) + '</desc>',
            '<defs><marker id="arrow" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto"><path d="M0 0 L8 4 L0 8Z" fill="#78908e"/></marker></defs>',
            f'<rect width="{width}" height="{height}" fill="white"/>', '<g font-family="Arial, sans-serif" fill="#173c40">',
            '<text x="24" y="34" font-size="23" font-weight="bold">' + esc(graph['title']) + '</text>']
    for i, line in enumerate(_wrap(CAPTION, 140)):
        rows.append(f'<text x="24" y="{59+i*18}" font-size="12">{esc(line)}</text>')
    for index, item in enumerate(graph['edges']):
        sx, sy = positions[item['source']]
        tx, ty = positions[item['target']]
        sx, sy, tx = sx + card / 2, sy + 104, tx + card / 2
        # Route through row gaps and an outside gutter. Long edges must not
        # disappear behind intermediate nodes and imply a false dependency.
        lane = 1030 + (index % 12) * 6
        rows.append(f'<path d="M{sx} {sy} V{sy+12} H{lane} V{ty-12} H{tx} V{ty-4}" fill="none" stroke="#78908e" stroke-width="1.2" marker-end="url(#arrow)"><title>{esc(item["relation"])}</title></path>')
    for item in graph['nodes']:
        x, py = positions[item['id']]
        background, color = _colors(item['status'])
        rows += [f'<g id="node-{item["number"]}"><title>{esc(item["label"]+"; "+item["status"]+"; "+item["evidence"])}</title>',
                 f'<rect x="{x}" y="{py}" width="{card}" height="104" rx="9" fill="{background}" stroke="{color}"/>',
                 f'<text x="{x+12}" y="{py+20}" font-size="10" fill="{color}">{item["number"]} · {esc(item["kind"].upper())} · {esc(item["status"])}</text>']
        for i, line in enumerate(_wrap(item['label'], 43, 2)):
            rows.append(f'<text x="{x+12}" y="{py+43+i*17}" font-size="13" font-weight="bold">{esc(line)}</text>')
        rows.append(f'<text x="{x+12}" y="{py+88}" font-size="10">{esc(_wrap(item["evidence"], 50, 1)[0])}</text></g>')
    rows.append(f'<text x="24" y="{height-48}" font-size="12">Partial history · {len(graph["warnings"])} warning(s). Full details accompany this figure in HTML and JSON.</text>')
    rows.append(f'<text x="24" y="{height-27}" font-size="11">Generated {esc(graph["generated_at"])} · Bactologix {esc(graph["generator_version"])}</text></g></svg>')
    return '\n'.join(rows).encode('utf-8')


def _html(graph, drawing):
    esc = lambda s: html.escape(_text(s), quote=True)
    details = []
    for item in graph['nodes']:
        details.append(f'<details id="detail-{item["number"]}"><summary>{item["number"]}. {esc(item["label"])} — {esc(item["status"])}</summary><p>{esc(item["evidence"])}</p><pre>{esc(_json(item))}</pre></details>')
    warnings = ''.join('<li>' + esc(w) + '</li>' for w in graph['warnings'] + graph['limitations'])
    edges = ''.join('<li>' + esc(e['source']) + ' → ' + esc(e['target']) + ' (' + esc(e['relation']) + ')</li>' for e in graph['edges'])
    return (f'<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>{esc(graph["title"])}</title>'
            '<style>body{font:15px/1.6 system-ui,sans-serif;color:#173c40;background:#f3f7f7;margin:0}main{max-width:1120px;margin:32px auto;padding:24px;background:white;border-radius:14px}h1{font-size:28px}a{color:#08786d}.figure{overflow:auto}svg{max-width:100%;height:auto}details{border:1px solid #cbdcda;border-radius:8px;padding:14px;margin:12px 0}summary{cursor:pointer;font-weight:600}pre{white-space:pre-wrap;overflow-wrap:anywhere;font-size:12px}.notice{background:#fff7e5;padding:16px;border-radius:8px}li{overflow-wrap:anywhere}@media print{body{background:white}main{margin:0;padding:0}details{break-inside:avoid}}</style>'
            f'<main><h1>{esc(graph["title"])}</h1><p>{esc(CAPTION)}</p><nav><a href="#diagram">Diagram</a> · <a href="#details">Evidence details</a> · <a href="history.json">JSON</a> · <a href="history.svg">SVG</a> · <a href="history.pdf">PDF</a></nav>'
            f'<p class="notice">{esc(graph["coverage"]["label"])}</p><section class="figure" id="diagram">{drawing.decode("utf-8")}</section>'
            '<h2 id="details">Recorded node details</h2>' + ''.join(details) + '<h2>Recorded relationships</h2><ul>' + edges + '</ul><h2>Scope and unresolved records</h2><ul>' + warnings + '</ul></main></html>').encode('utf-8')


def _pdf_string(value):
    # Base-14 fonts use Windows-1252. Unsupported codepoints remain visible;
    # companion HTML/JSON retains original UTF-8 rather than losing characters.
    value = ''.join(c if c.encode('cp1252', 'replace') != b'?' or c == '?' else f'[U+{ord(c):04X}]' for c in _text(value))
    return value.replace('\\', '\\\\').replace('(', '\\(').replace(')', '\\)').encode('cp1252', 'replace')


def pdf(graph):
    """Small paginated vector PDF with explicit cross-page node references."""
    objects, pages, nodes, per_page = [], [], graph['nodes'], 8
    numbers = {n['id']: n['number'] for n in nodes}
    page_of = {n['id']: i // per_page + 1 for i, n in enumerate(nodes)}

    def add(raw):
        objects.append(raw)
        return len(objects)

    catalog, page_tree = add(b''), add(b'')
    font = add(b'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>')
    for offset in range(0, max(1, len(nodes)), per_page):
        chunk, page_number, commands = nodes[offset:offset+per_page], offset // per_page + 1, []
        positions = {n['id']: (30+(i % 2)*280, 590-(i // 2)*135) for i, n in enumerate(chunk)}

        def text(x, y, size, value):
            commands.append(f'BT /F1 {size} Tf {x} {y} Td ('.encode() + _pdf_string(value) + b') Tj ET\n')

        def line(x1, y1, x2, y2):
            commands.append(f'{x1} {y1} m {x2} {y2} l S\n'.encode())

        commands.append(b'0.09 0.23 0.25 rg 0.46 0.56 0.55 RG 0.6 w\n')
        text(30, 802, 17, 'Recorded analysis history')
        text(30, 782, 11, str(graph['sample'].get('id', 'Sample')))
        for i, item in enumerate(_wrap(CAPTION, 102)):
            text(30, 762-i*11, 8, item)
        for edge_index, edge in enumerate(graph['edges']):
            if edge['source'] not in positions or edge['target'] not in positions:
                continue
            sx, sy = positions[edge['source']]
            tx, ty = positions[edge['target']]
            if sy == ty:
                line(sx+250, sy+59, tx-4, ty+59)
                line(tx-4, ty+59, tx-9, ty+62)
                line(tx-4, ty+59, tx-9, ty+56)
            else:
                gutter = 571 + (edge_index % 5)*4
                line(sx+125, sy, sx+125, sy-8)
                line(sx+125, sy-8, gutter, sy-8)
                line(gutter, sy-8, gutter, ty+126)
                line(gutter, ty+126, tx+125, ty+126)
                line(tx+125, ty+126, tx+125, ty+122)
                line(tx+125, ty+122, tx+122, ty+127)
                line(tx+125, ty+122, tx+128, ty+127)
        for item in chunk:
            x, y = positions[item['id']]
            commands.append(f'1 1 1 rg {x} {y} 250 118 re B 0.09 0.23 0.25 rg\n'.encode())
            text(x+8, y+102, 8, f'{item["number"]} · {item["kind"]} · {item["status"]}')
            for i, value in enumerate(_wrap(item['label'], 43, 2)):
                text(x+8, y+84-i*13, 10, value)
            for i, value in enumerate(_wrap(item['evidence'], 53, 2)):
                text(x+8, y+51-i*10, 7, value)
            incoming = [e['source'] for e in graph['edges'] if e['target'] == item['id']]
            outgoing_ids = [e['target'] for e in graph['edges'] if e['source'] == item['id']]
            reference = lambda ids: ', '.join(str(numbers[key])+(' (p'+str(page_of[key])+')' if page_of[key] != page_number else '') for key in ids) or 'none'
            for i, value in enumerate(_wrap('From: '+reference(incoming)+'; To: '+reference(outgoing_ids), 64, 2)):
                text(x+8, y+22-i*9, 6.7, value)
        text(30, 145, 8, 'Arrows on each page and cross-page node references describe recorded relationships.')
        text(30, 132, 8, f'Partial history; {len(graph["warnings"])} warning(s). Full node details and limitations accompany the HTML/JSON.')
        text(30, 119, 8, 'Unsupported PDF-font characters use [U+codepoint]; companion files retain the original Unicode text.')
        text(30, 42, 8, f'Bactologix {graph["generator_version"]} · {graph["generated_at"]} · Page {page_number}/{max(1, math.ceil(len(nodes)/per_page))}')
        raw = b''.join(commands)
        content = add(b'<< /Length '+str(len(raw)).encode()+b' >>\nstream\n'+raw+b'endstream')
        pages.append(add(f'<< /Type /Page /Parent {page_tree} 0 R /MediaBox [0 0 595 842] /Resources << /Font << /F1 {font} 0 R >> >> /Contents {content} 0 R >>'.encode()))
    objects[catalog-1] = f'<< /Type /Catalog /Pages {page_tree} 0 R >>'.encode()
    objects[page_tree-1] = ('<< /Type /Pages /Count '+str(len(pages))+' /Kids ['+' '.join(str(p)+' 0 R' for p in pages)+'] >>').encode()
    result, offsets = bytearray(b'%PDF-1.4\n%\xe2\xe3\xcf\xd3\n'), [0]
    for i, obj in enumerate(objects, 1):
        offsets.append(len(result))
        result.extend(f'{i} 0 obj\n'.encode()+obj+b'\nendobj\n')
    start = len(result)
    result.extend(f'xref\n0 {len(offsets)}\n0000000000 65535 f \n'.encode())
    result.extend(b''.join(f'{position:010d} 00000 n \n'.encode() for position in offsets[1:]))
    result.extend(f'trailer\n<< /Size {len(offsets)} /Root {catalog} 0 R >>\nstartxref\n{start}\n%%EOF\n'.encode())
    return bytes(result)


def bundle_files(store, sample_id):
    """Return safe relative filenames -> bytes, without external services."""
    graph = build(store, sample_id)
    drawing = svg(graph)
    return {'history.json': _json(graph).encode('utf-8'), 'history.svg': drawing,
            'history.html': _html(graph, drawing), 'history.pdf': pdf(graph)}
