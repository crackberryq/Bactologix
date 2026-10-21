"""Native Streamlit UI. All execution goes through the durable local worker."""
from __future__ import annotations
import base64
import copy
import html
import json
from pathlib import Path
import shlex
import webbrowser
import yaml
import streamlit as st
from bactologix import __version__
from bactologix.catalog import DATA, PIPELINES, fetch_versions, supported, versions
from bactologix.engine import doctor, enqueue, launch_worker
from bactologix.runs import (absolute_path, clone_runs, command, create_run, import_samples, parse_config,
                             register_sample, reports, template, validate_launch)
from bactologix.store import Store, identifier, now

st.set_page_config(page_title="Bactologix", page_icon="🧬", layout="wide")
store = Store()

st.markdown("""<style>
.stApp {background:#f6f8f7;color:#173c38}
[data-testid="stSidebar"] {background:#143d39}
[data-testid="stSidebar"] * {color:#e8f3ed}
[data-testid="stSidebar"] [data-testid="stCaptionContainer"] {color:#b6d0c4}
.block-container{max-width:1450px;padding-top:2rem;padding-bottom:3rem}
h1,h2,h3{letter-spacing:-.035em;color:#123f38}
[data-testid="stMetric"]{background:white;border:1px solid #dbe5df;border-radius:14px;padding:18px}
div.stButton > button[kind="primary"]{background:#18735f;border-color:#18735f}
[data-testid="stCode"]{border-radius:10px}
</style>""", unsafe_allow_html=True)


def action(fn):
    try:
        result = fn()
        st.success(result if isinstance(result, str) else "Saved.")
        return result
    except Exception as exc:
        st.error(str(exc))
        return None


def queue(kind, payload):
    jid = enqueue(store, kind, payload)
    return "Queued " + jid + ". Follow it in Jobs & logs."


def title(text, description):
    st.title(text)
    st.caption(description)


def overview():
    title("Your local analysis workspace", "HybAs + BaSIS · installed versions, independent runs and accessible reports")
    cols = st.columns(4)
    cols[0].metric("Samples", len(store.all("samples")))
    cols[1].metric("Run drafts", len(store.all("runs")))
    cols[2].metric("Active / queued", sum(j["status"] in {"queued", "running"} for j in store.jobs()))
    cols[3].metric("HTML reports", len(reports(store)))
    st.subheader("Start here")
    st.write("1. Set up a pipeline runner.  2. Add sample folders and review configuration.  3. Create a draft and check its native dry-run.  4. Run and open the results.")
    st.info("This is the native alpha: setup, jobs and filesystem operations are real. Each new draft writes to its own output folder.")
    st.subheader("Installation status")
    for tool, spec in PIPELINES.items():
        installation = store.get("installs", store.get("settings", "active-" + tool))
        with st.container(border=True):
            st.markdown("**" + spec["name"] + "**")
            if installation:
                st.write(("Runner ready · " if installation["ready"] else "Needs attention · ") + installation["version"]["ref"])
                st.caption(installation["version"]["commit"])
            else:
                st.write("Not set up yet")
    st.subheader("Live software test")
    st.write("Run a tiny Snakemake workflow through the same worker used for analysis. It creates an HTML test report without using sample data or downloading analysis databases.")
    if st.button("Run live smoke test", type="primary"):
        action(lambda: queue("smoke", {}))
    st.caption("Workspace: " + str(store.home))


