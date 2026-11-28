"""Export run reports to PDF using Windows Edge or Chrome from Ubuntu WSL.

python3 print-bactologix-reports.py --retry 'C:\\Users\\Jimi\\Downloads\\Bactologix-PDF-example.zip'
python3 print-bactologix-reports.py --run run-EXAMPLE --dest /mnt/c/Users/Jimi/Downloads
"""
import argparse
import base64
import csv
from datetime import datetime
import functools
import http.server
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import secrets
import shutil
import subprocess
import tempfile
import threading
from urllib.parse import quote, unquote, urlsplit
import zipfile

FIELDS = ("Run", "Source HTML", "PDF in ZIP", "Status")
RUN_ID = re.compile(r"run-[A-Za-z0-9_-]+\Z")
BROWSERS = [
    "/mnt/c/Program Files (x86)/Microsoft/Edge/Application/msedge.exe",
    "/mnt/c/Program Files/Microsoft/Edge/Application/msedge.exe",
    "/mnt/c/Program Files/Google/Chrome/Application/chrome.exe",
    "/mnt/c/Program Files (x86)/Google/Chrome/Application/chrome.exe",
]
POWERSHELL = "/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe"
# The subprocess ID is the real Windows browser PID. Each attempt owns a new profile.
PRINT_SCRIPT = r"""
$ErrorActionPreference='Stop'
$config = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('__CONFIG__')) | ConvertFrom-Json
$proc = Start-Process -FilePath $config.browser -ArgumentList $config.arguments -PassThru -WindowStyle Hidden -RedirectStandardError $config.stderr
try {
    $timer = [Diagnostics.Stopwatch]::StartNew()
    $lastSize = -1
    $exitAt = -1
    while ($timer.Elapsed.TotalSeconds -lt $config.timeout) {
        if (Test-Path -LiteralPath $config.pdf -PathType Leaf) {
            $stream = $null
            try {
                $stream = [IO.File]::Open($config.pdf, 'Open', 'Read', 'ReadWrite')
                if ($stream.Length -gt 200 -and $stream.Length -eq $lastSize) {
                    $stream.Position = [Math]::Max(0L, $stream.Length - 1024L)
                    $tail = New-Object byte[] ([int]($stream.Length - $stream.Position))
                    $read = $stream.Read($tail,0,$tail.Length)
                    if ([Text.Encoding]::ASCII.GetString($tail,0,$read).Contains('%%EOF')) { exit 0 }
                }
                $lastSize = $stream.Length
            } catch [IO.IOException] {
                $lastSize = -1
            } finally {
                if ($stream) { $stream.Dispose() }
            }
        }
        if ($proc.HasExited) {
            if ($exitAt -lt 0) { $exitAt = $timer.Elapsed.TotalSeconds }
            if ($timer.Elapsed.TotalSeconds - $exitAt -gt 4) { throw "Browser exited with code $($proc.ExitCode) before a complete PDF appeared." }
        }
        Start-Sleep -Milliseconds 350
    }
    throw "PDF did not finish within $($config.timeout) seconds."
} finally {
    if (-not $proc.HasExited) { & taskkill.exe /PID $proc.Id /T /F | Out-Null }
    $proc.Dispose()
}
"""

def linux_path(value):
    if re.match(r"^[a-zA-Z]:[\\/]|^\\\\", value):
        value = subprocess.check_output(["wslpath", "-u", value], text=True).strip()
    return Path(value).expanduser().resolve()


def win_path(path):
    return subprocess.check_output(["wslpath", "-w", str(path)], text=True).strip()


def report_path(runs, run, relative):
    if not RUN_ID.fullmatch(run) or "\\" in relative:
        raise ValueError("Invalid run or report path")
    path = PurePosixPath(relative)
    if path.is_absolute() or ".." in path.parts or not path.parts:
        raise ValueError("Relative report path required")
    root = (runs / run / "outputs").resolve()
    item = (root / path).resolve()
    if not item.is_relative_to(root):
        raise ValueError("Report path outside run outputs")
    return item


