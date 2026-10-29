"""GitHub releases are resolved to immutable commits before installation."""
from __future__ import annotations
import json
import copy
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.parse import quote
from .store import now
from . import __version__

DATA = Path(__file__).parent / "data"
PIPELINES = {
    "hybas": {"name": "HybAs AWARE", "repo": "crackberryq/Bactomics_HybAs", "preferred": "HybAs_v9.0.0-AWARE",
              "commit": "780ee7086c15c49f2825800ec463327f009f14a6", "previous_commits": ["9477fd3b6578e23a84d6524e1c89e65e01590f03"],
              "snakefile": "Hybas.smk", "environment": "environment.yaml"},
    "basis": {"name": "BaSIS AWARE", "repo": "crackberryq/Bactomics_BaSIS", "preferred": "basis-aware",
              "commit": "14f161a1f252a712053892961edf2275469c8dab",
              "followup_commits": ["14f161a1f252a712053892961edf2275469c8dab", "1d2d96b266e98d956254d1a518ff5b437ab7a10a"],
              "previous_commits": ["1d2d96b266e98d956254d1a518ff5b437ab7a10a", "f66dfedd46da05ae79bf769e7e6fada91270d4d1", "d2d2e7806df4d2f586dc4a11d5c1f980deb06857"],
              "previous_ref": "snakemake-standalone",
              "snakefile": "BaSIS.smk", "environment": "environment.yml"},
    "micpscope": {"name": "MICP Scope", "repo": "crackberryq/Bactomics_MICPscope", "preferred": "micpscope-v0.1.0a4",
                  "commit": "8490e9d1587a7ad40d0e8f687fcf9375637faa2e", "previous_commits": ["63b2ac9682d3afcd593302a01da4187af739d96f", "4e5cdfad54d6d7d0c198f9458aa27dd1316da0a2"],
                  "release_names": {"8490e9d1587a7ad40d0e8f687fcf9375637faa2e": "micpscope-v0.1.0a4", "63b2ac9682d3afcd593302a01da4187af739d96f": "micpscope-v0.1.0a3", "4e5cdfad54d6d7d0c198f9458aa27dd1316da0a2": "micpscope-v0.1.0a2"}, "snakefile": "MICPscope.smk", "environment": "environment.yml"},
}


def api(path):
    req = Request("https://api.github.com/" + path,
                  headers={"User-Agent": "Bactologix/" + __version__, "Accept": "application/vnd.github+json"})
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
        rows.append(dict(id="branch:" + name, kind="branch", ref=name, name=("MICP Scope development" if tool == "micpscope" and name == "main" else name),
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
    if not cached:
        snapshot = json.loads((DATA / "versions.json").read_text())
        pipeline = snapshot["pipelines"][tool]
        cached = {"checkedAt": pipeline.get("checkedAt", snapshot["checkedAt"]),
                  "versions": pipeline["versions"], "live": False}
    result = copy.deepcopy(cached)
    spec = PIPELINES[tool]
    # Moving branches must never hide the compatible snapshots after a refresh.
    pinned = []
    for commit in [spec["commit"], *spec.get("previous_commits", [])]:
        ref = spec.get("release_names", {}).get(commit) or (spec["preferred"] if commit == spec["commit"] else spec.get("previous_ref", spec["preferred"]))
        display_name = (ref + (" — recommended" if commit == spec["commit"] else " — earlier compatible snapshot")) if "release_names" in spec else ("Recommended compatible snapshot" if commit == spec["commit"] else "Earlier compatible snapshot")
        pinned.append(dict(id="commit:" + commit, kind="pinned", ref=ref + " @ " + commit[:8],
                           name=display_name,
                           commit=commit, url=f'https://github.com/{spec["repo"]}/tree/{commit}',
                           preferred=commit == spec["commit"], prerelease=False))
    result["versions"] = pinned + [row for row in result["versions"] if row["kind"] != "pinned"]
    for row in result["versions"]:
        row["preferred"] = bool(row.get("preferred") and row["commit"] == spec["commit"])
    releases = sorted([r for r in result['versions'] if r['kind'] == 'release'],
                      key=lambda r: r.get('publishedAt') or '', reverse=True)
    result['latest_release'] = releases[0]['ref'] if releases else None
    reviewed = [r for r in releases if supported(tool, r['commit'], store)]
    result['recommended_release'] = reviewed[0]['ref'] if reviewed else None
    for row in result['versions']:
        row.setdefault('compatibility_reason', 'Reviewed legacy adapter' if supported(tool, row['commit'], store)
                       else 'No tested compatible contract for this commit; source download only.')
    return result


def supported(tool, commit, store=None):
    from .release_contract import approval
    spec = PIPELINES[tool]
    return commit in [spec["commit"], *spec.get("previous_commits", [])] or approval(store, tool, commit) is not None