def setup():
    title("Install & update", "Choose the exact version. Previous sources, environments and runs are retained.")
    check = doctor(store)
    cols = st.columns(3)
    cols[0].metric("Operating system", check["system"])
    cols[1].metric("Available disk", str(check["free_gb"]) + " GB")
    cols[2].metric("CPU threads", check["cpus"] or 1)
    if not check["pipeline_platform_supported"]:
        st.warning("For pipeline jobs, install and launch Bactologix inside Linux or WSL2.")
    if not check["git"]:
        st.warning("Git is required. Install Git using your Linux distribution's package manager.")
    st.write("Conda: " + (check["conda"] or "Not found"))
    with st.expander("Choose an existing Conda executable"):
        conda_path = st.text_input("Conda executable", value=store.get("settings", "conda", ""), placeholder="/home/jimi/miniforge3/bin/conda")
        if st.button("Save Conda location"):
            def save_conda():
                path = absolute_path(conda_path)
                if not path.is_file():
                    raise ValueError("That executable does not exist")
                store.put("settings", "conda", str(path))
                return "Conda location saved."
            action(save_conda)
    if not check["conda"]:
        st.caption("Automatic setup downloads Miniforge from conda-forge's official GitHub release, verifies its SHA-256, and installs it inside this workspace.")
        if st.button("Install local Conda"):
            action(lambda: queue("bootstrap", {}))
    for tool, tab in zip(PIPELINES, st.tabs(["HybAs", "BaSIS"])):
        with tab:
            spec = PIPELINES[tool]
            if st.button("Refresh available versions", key="refresh-" + tool):
                def refresh():
                    result = fetch_versions(tool)
                    store.put("catalog", tool, result)
                    return "Live GitHub version list refreshed."
                with st.spinner("Reading GitHub releases and branches…"):
                    action(refresh)
            catalogue = versions(store, tool)
            st.caption(("Live GitHub check: " if catalogue.get("live") else "Bundled GitHub snapshot: ") + catalogue["checkedAt"])
            channel = st.radio("Show versions", ["All", "Releases", "Branches"], horizontal=True, key="channel-" + tool)
            rows = [v for v in catalogue["versions"] if channel == "All" or (channel == "Releases" and v["kind"] == "release") or (channel == "Branches" and v["kind"] == "branch")]
            if rows:
                selected = st.selectbox("Choose version", rows, format_func=lambda v: ("★ " if v.get("preferred") else "") + v["name"] + " · " + v["kind"] + " · " + v["commit"][:8], key="version-" + tool)
                st.link_button("Source / release notes", selected["url"])
                if selected.get("notes"):
                    with st.expander("Release notes"):
                        st.text(selected["notes"])
                compatible = supported(tool, selected["commit"])
                st.caption("Supported native adapter." if compatible else "Source download available. Managed runs require a reviewed adapter for this commit.")
                if st.button("Download & set up runner" if compatible else "Download source", key="install-" + tool, type="primary"):
                    action(lambda: queue("install", {"tool": tool, "version": selected}))
            for item in [i for i in store.all("installs") if i["tool"] == tool]:
                with st.expander(("Ready · " if item["ready"] else "Downloaded / needs setup · ") + item["version"]["ref"] + " · " + item["version"]["commit"][:8]):
                    st.code(item["source"], language=None)
                    if item.get("controller"):
                        st.caption("Controller: " + item["controller"])
                    col1, col2, col3 = st.columns(3)
                    if col1.button("Use this version", key="use-" + item["id"], disabled=not item["ready"]):
                        store.put("settings", "active-" + tool, item["id"])
                        st.success("Selected for future drafts.")
                    if col2.button("Check setup", key="check-" + item["id"], disabled=not item.get("controller")):
                        action(lambda: queue("check", {"installation": item["id"]}))
                    if col3.button("Repair runner", key="repair-" + item["id"], disabled=not supported(tool, item["version"]["commit"])):
                        action(lambda: queue("install", {"tool": tool, "version": item["version"], "repair": True}))
    st.caption("Analysis tool environments are cached per pipeline outside version checkouts. Runner repair creates a separate environment; it keeps the previous one.")


