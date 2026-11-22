import base64
from bactologix.trajectory_preview import preview


def test_preview_embeds_owned_plot_and_measurements(tmp_path):
    root = tmp_path/"reports/polishing_trajectory"
    root.mkdir(parents=True)
    (root/"trajectory.svg").write_text("<svg/>")
    (root/"stages.tsv").write_text("stage\ninitial\n")
    source = root/"index.html"
    raw = b'<img src="trajectory.svg"><ul><a href="stages.tsv">data</a><a href="../large.fa">assembly</a>'
    source.write_bytes(raw)
    blob = base64.b64encode(raw).decode()
    rendered = base64.b64decode(preview(blob,source,tmp_path)).decode()
    assert "data:image/svg+xml;base64," in rendered
    assert 'download="stages.tsv"' in rendered
    assert 'href="../large.fa"' not in rendered
    assert source.read_bytes()==raw
    assert preview(blob,root/"unrelated.html",tmp_path)==blob
    (root/"trajectory.svg").unlink()
    (root/"trajectory.svg").symlink_to(root/"stages.tsv")
    assert preview(blob,source,tmp_path)==blob


def test_edition_preserves_embedded_figure_and_embeds_owned_table_only(tmp_path):
    import os
    reports = tmp_path/'run/reports'
    tables = reports/'polishing_trajectory'
    tables.mkdir(parents=True)
    (tables/'stages.tsv').write_text('stage\ndraft\n')
    (tables/'trajectory.svg').write_text('<svg/>')
    unsafe = tmp_path/'unrelated.json'
    unsafe.write_text('private')
    (tables/'unsafe.json').symlink_to(unsafe)
    edition = tmp_path/'editions/current'
    source = edition/'hybas_research/hybas_report.html'
    source.parent.mkdir(parents=True)
    link = os.path.relpath(tables, source.parent)
    page = f'<html data-hybas-report="research"><main><img src="data:image/svg+xml;base64,AAAA"><a href="{link}/stages.tsv">Saved data</a><a href="{link}/unsafe.json">Unrelated</a></main></html>'
    blob = base64.b64encode(page.encode()).decode()
    content = base64.b64decode(preview(blob, source, reports.parent, edition)).decode()
    assert 'download="stages.tsv"' in content and content.count('<img') == 1
    assert 'download="unsafe.json"' not in content


def test_original_research_preview_keeps_native_polishing_navigation(tmp_path):
    trajectory = tmp_path / "reports/polishing_trajectory"
    trajectory.mkdir(parents=True)
    (trajectory / "trajectory.svg").write_text("<svg/>")
    source = tmp_path / "reports/hybas_research/hybas_report.html"
    source.parent.mkdir()
    page = ("<html lang='en' data-hybas-report='research'><main>"
            "<a href='#assembly'>Assembly</a><section id='assembly'>"
            "<a href='../polishing_trajectory/index.html'>Explore the polishing trajectory</a>"
            "</section></main></html>")
    source.write_text(page)
    blob = base64.b64encode(page.encode()).decode()
    assert preview(blob, source, tmp_path) == blob
