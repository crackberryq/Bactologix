"""Render saved JSON using one explicitly selected, installed pipeline source."""
import html
import json
from pathlib import Path
import sys


def render(snapshot, plan_file, outdir):
    plan = json.loads(Path(plan_file).read_text())
    data = json.loads(Path(snapshot).read_text())
    out = Path(outdir)
    out.mkdir(parents=True, exist_ok=False)
    source = Path(plan['source'])
    if plan['tool'] == 'micpscope':
        sys.path.insert(0, str(source / 'src'))
        from micpscope.report import render_report
        (out / 'report.html').write_text(render_report(data), encoding='utf-8')
    elif plan['tool'] == 'hybas':
        sys.path.insert(0, str(source / 'workflow/lib'))
        from hybas_report import render_report
        (out / 'report.html').write_text(render_report(data), encoding='utf-8')
    elif plan['tool'] == 'basis':
        package_root = source if (source / 'basis/report.py').is_file() else source / 'workflow/vendor'
        sys.path.insert(0, str(package_root))
        from basis.report import write_report
        write_report(data['run'], data['samples'], data['records'], data['deep_records'], out)
    else:
        raise ValueError('No saved-data renderer for this pipeline.')
    report = out / 'report.html'
    content = report.read_text(encoding='utf-8')
    e = lambda v: html.escape(str(v), quote=True)
    banner = ('<section aria-label="Report edition provenance" style="margin:16px;padding:16px;border:1px solid #ccdce2;background:#f4f8fa;color:#173443;font:14px/1.6 system-ui">'
              '<strong>Refreshed report · saved evidence only</strong><br>Analysis version: ' + e(plan['analysis_version']) +
              ' · Report renderer: ' + e(plan['renderer_version']) + '<br>Source commit recorded for evidence attempt: ' + e(plan.get('analysis_commit') or 'unverified / not recorded') +
              '<br>Latest computational execution: <strong>' + e(plan.get('analysis_status', 'unknown')) + '</strong>' +
              '<br>Latest execution attempt: ' + e(plan.get('analysis_job_id') or 'not recorded') +
              '<br>Evidence attribution: ' + e(plan.get('evidence_identity', {}).get('label', 'Evidence attribution unverified')) +
              '<br>Evidence attempt: ' + e(plan.get('evidence_identity', {}).get('job_id') or 'not recorded') +
              ' · Attempt status: ' + e(plan.get('evidence_identity', {}).get('attempt_status', 'unknown')) +
              '<br>Assessment coverage: ' + e(plan.get('assessment', {}).get('label', 'Coverage unknown')) +
              ''.join('<p>' + e(reason) + '</p>' for reason in plan.get('assessment', {}).get('reasons', [])) +
              '<br>Renderer commit: ' + e(plan['renderer_commit']) + '<br>Saved evidence SHA-256: <code style="overflow-wrap:anywhere">' + e(plan['evidence_sha256']) +
              '</code><p>No new analyses were run. Original analysis versions inside this report remain unchanged.</p>' +
              ''.join('<p>' + e(n) + '</p>' for n in plan['notes']) + '</section>')
    # Insert into the main column if present, keeping the sidebar/grid intact.
    import re
    match = re.search(r'<main\b[^>]*>', content, re.I) or re.search(r'<body\b[^>]*>', content, re.I)
    if not match:
        raise ValueError('Renderer did not return an HTML document.')
    content = content[:match.end()] + banner + content[match.end():]
    report.write_text(content, encoding='utf-8')


if __name__ == '__main__':
    render(*sys.argv[1:])