def samples_page():
    title("Samples", "Register existing local folders. Source reads and annotations remain in place.")
    with st.form("add-sample"):
        cols = st.columns([1, 2, 1])
        sid = cols[0].text_input("Sample ID")
        path = cols[1].text_input("Input folder", placeholder="/home/jimi/bactomics/isolate000TB")
        mode = cols[2].selectbox("Read mode", ["hybrid", "ont_only", "illumina_only"])
        if st.form_submit_button("Add sample", type="primary"):
            action(lambda: register_sample(store, sid.strip(), path.strip(), mode))
    st.caption("HybAs reads: raw/illumina and raw/nanopore. BaSIS inputs: annotation/<sample_id>.fna, .faa and .gff; .gbk when required by enabled modules.")
    with st.expander("Import a sample table"):
        st.download_button("Download CSV template", "sample_id,input_folder,read_mode\nisolate000TB,/home/jimi/bactomics/isolate000TB,hybrid\n", "bactologix-samples.csv", "text/csv")
        upload = st.file_uploader("CSV or TSV", type=["csv", "tsv"])
        if st.button("Import samples", disabled=upload is None):
            action(lambda: "Imported " + str(len(import_samples(store, upload.getvalue().decode("utf-8-sig")))) + " samples.")
    items = store.all("samples")
    if items:
        st.dataframe(items, width="stretch", hide_index=True)


def flatten(value, prefix=()):
    result = {}
    for key, item in value.items():
        path = (*prefix, key)
        if isinstance(item, dict) and item:
            result.update(flatten(item, path))
        else:
            result[".".join(path)] = (path, item)
    return result


def configuration():
    title("Configuration", "Full native YAML, with searchable settings. Every run retains a frozen copy.")
    tool = st.radio("Pipeline", list(PIPELINES), format_func=lambda t: PIPELINES[t]["name"], horizontal=True, key="config-tool")
    key = "yaml-" + tool
    st.session_state.setdefault(key, store.get("configs", tool, template(tool)))
    with st.expander("Import configuration"):
        upload = st.file_uploader("Run YAML", type=["yaml", "yml"], key="yaml-upload-" + tool)
        if st.button("Load uploaded YAML", disabled=upload is None):
            try:
                text = upload.getvalue().decode("utf-8-sig")
                parse_config(text)
                st.session_state[key] = text
            except Exception as exc:
                st.error(str(exc))
    with st.expander("Find and edit a setting"):
        try:
            cfg = parse_config(st.session_state[key])
            defaults = json.loads((DATA / "config-catalog.json").read_text())["defaults"][tool]
            values = flatten(defaults)
            values.update(flatten(cfg))
            field = st.selectbox("Setting (type to search)", sorted(values), key="field-" + tool)
            path, default = values[field]
            scalar_key = "setting-" + tool + "-" + field
            text = st.text_area("YAML value", value=yaml.safe_dump(default, sort_keys=False).removesuffix("...\n").strip(), key=scalar_key, height=90)
            if st.button("Apply setting to draft"):
                value = yaml.safe_load(text)
                target = cfg
                for part in path[:-1]:
                    if not isinstance(target.get(part), dict):
                        target[part] = {}
                    target = target[part]
                target[path[-1]] = value
                rewritten = yaml.safe_dump(cfg, sort_keys=False, allow_unicode=True)
                parse_config(rewritten)
                st.session_state[key] = rewritten
        except Exception as exc:
            st.error(str(exc))
    st.text_area("Full configuration", key=key, height=500)
    st.caption("Managed output/sample paths and CPU limits are bound when creating a run. Use absolute paths for existing databases and models. Guided edits preserve unknown values but rewrite YAML formatting/comments.")
    if st.button("Save defaults", type="primary"):
        def save():
            parse_config(st.session_state[key])
            store.put("configs", tool, st.session_state[key])
            return "Saved for future run drafts."
        action(save)
    st.download_button("Download YAML", st.session_state[key], tool + "-config.yaml", "text/yaml")


