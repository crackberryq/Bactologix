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
