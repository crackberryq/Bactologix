"""The approved HTML workspace, mounted as an interactive Streamlit component."""
from pathlib import Path
import time
import streamlit as st
from streamlit.components.v2 import component
from bactologix.store import Store, workspace, display_read
from bactologix.ui_backend import handle_request, display_busy, display_snapshot

from bactologix.channels import title
from bactologix.runs import input_read_progress

st.set_page_config(page_title=title(), page_icon=str(Path(__file__).parent / 'ui/brand.png'), layout="wide", initial_sidebar_state="collapsed")
st.markdown("""<style>
header[data-testid="stHeader"], [data-testid="stSidebar"], #MainMenu, footer {display:none}
.stApp {background:#f5f7f6}
.block-container {padding:0!important;max-width:100%!important}
[data-testid="stMainBlockContainer"] {padding:0!important}
[data-testid="stVerticalBlock"] {gap:0}
.st-key-draft-read-progress {
  position:fixed; top:1rem; right:1rem; z-index:1000;
  width:min(27rem,calc(100vw - 2rem)); padding:.65rem 1rem;
  background:#fff; border:1px solid #dce4e3; border-radius:.6rem;
  box-shadow:0 3px 15px #112d3520; pointer-events:none;
}
.st-key-draft-read-progress [data-testid="stText"] {
  white-space:nowrap; overflow:hidden; text-overflow:ellipsis;
}
/* The custom workspace keeps its previous snapshot usable while a request runs.
   Streamlit otherwise fades this one element (including its entire sidebar).
   Keep native elements and disconnected/error states under Streamlit's control. */
[data-testid="stApp"][data-test-connection-state="CONNECTED"]:is([data-test-script-state="running"], [data-test-script-state="rerunRequested"])
  .st-key-workspace-ui[data-testid="stElementContainer"][data-stale="true"] {
  opacity:1;
  transition:none;
}
</style>""", unsafe_allow_html=True)

@st.cache_resource
def workspace_component():
    assets = Path(__file__).parent / "ui"
    brand = (assets / 'brand.svg').read_text().strip()
    return component("bactologix_workspace", html=(assets / "shell.html").read_text().replace('__BRAND_SVG__', brand),
                     css=(assets / "styles.css").read_text(), js=(assets / "workspace.js").read_text().replace('__BRAND_SVG__', brand))

@st.cache_resource(scope="session", show_spinner=False)
def open_workspace(home):
    # Initialize the SQLite schema once per workspace in this UI session. Each
    # snapshot still opens fresh transactions to read the worker's latest state.
    return Store(home)

def wait_for_workspace():
    st.info("Waiting for the workspace database. Bactologix will check again in two seconds.")
    time.sleep(2)
    st.rerun()


try:
    with display_read():
        store = open_workspace(workspace())
except Exception as exc:
    if not display_busy(exc):
        raise
    wait_for_workspace()

draft_progress_slot = st.empty()


def on_action():
    request = st.session_state["workspace-ui"].get("action")
    if not request:
        return
    if not isinstance(request, dict) or request.get("kind") != "create_runs":
        handle_request(store, request, st.session_state)
        return

    # The callback streams into its existing slot while Create drafts owns this
    # request. Keep short reads quiet and bound the number of browser updates.
    next_update = time.monotonic() + .5
    current_file = None
    label = bar = None

    def show_read(path, consumed, size):
        nonlocal next_update, current_file, label, bar
        now = time.monotonic()
        if now < next_update and not (path == current_file and consumed == size):
            return
        next_update = now + .5
        if bar is None:
            panel = draft_progress_slot.container(key="draft-read-progress")
            label = panel.empty()
            bar = panel.empty()
        if path != current_file:
            current_file = path
            label.text("Reading " + path.name)
        mib = 1024 ** 2
        amount = (f"{consumed / mib:,.1f} of {size / mib:,.1f} MiB" if size >= mib
                  else f"{consumed:,} of {size:,} bytes")
        bar.progress(min(1.0, consumed / size) if size else 1.0, text=f"{amount} read for this file")

    token = input_read_progress.set(show_read)
    try:
        handle_request(store, request, st.session_state)
    finally:
        input_read_progress.reset(token)
        if bar is not None:
            draft_progress_slot.empty()

workspace_state = display_snapshot(store, cached=bool(st.session_state.get('ui-response', {}).get('async_task')))
if workspace_state is None:
    wait_for_workspace()
workspace_component()(key="workspace-ui", data={"workspace": workspace_state, "response": st.session_state.get("ui-response")},
                      on_action_change=on_action, width="stretch", height="content")