def run_page():
    title("Runs & variants", "Create an isolated draft, inspect the native dry-run, then start the analysis.")
    samples = store.all("samples")
    with st.expander("New run / batch", expanded=not store.all("runs")):
        tool = st.radio("Workflow", list(PIPELINES), format_func=lambda t: PIPELINES[t]["name"], horizontal=True, key="run-tool")
        chosen = st.multiselect("Samples", samples, format_func=lambda s: s["id"], key="run-samples")
        cols = st.columns(2)
        cores = cols[0].number_input("CPU cores per run", 1, 512, min(4, doctor(store)["cpus"] or 1))
        memory = cols[1].number_input("Memory budget (MB)", 512, 2000000, 16000, step=512)
        st.caption("Batch runs are queued sequentially in this alpha, so they cannot compete for the same environment cache or CPU budget.")
        if st.button("Create run drafts", type="primary", disabled=not chosen):
            def create():
                made = []
                for sample in chosen:
                    try:
                        made.append(create_run(store, tool, sample, store.get("configs", tool, template(tool)), cores, memory))
                    except Exception as exc:
                        raise ValueError(f"Created {len(made)} draft(s). {sample['id']}: {exc}") from exc
                return "Created " + str(len(made)) + " independent run drafts."
            action(create)
    items = store.all("runs")
    if not items:
        st.info("Add samples and set up a runner to create your first draft.")
        return
    with st.expander("Batch actions"):
        batch = st.multiselect("Run drafts", items, format_func=lambda r: r["label"] + " · " + r["id"], key="batch-runs")
        operation = st.selectbox("Batch operation", ["dry-run", "prepare", "run"], format_func=lambda a: {"dry-run": "Check / dry-run", "prepare": "Prepare analysis tools", "run": "Run / resume"}[a])
        if st.button("Queue selected drafts", disabled=not batch):
            def batch_queue():
                for item in batch:
                    validate_launch(store, item["id"], operation)
                queued = []
                for item in batch:
                    try:
                        queued.append(enqueue(store, "workflow", {"run_id": item["id"], "action": operation}))
                    except Exception as exc:
                        raise ValueError(f"Queued {len(queued)} job(s). {item['id']}: {exc}") from exc
                return "Queued " + str(len(queued)) + " jobs in order."
            action(batch_queue)
    run = st.selectbox("Select run", items, format_func=lambda r: r["label"] + " · " + PIPELINES[r["tool"]]["name"] + " · " + r["id"], key="run-selection")
    st.write("**" + run["label"] + "**")
    st.caption("Source " + run["commit"] + " · " + ("Native dry-run passed" if run["dry_run_ok"] else "Needs dry-run"))
    st.code(run["output"], language=None)
    active = any(j["kind"] == "workflow" and j["payload"]["run_id"] == run["id"] and j["status"] in {"queued", "running"} for j in store.jobs())
    cols = st.columns(3)
    if cols[0].button("Check / dry-run", type="primary", disabled=active):
        action(lambda: queue("workflow", {"run_id": run["id"], "action": "dry-run"}))
    if cols[1].button("Prepare analysis tools", disabled=active or not run["dry_run_ok"]):
        action(lambda: queue("workflow", {"run_id": run["id"], "action": "prepare"}))
    if cols[2].button("Run / resume", disabled=active or not run["dry_run_ok"]):
        action(lambda: queue("workflow", {"run_id": run["id"], "action": "run"}))
    if run["tool"] == "basis":
        st.caption("BaSIS follows the saved resources.policy: auto may download missing databases/models; manual requires configured resource paths.")
    with st.expander("Execution plan and frozen YAML"):
        try:
            st.code(shlex.join(command(store, run, "run")), language="bash")
        except Exception as exc:
            st.warning(str(exc))
        st.code(run["config"], language="yaml")
        st.download_button("Download frozen config", run["config"], run["id"] + ".yaml")
        st.json({"parent": run["parent"], "inputs": run["inputs"], "reuse": run["reuse"]})
    with st.expander("Create a revised draft"):
        revised = st.text_area("Configuration for the new draft", value=run["submitted"], height=300, key="revised-" + run["id"])
        st.caption("This creates a new output folder and preserves the selected run. A native dry-run is required again.")
        if st.button("Create revised draft"):
            action(lambda: create_run(store, run["tool"], run["sample"], revised, run["cores"], run["memory_mb"],
                                      run["id"], run["label"] + " · revised", run["installation"]))
    if run["tool"] == "hybas":
        with st.expander("Clone / compare polishing strategies"):
            st.write("Clones preserve the parent configuration and reference the same original input files. In this alpha they create fresh independent workflows; they do not resume from a post-AWARE assembler checkpoint.")
            aware_free = st.checkbox("Create an AWARE-free branch")
            options = ["none", "tripolish"] if run["sample"]["mode"] == "illumina_only" else ["none", "racon", "medaka", "tripolish"]
            labels = {"none": "Baseline", "tripolish": "Polypolish"} if run["sample"]["mode"] == "illumina_only" else {}
            strategies = st.multiselect("Polishing strategies", options, default=["none", "tripolish"], format_func=lambda s: labels.get(s, s))
            if st.button("Create variant drafts", disabled=not strategies):
                action(lambda: "Created " + str(len(clone_runs(store, run["id"], aware_free, strategies))) + " variants.")
        if Path(run["output"], "annotation", run["sample"]["id"] + ".fna").is_file():
            if st.button("Create BaSIS draft from this run"):
                sample = dict(run["sample"], path=run["output"])
                action(lambda: create_run(store, "basis", sample, store.get("configs", "basis", template("basis")), run["cores"], run["memory_mb"], run["id"]))
    child_runs = [r for r in items if r.get("parent") == run["id"]]
    if child_runs:
        st.dataframe([{"Variant": r["label"], "Run": r["id"], "Dry-run": r["dry_run_ok"], "Completed": r.get("completed", ""), "Output": r["output"]} for r in child_runs], hide_index=True)
    found = [r for r in reports(store) if r["run"] == run["id"]]
    for report in found:
        if st.button("View " + report["name"], key="view-" + report["id"]):
            st.session_state["selected-report"] = report["id"]
            st.session_state["nav-next"] = "Results & reports"
            st.rerun()


