"""Resolve owned trajectory assets for the isolated desktop report frame."""
import base64
import html
import os
from urllib.parse import unquote, urlsplit
import re
from pathlib import Path


def small_asset(root, name, limit):
    path = root / name
    if path.is_symlink() or not path.is_file() or path.stat().st_size > limit:
        return None
    with path.open("rb") as source:
        data = source.read(limit + 1)
    return data if len(data) <= limit else None


def linked_assets(page, source, roots):
    """Embed small, linked report tables within the owning run or report edition."""
    from .results import read_file
    budget = 8 * 1024 * 1024
    owned = [p for p in roots if p.resolve() == p]
    def replace(match):
        nonlocal budget
        href = urlsplit(html.unescape(match.group(2)))
        if href.scheme or href.netloc or not href.path or href.path.startswith('/'):
            return match.group()
        path = Path(os.path.abspath(source.parent / unquote(href.path)))
        if path.suffix not in ('.tsv', '.json') or not any(path.is_relative_to(root) for root in owned):
            return match.group()
        try:
            data = read_file(path, min(budget, 4 * 1024 * 1024))
        except (ValueError, OSError):
            return match.group()
        budget -= len(data)
        content = base64.b64encode(data).decode('ascii')
        mime = 'application/json' if path.suffix == '.json' else 'text/tab-separated-values'
        return f'download="{html.escape(path.name, quote=True)}" href="data:{mime};base64,{content}"'
    return re.sub(r"""\bhref=(["'])([^"']*)\1""", replace, page, flags=re.I)


def preview(encoded, source, output, edition=None):
    if not output:
        return encoded
    reports = Path(output).resolve() / "reports"
    root = reports / "polishing_trajectory"
    research = reports / "hybas_research/hybas_report.html"
    index = root / "index.html"
    source = Path(source).resolve()
    page = base64.b64decode(encoded).decode("utf-8", errors="replace")
    directories = ("hybas_research", "polishing_trajectory", "polishing_validation")
    roots = [reports / name for name in directories]
    if edition:
        roots.extend(Path(edition) / name for name in directories)
    if source.parent in roots and "data-hybas-report=" in page:
        rendered = linked_assets(page, source, roots)
        return base64.b64encode(rendered.encode()).decode("ascii")
    if reports.resolve() != reports or root.resolve() != root or source not in (index, research):
        return encoded
    svg = small_asset(root, "trajectory.svg", 2 * 1024 * 1024)
    if not svg:
        return encoded
    figure = "data:image/svg+xml;base64," + base64.b64encode(svg).decode("ascii")
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