def list_reports(root):
    visited = set()
    def fail(error):
        raise error
    for base, dirs, files in os.walk(root, followlinks=True, onerror=fail):
        folder = Path(base)
        resolved = folder.resolve()
        if resolved in visited or not resolved.is_relative_to(root.resolve()):
            dirs.clear()
            continue
        visited.add(resolved)
        dirs.sort()
        for name in sorted(files):
            if Path(name).suffix.lower() in (".htm", ".html"):
                yield (folder / name).relative_to(root).as_posix()


# Match the generated BaSIS table. Individual rows contain no nested tables.
TABLE_ID = re.compile(rb"""<table\b[^>]*\bid=["']finding-table["'][^>]*>""", re.I)
BODY_OPEN = re.compile(rb"<tbody\b[^>]*>", re.I)
ROW = re.compile(rb"<tr\b([^>]*)>.*?</tr\s*>", re.S | re.I)
GROUP = re.compile(rb"""\bdata-group=["']([^"']+)["']""", re.I)


def compact_screening(source):
    table = TABLE_ID.search(source)
    if not table:
        return source, 0
    body = BODY_OPEN.search(source, table.end())
    if not body:
        return source, 0
    end = source.find(b"</tbody>", body.end())
    if end < 0 or source.find(b"<table", table.end(), end) >= 0:
        return source, 0
    count = 0
    kept = []
    for row in ROW.finditer(source, body.end(), end):
        group = GROUP.search(row[1])
        if group is None:
            return source, 0
        count += 1
        if group[1] == b"review" and len(kept) < 50:
            kept.append(row[0])
    if count <= 50:
        return source, 0
    # The on-page filter only sees rows in this temporary print view. Hide its
    # dynamic counter and use the true source count immediately above the table.
    note = (f'<p><strong>Printed findings</strong> &mdash; {len(kept):,} main review rows shown '
            f'of {count:,} saved findings across all evidence groups. The ZIP includes the complete '
            'original report and its full screening-findings.tsv table.</p>')
    styles = b"<style>#finding-count,#finding-empty,.finding-pages,.finding-print-option{display:none!important}</style>"
    view = (source[:table.start()] + note.encode() + source[table.start():body.end()] +
            b"".join(kept) + source[end:])
    return view.replace(b"</head>", styles + b"</head>", 1), count


def print_view(path):
    source = path.read_bytes()
    if path.parent.name == "polishing_trajectory":
        source = re.sub(rb"(<details)>(\s*<summary>(?:Stage measurements|Geometric trajectory)</summary>)",
                        rb"\1 open>\2", source)
    if path.parent.name == "safety" and path.name == "report.html":
        return compact_screening(source)
    return source, 0


class ReportHandler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, runs, token, selected_runs, fetched, **kwargs):
        self.runs, self.token, self.selected_runs, self.fetched = runs, token, selected_runs, fetched
        super().__init__(*args, directory=str(runs), **kwargs)

    def log_message(self, *_):
        pass

    def send_head(self):
        url = urlsplit(self.path)
        parts = unquote(url.path).split("/")
        try:
            _, token, run, outputs, *rest = parts
            if token != self.token or run not in self.selected_runs or outputs != "outputs":
                raise ValueError
            relative = "/".join(rest)
            item = report_path(self.runs, run, relative)
            if not item.is_file():
                raise ValueError
        except (ValueError, OSError):
            self.send_error(404)
            return None
        if url.query == "pdf" and item.suffix.lower() in (".html", ".htm"):
            active = self.server.active
            if active is None or active[:2] != (run, relative):
                self.send_error(404)
                return None
            body = active[2]
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            if self.command == "GET":
                self.fetched.add((run, relative))
            return io.BytesIO(body)
        self.path = "/" + "/".join(quote(p, safe="") for p in parts[2:])
        return super().send_head()


def valid_pdf(path):
    if not path.exists() or path.stat().st_size < 200:
        return False
    with path.open("rb") as stream:
        if stream.read(5) != b"%PDF-":
            return False
        stream.seek(max(0, path.stat().st_size - 1024))
        return b"%%EOF" in stream.read()