@st.fragment(run_every="3s")
def jobs_page():
    title("Jobs & logs", "Real subprocess logs. Jobs keep running when you close or refresh this browser tab.")
    jobs = store.jobs()
    if any(j["status"] == "queued" for j in jobs) and st.button("Start / recover queued jobs"):
        launch_worker(store)
        st.info("Worker requested. An existing active job retains the workspace lock.")
    if st.button("Refresh jobs"):
        st.rerun(scope="fragment")
    if not jobs:
        st.info("No jobs yet.")
        return
    st.dataframe([{"Job": j["id"], "Action": j["kind"] + (" / " + j["payload"].get("action", "") if j["kind"] == "workflow" else ""), "Status": j["status"], "Created": j["created"]} for j in jobs], hide_index=True, width="stretch")
    chosen = st.selectbox("Job log", jobs, format_func=lambda j: j["id"] + " · " + j["kind"] + " · " + j["status"], key="job-selection")
    if chosen.get("error"):
        st.error(chosen["error"])
    if chosen["status"] in {"queued", "running"} and st.button("Stop this job", key="stop-job"):
        store.cancel(chosen["id"])
        st.info("Stop requested. The worker will interrupt the process group and retain its log.")
    path = store.log_path(chosen["id"])
    if path.exists():
        with path.open("rb") as handle:
            handle.seek(max(0, path.stat().st_size - 100000))
            tail = handle.read().decode("utf-8", errors="replace")
        st.code(tail or "Waiting for process output…", language=None)
        if path.stat().st_size <= 20 * 1024 * 1024:
            st.download_button("Download full log", path.read_bytes(), path.name, "text/plain")
        else:
            st.caption("Full log: " + str(path))


