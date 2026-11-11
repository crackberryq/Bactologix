"""Large native reports keep a complete, bounded content observation."""
from contextlib import contextmanager
import hashlib
from pathlib import Path

import pytest

from bactologix import report_evidence, results


def test_large_report_is_hashed_in_chunks(tmp_path, monkeypatch):
    path = tmp_path / 'evidence.json'
    # Sparse data keeps this fixture cheap while crossing the previous 100 MiB cap.
    with path.open('wb') as handle:
        handle.truncate(101 * 1024 * 1024)
    original = results._file
    class Bounded:
        def __init__(self, handle): self.handle = handle
        def fileno(self): return self.handle.fileno()
        def read(self, count):
            assert 0 < count <= 1024 * 1024
            return self.handle.read(count)
    @contextmanager
    def bounded(value):
        with original(value) as handle:
            yield Bounded(handle)
    monkeypatch.setattr(results, '_file', bounded)
    digest = hashlib.sha256()
    for _ in range(101): digest.update(bytes(1024 * 1024))
    assert report_evidence.fingerprint(path) == digest.hexdigest()


def test_linked_and_over_limit_reports_are_rejected(tmp_path, monkeypatch):
    path = tmp_path / 'evidence.json'
    path.write_bytes(b'1234')
    link = tmp_path / 'linked.json'
    link.symlink_to(path)
    with pytest.raises(OSError): report_evidence.fingerprint(link)
    monkeypatch.setattr(report_evidence, 'LIMIT', 3)
    with pytest.raises(ValueError, match='limit'): report_evidence.fingerprint(path)


def test_changed_file_is_not_given_a_fingerprint(tmp_path, monkeypatch):
    path = tmp_path / 'evidence.json'
    path.write_bytes(b'original')
    original = results._file
    opens = []
    @contextmanager
    def replaced(value):
        opens.append(value)
        if len(opens) == 2:
            path.unlink()
            path.write_bytes(b'replaced')
        with original(value) as handle: yield handle
    monkeypatch.setattr(results, '_file', replaced)
    with pytest.raises(ValueError, match='changed'): report_evidence.fingerprint(path)