def browser_pdf(browser, work, pdf, url, timeout):
    profile = Path(tempfile.mkdtemp(prefix="profile-", dir=work))
    stderr = profile.parent / (profile.name + ".log")
    options = ["--headless", "--no-first-run", "--no-default-browser-check", "--disable-extensions",
               "--disable-background-networking", "--no-proxy-server", "--no-pdf-header-footer",
               "--user-data-dir=" + win_path(profile), "--print-to-pdf=" + win_path(pdf), url]
    config = {"browser": win_path(browser), "arguments": subprocess.list2cmdline(options),
              "stderr": win_path(stderr), "pdf": win_path(pdf), "timeout": timeout}
    encoded = base64.b64encode(json.dumps(config).encode()).decode()
    script = base64.b64encode(PRINT_SCRIPT.replace("__CONFIG__", encoded).encode("utf-16-le")).decode()
    try:
        result = subprocess.run([POWERSHELL, "-NoProfile", "-NonInteractive", "-EncodedCommand", script],
                                capture_output=True, timeout=timeout + 30)
        if result.returncode or not valid_pdf(pdf):
            diagnostic = stderr.read_bytes()[-900:].decode(errors="replace") if stderr.exists() else ""
            raise ValueError(f"{browser.name} print failed, exit {result.returncode}. {diagnostic}")
    finally:
        shutil.rmtree(profile, ignore_errors=True)
        stderr.unlink(missing_ok=True)


def old_inventory(archive):
    with archive.open("print-inventory.csv") as src:
        records = csv.DictReader(io.TextIOWrapper(src, encoding="utf-8-sig"))
        if tuple(records.fieldnames or ()) != FIELDS:
            raise ValueError("Unrecognized print inventory")
        rows = list(records)
    for r in rows:
        run, relative, entry = (r[k] for k in FIELDS[:3])
        if not RUN_ID.fullmatch(run) or not relative or PurePosixPath(relative).is_absolute() or ".." in PurePosixPath(relative).parts or "\\" in relative:
            raise ValueError("Invalid source in print inventory")
        if r["Status"] == "OK" and entry != f"{run}/{relative}.pdf":
            raise ValueError("Unexpected PDF path in print inventory")
    return rows