def report_viewer(path):
    source = Path(path)
    if st.button("Open original in browser"):
        if webbrowser.open(source.as_uri(), new=2):
            st.success("Requested the browser on the machine running Bactologix.")
        else:
            st.info("No desktop browser handler is available. Download the report or open its local path.")
    if source.stat().st_size > 50 * 1024 * 1024:
        st.warning("This report exceeds the 50 MB embedded-viewer limit. Open the original file.")
        return
    content = source.read_text(errors="replace")
    # Nested iframe deliberately lacks allow-same-origin. Report scripts cannot reach the app.
    inner = '<iframe title="HTML report" sandbox="allow-scripts allow-downloads" referrerpolicy="no-referrer" style="width:100%;height:770px;border:0;background:white" srcdoc="' + html.escape(content, quote=True) + '"></iframe>'
    st.iframe(inner, height=790, alt="HTML report viewer")
    st.download_button("Download original HTML", source.read_bytes(), source.name, "text/html")
    st.caption("Embedded view supports self-contained HTML. Open the original with its companion files when the report uses relative assets.")


def results_page():
    title("Results & reports", "Open native MultiQC and Safety HTML reports, or register an existing local report.")
    with st.expander("Add an existing report"):
        path = st.text_input("HTML report path", placeholder="/home/jimi/bactomics/isolate000TB/safety/report.html")
        name = st.text_input("Report label", value="Existing report")
        if st.button("Register report"):
            def register():
                file = absolute_path(path)
                if not file.is_file() or file.suffix.lower() not in {".html", ".htm"}:
                    raise ValueError("Choose an existing HTML file")
                rid = identifier("report")
                store.put("reports", rid, {"id": rid, "name": name, "path": str(file), "sample": "User-selected", "run": "External report"})
                return "Registered. Its association is user-selected."
            action(register)
    found = reports(store)
    if not found:
        st.info("No reports found yet. Run the live smoke test or register a local HTML report.")
        return
    ids = [r["id"] for r in found]
    preferred = st.session_state.pop("selected-report", None)
    index = ids.index(preferred) if preferred in ids else 0
    report = st.selectbox("Open report", found, index=index, format_func=lambda r: r["sample"] + " · " + r["name"] + " · " + r["run"])
    st.caption(report["path"])
    report_viewer(report["path"])


def credits():
    title("Credits & citation", "Created by Jimi Goldstein · Human Link")
    st.markdown("**Jimi Goldstein** · [ORCID 0000-0002-7829-0090](https://orcid.org/0000-0002-7829-0090)")
    st.write("Bactologix provides a common local interface for installation, configuration, execution and report access. Cite the pipelines, individual tools and databases used in your analysis as well as Bactologix.")
    st.code((DATA / "CITATION.txt").read_text(), language=None)
    cols = st.columns(4)
    for col, extension in zip(cols, ["ris", "bib", "cff", "txt"]):
        col.download_button("Download " + extension.upper(), (DATA / ("CITATION." + extension)).read_bytes(), "Bactologix-citation." + extension)
    for tool, spec in PIPELINES.items():
        st.link_button(spec["name"] + " on GitHub", "https://github.com/" + spec["repo"])
        st.download_button(spec["name"] + " citation", (DATA / ("CITATION.HybAs.cff" if tool == "hybas" else "CITATION.BaSIS.cff")).read_bytes(), spec["name"] + "-CITATION.cff")
    st.caption("Native alpha " + __version__ + ". No DOI or published PyPI release is asserted.")


with st.sidebar:
    st.markdown("# Bactologix")
    st.caption("LOCAL WORKSPACE · " + __version__)
    if "nav-next" in st.session_state:
        st.session_state["nav"] = st.session_state.pop("nav-next")
    page = st.radio("Workspace", ["Overview", "Install & update", "Samples", "Configuration", "Runs & variants", "Jobs & logs", "Results & reports", "Credits & citation"], label_visibility="collapsed", key="nav")
    st.divider()
    st.markdown("**Created by Jimi Goldstein**")
    st.caption("Human Link")
    st.markdown("[ORCID](https://orcid.org/0000-0002-7829-0090)")
    st.caption("Your data and jobs stay on the machine running Bactologix.")

{"Overview": overview, "Install & update": setup, "Samples": samples_page, "Configuration": configuration,
 "Runs & variants": run_page, "Jobs & logs": jobs_page, "Results & reports": results_page,
 "Credits & citation": credits}[page]()
