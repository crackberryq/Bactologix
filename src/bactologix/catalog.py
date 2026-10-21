"""GitHub releases are resolved to immutable commits before installation."""
from __future__ import annotations
import json
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.parse import quote
from .store import now

DATA = Path(__file__).parent / "data"
PIPELINES = {
    "hybas": {"name": "HybAs", "repo": "crackberryq/Bactomics_HybAs", "preferred": "HybAs_v9.0.0-AWARE",
              "commit": "9477fd3b6578e23a84d6524e1c89e65e01590f03", "snakefile": "Hybas.smk", "environment": "environment.yaml"},
    "basis": {"name": "BaSIS", "repo": "crackberryq/Bactomics_BaSIS", "preferred": "snakemake-standalone",
              "commit": "d2d2e7806df4d2f586dc4a11d5c1f980deb06857", "snakefile": "BaSIS.smk", "environment": "environment.yml"},
}


def api(path):
    req = Request("https://api.github.com/" + path,
                  headers={"User-Agent": "Bactologix/0.1.0a3", "Accept": "application/vnd.github+json"})
    with urlopen(req, timeout=30) as response:
        return json.load(response)


def pages(path):
    rows = []
    for page in range(1, 21):
        batch = api(path + ("&" if "?" in path else "?") + f"per_page=100&page={page}")
        rows.extend(batch)
        if len(batch) < 100:
            return rows
    raise ValueError("GitHub listing exceeded 2,000 entries; narrow the catalogue before installation")


def fetch_versions(tool):
    spec = PIPELINES[tool]
    base = "repos/" + spec["repo"]
    rows = []
    for branch in pages(base + "/branches"):
        name = branch["name"]
        rows.append(dict(id="branch:" + name, kind="branch", ref=name, name=name,
                         commit=branch["commit"]["sha"], url=f'https://github.com/{spec["repo"]}/tree/{quote(name, safe="")}',
                         preferred=name == spec["preferred"], prerelease=False))
    for release in pages(base + "/releases"):
        if release.get("draft"):
            continue
        tag = release["tag_name"]
        # The commits endpoint peels annotated tags; target_commitish can be a moving branch.
        commit = api(base + "/commits/" + quote(tag, safe=""))["sha"]
        rows.append(dict(id="release:" + tag, kind="release", ref=tag,
                         name=release.get("name") or tag, commit=commit, url=release["html_url"],
                         preferred=False, prerelease=release.get("prerelease", False),
                         publishedAt=release.get("published_at"), notes=release.get("body", "")))
    rows.sort(key=lambda row: (not row["preferred"], row["kind"] != "release"))
    return {"checkedAt": now(), "versions": rows, "live": True}


def versions(store, tool):
    cached = store.get("catalog", tool)
    if cached:
        return cached
    snapshot = json.loads((DATA / "versions.json").read_text())
    return {"checkedAt": snapshot["checkedAt"], "versions": snapshot["pipelines"][tool]["versions"], "live": False}


def supported(tool, commit):
    return commit == PIPELINES[tool]["commit"]

