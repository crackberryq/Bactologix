"""Exercise live draft progress through the actual component trigger and session queue."""
import time
from pathlib import Path

import pytest
from streamlit.runtime.forward_msg_queue import ForwardMsgQueue
from streamlit.testing.v1 import AppTest

from test_native import installation, sample, workspace
from test_ui import APP, send


@pytest.mark.parametrize("fail_read", [False, True])
def test_slow_draft_reports_bytes_before_next_read_and_clears(workspace, installation, sample,
                                                            monkeypatch, fail_read):
    monkeypatch.setenv("BACTOLOGIX_HOME", str(workspace.home))
    source = Path(sample["path"]) / "raw/illumina/sample_R1.fastq"
    with source.open("wb") as fixture:
        fixture.truncate(3 * 1024 ** 2)
    app = AppTest.from_file(str(APP)).run(timeout=20)

    labels = []
    updates = []
    enqueue = ForwardMsgQueue.enqueue

    def record(queue, msg):
        enqueue(queue, msg)
        element = msg.delta.new_element
        if element.HasField("text"):
            labels.append(element.text.body)
        elif element.HasField("progress"):
            updates.append((element.progress.value, element.progress.text))

    monkeypatch.setattr(ForwardMsgQueue, "enqueue", record)
    original_open = Path.open
    opens = []
    observed_while_reading = []

    def open_file(path, mode="r", *args, **kwargs):
        handle = original_open(path, mode, *args, **kwargs)
        if path != source or mode != "rb":
            return handle
        opens.append(path)
        read = handle.read

        def slow_read(size=-1):
            if handle.tell() == 0:
                time.sleep(.6)
            elif handle.tell() == 1024 ** 2:
                observed_while_reading.append(
                    "Reading " + source.name in labels
                    and (33, "1.0 of 3.0 MiB read for this file") in updates
                )
                if fail_read:
                    raise OSError("simulated read interruption")
            return read(size)

        handle.read = slow_read
        return handle

    monkeypatch.setattr(Path, "open", open_file)
    result = send(app, "create_runs", {"tool": "hybas", "ids": [sample["id"]]})
    assert observed_while_reading == [True]
    assert opens == [source]
    assert not app.get("progress")
    assert result["ok"] == (not fail_read), result
    if fail_read:
        assert "simulated read interruption" in result["message"]
        assert not workspace.all("runs")
    else:
        assert (100, "3.0 of 3.0 MiB read for this file") in updates
        assert workspace.get("runs", result["open_run"])


def test_progress_counts_follow_each_file_including_empty(tmp_path):
    from bactologix.runs import input_read_progress, snapshot_files

    first, second = tmp_path / "first", tmp_path / "second"
    first.write_bytes(b"")
    second.write_bytes(b"small")
    counts = []
    token = input_read_progress.set(lambda path, done, size: counts.append((path, done, size)))
    try:
        rows = snapshot_files([second, first])
    finally:
        input_read_progress.reset(token)
    assert counts == [(first, 0, 0), (second, 0, 5), (second, 5, 5)]
    assert [row["size"] for row in rows] == [0, 5]
