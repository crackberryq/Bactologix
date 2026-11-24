import csv
import importlib.util
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.request import ProxyHandler, build_opener
import zipfile


SCRIPT = Path(__file__).resolve().parents[1] / "scripts/print-bactologix-reports.py"
SPEC = importlib.util.spec_from_file_location("print_reports", SCRIPT)
export = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(export)


class ReportPrintingTests(unittest.TestCase):
    def test_large_print_view_labels_scope_and_keeps_only_the_first_page_of_review(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            safety = root / "safety"
            safety.mkdir()
            path = safety / "report.html"
            rows = [f'<tr data-group="exploratory"><td>{i}</td></tr>' for i in range(500)]
            rows += [f'<tr data-group="review"><td>item-{i}</td></tr>' for i in range(53)]
            source = ('<head></head><table id="finding-table"><tbody>' + ''.join(rows) + '</tbody></table>').encode()
            path.write_bytes(source)
            view, count = export.print_view(path)
            self.assertEqual(path.read_bytes(), source)
            self.assertEqual(count, 553)
            self.assertIn(b"50 main review rows shown of 553 saved findings", view)
            self.assertEqual(view.count(b'<tr data-group="review">'), 50)
            self.assertNotIn(b'item-50', view)
            self.assertNotIn(b'<tr data-group="exploratory">', view)
            self.assertIn(b'screening-findings.tsv', view)

    def test_keeps_unrecognized_markup_and_only_opens_trajectory_measurements_and_chart(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "polishing_trajectory" / "index.html"
            path.parent.mkdir()
            path.write_bytes(b'<details><summary>Geometric trajectory</summary></details>'
                             b'<details><summary>Stage measurements</summary></details>'
                             b'<details><summary>Evidence files</summary></details>')
            view, count = export.print_view(path)
            self.assertEqual(view.count(b'<details open>'), 2)
            self.assertEqual(count, 0)
            self.assertIn(b'<details><summary>Evidence files', view)
            unexpected = b'<table id="finding-table"><tbody><tr><td>no group</td></tr></tbody></table>'
            self.assertEqual(export.compact_screening(unexpected), (unexpected, 0))

    def test_retry_reuses_valid_pdf_and_reprints_missing_pdf(self):
        pdf = b"%PDF-1.4\n" + b" " * 250 + b"%%EOF"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run = "run-synthetic"
            folder = root / "runs" / run / "outputs" / "sample"
            folder.mkdir(parents=True)
            (folder / "pending.html").write_text("<p>missing page</p>")
            old = root / "previous.zip"
            entries = [(run, "sample/okay.html", run + "/sample/okay.html.pdf", "OK"),
                       (run, "sample/pending.html", "", "browser failure")]
            csv_file = io.StringIO()
            writer = csv.writer(csv_file)
            writer.writerow(export.FIELDS)
            writer.writerows(entries)
            with zipfile.ZipFile(old, "x") as archive:
                archive.writestr(entries[0][2], pdf)
                archive.writestr("print-inventory.csv", csv_file.getvalue())
            def render(browser, work, dest, url, timeout):
                opener = build_opener(ProxyHandler({}))
                with opener.open(url, timeout=5) as response:
                    self.assertIn(b'missing page', response.read())
                dest.write_bytes(pdf)
            with zipfile.ZipFile(old) as previous, patch.object(export, "browser_pdf", side_effect=render) as printer:
                code = export.run_export(root / "runs", root, [], previous, ["fake"], 20)
            self.assertEqual(code, 0)
            self.assertEqual(printer.call_count, 1)
            with zipfile.ZipFile(next(root.glob("Bactologix-PDF-*.zip"))) as archive:
                self.assertEqual(archive.read(entries[0][2]), pdf)
                self.assertEqual(archive.read(run + "/sample/pending.html.pdf"), pdf)
                self.assertEqual([row["Status"] for row in export.old_inventory(archive)], ["OK"] * 2)


if __name__ == "__main__":
    unittest.main()
