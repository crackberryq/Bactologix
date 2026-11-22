"""Resolve owned trajectory assets for the isolated desktop report frame."""
import base64
import re
from pathlib import Path


def small_asset(root, name, limit):
    path = root / name
    if path.is_symlink() or not path.is_file() or path.stat().st_size > limit:
        return None
    with path.open("rb") as source:
        data = source.read(limit + 1)
    return data if len(data) <= limit else None


def preview(encoded, source, output):
    if not output:
        return encoded
    reports = Path(output).resolve() / "reports"
    root = reports / "polishing_trajectory"
    research = reports / "hybas_research/hybas_report.html"
    index = root / "index.html"
    source = Path(source).resolve()
    if reports.resolve() != reports or root.resolve() != root or source not in (index, research):
        return encoded
    svg = small_asset(root, "trajectory.svg", 2 * 1024 * 1024)
    if not svg:
        return encoded
    figure = "data:image/svg+xml;base64," + base64.b64encode(svg).decode("ascii")
    page = base64.b64decode(encoded).decode("utf-8")
    if source == research:
        page = page.replace('src="../polishing_trajectory/trajectory.svg"', f'src="{figure}"')
        page = page.replace('href="../polishing_trajectory/index.html"', 'href="#trajectory-evidence"')
        if figure not in page:
            page = page.replace("</main>", '<section><h2>Polishing trajectory</h2><img style="max-width:100%" '
                                f'src="{figure}" alt="Measured polishing stages"></section></main>')
        page = page.replace("</main>", '<section id="trajectory-evidence"><h3>Polishing evidence</h3>'
                            '<p>Open the Polishing trajectory report for stage and transition tables.</p>'
                            '<a href="stages.tsv">Stage measurements</a> · '
                            '<a href="transitions.tsv">Transition measurements</a></section></main>')
    else:
        page = page.replace('src="trajectory.svg"', f'src="{figure}"')
    types = {"stages.tsv": "text/tab-separated-values", "transitions.tsv": "text/tab-separated-values",
             "marker_changes.tsv": "text/tab-separated-values", "recipe.json": "application/json",
             "geometry.json": "application/json", "trajectory.svg": "image/svg+xml"}
    for name, mime in types.items():
        attribute = f'href="{name}"'
        if attribute not in page:
            continue
        data = small_asset(root, name, 2 * 1024 * 1024)
        if data is not None:
            uri = base64.b64encode(data).decode("ascii")
            page = page.replace(attribute, f'download="{name}" href="data:{mime};base64,{uri}"')
    if source == index:
        # Large native files are opened from their report folder on the workstation.
        page = re.sub(r'<a href="(?!data:|#)[^"]+">([^<]+)</a>', r'<span>\1</span>', page)
        page = page.replace("<ul>", '<p><a href="metadata/files.html">Run files</a></p><ul>', 1)
    return base64.b64encode(page.encode()).decode("ascii")
