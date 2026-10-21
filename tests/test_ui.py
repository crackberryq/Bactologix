from pathlib import Path
from streamlit.testing.v1 import AppTest


def test_all_native_pages_and_yaml_edit(tmp_path, monkeypatch):
    monkeypatch.setenv("BACTOLOGIX_HOME", str(tmp_path))
    app = AppTest.from_file(str(Path(__file__).parents[1] / "src/bactologix/app.py")).run(timeout=20)
    for page in ["Overview", "Install & update", "Samples", "Configuration", "Runs & variants", "Jobs & logs", "Results & reports", "Credits & citation"]:
        app.sidebar.radio(key="nav").set_value(page).run(timeout=20)
        assert not app.exception, [e.value for e in app.exception]
    app.sidebar.radio(key="nav").set_value("Configuration").run()
    app.text_area(key="yaml-hybas").set_value("custom: {retain: [1, true]}\nthreads: 2\n").run()
    next(b for b in app.button if b.label == "Save defaults").click().run()
    assert not app.exception
    from bactologix.store import Store
    assert "retain" in Store(tmp_path).get("configs", "hybas")


def test_report_is_nested_and_escaped(tmp_path, monkeypatch):
    monkeypatch.setenv("BACTOLOGIX_HOME", str(tmp_path))
    from bactologix.store import Store
    store = Store(tmp_path)
    path = tmp_path / "example.html"
    path.write_text('<h1>Report test</h1><script>document.title="report"</script>')
    store.put("reports", "test", {"id": "test", "name": "Test report", "path": str(path), "sample": "test", "run": "external"})
    app = AppTest.from_file(str(Path(__file__).parents[1] / "src/bactologix/app.py")).run(timeout=20)
    app.sidebar.radio(key="nav").set_value("Results & reports").run()
    assert not app.exception
    frame = app.get("iframe")[0].proto.srcdoc
    assert 'sandbox="allow-scripts allow-downloads"' in frame
    assert "allow-same-origin" not in frame
    assert "&lt;script&gt;" in frame