def run_export(runs, dest, selected, previous, browsers, timeout):
    rows = old_inventory(previous) if previous else []
    if not rows and not selected:
        raise ValueError("No runs in the previous inventory")
    for run in selected:
        if not RUN_ID.fullmatch(run) or not (runs / run / "outputs").is_dir():
            raise ValueError(f"Unknown run or outputs directory: {run}")
        rows.extend(dict(zip(FIELDS, (run, rel, "", "pending"))) for rel in list_reports(runs / run / "outputs"))
    if not rows:
        raise ValueError("No report HTML found")
    dest.mkdir(exist_ok=True, parents=True)
    tag = datetime.now().strftime("%Y%m%d-%H%M%S-") + secrets.token_hex(3)
    final = dest / f"Bactologix-PDF-{tag}.zip"
    retained = completed = 0
    with tempfile.TemporaryDirectory(prefix="Bactologix-print-", dir=dest, ignore_cleanup_errors=True) as tmp:
        work = Path(tmp)
        partial = work / "reports.zip"
        fetched = set()
        token = secrets.token_hex(16)
        cls = functools.partial(ReportHandler, runs=runs, token=token,
                                selected_runs={r["Run"] for r in rows}, fetched=fetched)
        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), cls)
        server.active = None
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with zipfile.ZipFile(partial, "x", zipfile.ZIP_DEFLATED) as archive:
                if previous:
                    # Also carry forward original HTML and TSV companions from a previous retry.
                    companions = {f'{r["Run"]}/{r["Source HTML"]}' for r in rows if r["Status"] == "OK" and r["Source HTML"].endswith("/safety/report.html")}
                    companions |= {str(PurePosixPath(n).parent / "tables/screening-findings.tsv") for n in companions}
                    for name in sorted(companions.intersection(previous.namelist())):
                        with previous.open(name) as src, archive.open(name, "w", force_zip64=True) as out:
                            shutil.copyfileobj(src, out)
                for index, row in enumerate(rows, 1):
                    run, rel = row["Run"], row["Source HTML"]
                    entry = f"{run}/{rel}.pdf"
                    pdf = work / "report.pdf"
                    try:
                        if previous and row["Status"] == "OK" and entry in previous.namelist():
                            with previous.open(entry) as src, pdf.open("wb") as out:
                                shutil.copyfileobj(src, out)
                            if valid_pdf(pdf):
                                archive.write(pdf, entry)
                                retained += 1
                                continue
                        path = report_path(runs, run, rel)
                        body, count = print_view(path)
                        server.active = (run, rel, body)
                        key = (run, rel)
                        url = f"http://127.0.0.1:{server.server_port}/{token}/{run}/outputs/{quote(rel)}?pdf"
                        print(f"[{index}/{len(rows)}] Printing {run}/{rel}", flush=True)
                        errors = []
                        for browser in browsers:
                            fetched.discard(key)
                            pdf.unlink(missing_ok=True)
                            try:
                                browser_pdf(browser, work, pdf, url, timeout)
                                if key not in fetched:
                                    raise ValueError("The Windows browser did not fetch the report. Check WSL localhost forwarding.")
                                break
                            except (OSError, ValueError, subprocess.SubprocessError) as exc:
                                errors.append(str(exc))
                        else:
                            raise ValueError(" | ".join(errors))
                        if count:
                            tsv = path.parent / "tables/screening-findings.tsv"
                            original = tsv if tsv.is_file() else path.parent / "screening-findings.tsv"
                            # The compact view promises both original documents. Only publish it if both exist.
                            if not original.is_file():
                                raise ValueError("Full screening-findings.tsv unavailable for compact PDF")
                            archive.write(path, f"{run}/{rel}")
                            archive.write(original, str(PurePosixPath(run, rel).parent / "tables/screening-findings.tsv"))
                        archive.write(pdf, entry)
                        row["PDF in ZIP"], row["Status"] = entry, "OK"
                        completed += 1
                    except (OSError, ValueError, subprocess.SubprocessError) as exc:
                        row["PDF in ZIP"], row["Status"] = "", str(exc)
                        print(f"  {exc}", flush=True)
                    finally:
                        server.active = None
                        pdf.unlink(missing_ok=True)
                inventory = io.StringIO()
                writer = csv.DictWriter(inventory, fieldnames=FIELDS)
                writer.writeheader()
                writer.writerows(rows)
                archive.writestr("print-inventory.csv", inventory.getvalue().encode("utf-8-sig"))
        finally:
            server.shutdown()
            thread.join()
            server.server_close()
        partial.rename(final)
    print(f"Complete PDFs {retained + completed}/{len(rows)} ({retained} retained, {completed} printed). ZIP: {final}", flush=True)
    return 0 if retained + completed == len(rows) else 2


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--retry", help="existing review ZIP with print-inventory.csv")
    group.add_argument("--run", action="append", default=[], help="run ID, repeat for additional runs")
    parser.add_argument("--runs-root", default=str(Path.home() / ".local/share/bactologix-dev/runs"))
    parser.add_argument("--dest", help="destination directory, defaults to the retry ZIP directory or current directory")
    parser.add_argument("--timeout", type=int, default=180, help="seconds per browser and report")
    args = parser.parse_args()
    browsers = [Path(browser) for browser in BROWSERS if Path(browser).is_file()]
    if not browsers or not Path(POWERSHELL).is_file():
        parser.exit(1, "Windows Edge or Chrome and Windows PowerShell are required from WSL.\n")
    if args.timeout < 1:
        parser.error("--timeout must be positive")
    previous = linux_path(args.retry) if args.retry else None
    dest = linux_path(args.dest) if args.dest else previous.parent if previous else Path.cwd()
    runs = linux_path(args.runs_root)
    try:
        if previous:
            with zipfile.ZipFile(previous) as archive:
                return run_export(runs, dest, [], archive, browsers, args.timeout)
        return run_export(runs, dest, list(dict.fromkeys(args.run)), None, browsers, args.timeout)
    except (OSError, ValueError, KeyError, zipfile.BadZipFile) as exc:
        parser.exit(1, str(exc) + "\n")


if __name__ == "__main__":
    raise SystemExit(main())
