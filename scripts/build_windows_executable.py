"""Build a transparent per-user Windows setup EXE with the reviewed ZIP embedded."""
from __future__ import annotations
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import struct
import tempfile
import tomllib
import zipfile

SCHEMA = 1
MAX_PAYLOAD_BYTES = 256 * 1024 * 1024


def sha256(value):
    return hashlib.sha256(value).hexdigest()


def managed_windows_executable(content):
    try:
        offset = struct.unpack_from('<I', content, 0x3c)[0]
        if content[:2] != b'MZ' or content[offset:offset+4] != b'PE\0\0':
            return False
        optional = offset + 24
        magic = struct.unpack_from('<H', content, optional)[0]
        directory = optional + (96 if magic == 0x10b else 112 if magic == 0x20b else len(content))
        subsystem = struct.unpack_from('<H', content, optional + 68)[0]
        cli_address, cli_size = struct.unpack_from('<II', content, directory + 14 * 8)
        return subsystem == 2 and cli_address > 0 and cli_size >= 72
    except (ValueError, struct.error):
        return False


def names(channel):
    if channel not in {'stable', 'dev'}:
        raise ValueError('Choose stable or dev setup channel.')
    product = 'Bactologix-Dev' if channel == 'dev' else 'Bactologix'
    return {'product': product, 'exe': product + '-Setup.exe',
            'receipt': product + '-Setup.build.json', 'zip': product + '-Windows.zip'}


def load_bundle(root):
    spec = importlib.util.spec_from_file_location('windows_setup_bundle', root / 'scripts/build_windows_bundle.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def payload_files(payload, product):
    result, seen, total = [], set(), 0
    with zipfile.ZipFile(payload) as archive:
        for entry in archive.infolist():
            prefix = product + '/'
            name = entry.filename[len(prefix):] if entry.filename.startswith(prefix) else ''
            if (not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,180}', name)
                    or name.endswith('.') or re.match(r'(?i)^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\.|$)', name)
                    or name.lower() in seen or entry.is_dir()
                    or (entry.external_attr >> 16) & 0o170000 == 0o120000):
                raise ValueError('Unexpected or duplicate Windows payload path: ' + entry.filename)
            seen.add(name.lower())
            total += entry.file_size
            if entry.file_size > MAX_PAYLOAD_BYTES or total > MAX_PAYLOAD_BYTES:
                raise ValueError('Windows payload exceeds the supported size.')
            content = archive.read(entry)
            result.append({'name': name, 'size': len(content), 'sha256': sha256(content)})
    required = {product + '.ps1', product + '.vbs', product + '.cmd', product + '-Setup.cmd',
                'brand.ico', 'brand.png', 'install_bactologix.py', 'README.txt'}
    if not required.issubset({item['name'] for item in result}):
        raise ValueError('Windows payload is missing reviewed setup files.')
    if len(result) > 32:
        raise ValueError('Too many Windows payload files.')
    return sorted(result, key=lambda item: item['name'])


def source_hashes(root):
    files = [root / 'scripts/build_windows_bundle.py', root / 'scripts/build_windows_executable.py',
             root / 'scripts/windows/SetupBootstrap.cs', root / 'scripts/windows/setup.manifest',
             root / 'scripts/install_bactologix.py', root / 'docs/windows-desktop.txt',
             root / 'pyproject.toml', root / 'src/bactologix/ui/brand.ico',
             root / 'src/bactologix/ui/brand.png']
    development = root / 'docs/development-install.txt'
    if development.is_file():
        files.append(development)
    files.extend(sorted(p for p in (root / 'scripts/windows').iterdir()
                        if p.suffix in {'.ps1', '.cmd', '.vbs'}))
    return {p.relative_to(root).as_posix(): sha256(p.read_bytes()) for p in sorted(set(files))}


def make_payload(root, output, channel, commit, wheel=None):
    if not re.fullmatch('[0-9a-f]{40}', commit):
        raise ValueError('Build from an exact source commit.')
    version = tomllib.loads((root / 'pyproject.toml').read_text())['project']['version']
    if not re.fullmatch(r'\d+\.\d+\.\d+(?:[ab]\d+|rc\d+)?(?:\.dev\d+)?', version):
        raise ValueError('Unexpected setup version.')
    if ('.dev' in version) != (channel == 'dev'):
        raise ValueError('Setup version and channel do not match.')
    package = names(channel)
    output.mkdir(parents=True, exist_ok=True)
    bundle = load_bundle(root)
    payload = output / package['zip']
    if channel == 'dev':
        if not wheel or not Path(wheel).is_file():
            raise ValueError('Dev setup requires the exact tested application wheel.')
        wheel = Path(wheel)
        if wheel.name != f'bactologix-{version}-py3-none-any.whl':
            raise ValueError('Dev setup wheel must match its exact setup version.')
        with zipfile.ZipFile(wheel) as package_wheel:
            metadata = package_wheel.read(f'bactologix-{version}.dist-info/METADATA').decode('utf-8')
            if 'Name: bactologix' not in metadata.splitlines() or f'Version: {version}' not in metadata.splitlines():
                raise ValueError('Dev setup wheel metadata does not match its exact setup version.')
        bundle.build(root, payload, channel='dev', wheel=wheel)
    else:
        if wheel:
            raise ValueError('Regular setup resolves the verified regular release at installation time.')
        bundle.build(root, payload)
    manifest = {'schema': SCHEMA, 'product': package['product'], 'channel': channel,
                'setup_version': version, 'source_commit': commit,
                'application_resolution': 'embedded-development-wheel' if channel == 'dev' else 'verified-current-release',
                'archive_sha256': sha256(payload.read_bytes()),
                'files': payload_files(payload, package['product'])}
    return payload, manifest


def compiler_path():
    root = Path(os.environ.get('WINDIR', r'C:\Windows')) / 'Microsoft.NET'
    for framework in ('Framework64', 'Framework'):
        compiler = root / framework / 'v4.0.30319/csc.exe'
        if compiler.is_file():
            return compiler
    raise RuntimeError('The Windows .NET Framework C# compiler is required to build setup.')


def build(root, output, commit, channel='stable', wheel=None):
    root, output = Path(root).resolve(), Path(output).resolve()
    if os.name != 'nt':
        raise RuntimeError('Compile the installer in the Windows release job.')
    payload, manifest = make_payload(root, output, channel, commit, wheel)
    package = names(channel)
    executable = output / package['exe']
    manifest_bytes = json.dumps(manifest, sort_keys=True, separators=(',', ':')).encode('utf-8')
    numeric = re.match(r'(\d+)\.(\d+)\.(\d+)', manifest['setup_version']).groups()
    revision = re.search(r'[ab](\d+)', manifest['setup_version'])
    file_version = '.'.join((*numeric, revision.group(1) if revision else '0'))
    if any(int(v) > 65534 for v in file_version.split('.')):
        raise ValueError('Setup file version is outside Windows limits.')
    with tempfile.TemporaryDirectory(prefix='bactologix-exe-build-') as folder:
        folder = Path(folder)
        metadata = folder / 'BuildInfo.cs'
        metadata.write_text('''using System.Reflection;
[assembly: AssemblyTitle(%s)]
[assembly: AssemblyProduct(%s)]
[assembly: AssemblyCompany("GenoMix Human Link")]
[assembly: AssemblyDescription("Current-user setup for Bactologix and its Windows launcher")]
[assembly: AssemblyVersion(%s)]
[assembly: AssemblyFileVersion(%s)]
[assembly: AssemblyInformationalVersion(%s)]
internal static class BuildInfo {
    internal const string Product = %s;
    internal const string Channel = %s;
    internal const string SetupVersion = %s;
    internal const string SourceCommit = %s;
    internal const string ManifestSha256 = %s;
}
''' % tuple(json.dumps(s) for s in (package['product']+' Setup', package['product'],
                                  '.'.join((*numeric, '0')), file_version, manifest['setup_version'],
                                  package['product'], channel, manifest['setup_version'], commit,
                                  sha256(manifest_bytes))), encoding='utf-8')
        manifest_path = folder / 'payload.json'
        manifest_path.write_bytes(manifest_bytes)
        command = [str(compiler_path()), '/nologo', '/target:winexe', '/platform:anycpu',
                   '/optimize+', '/utf8output', '/out:' + str(executable),
                   '/win32icon:' + str(root / 'src/bactologix/ui/brand.ico'),
                   '/win32manifest:' + str(root / 'scripts/windows/setup.manifest'),
                   '/reference:System.Windows.Forms.dll', '/reference:System.Drawing.dll',
                   '/reference:System.IO.Compression.dll', '/reference:System.Runtime.Serialization.dll',
                   '/resource:' + str(payload) + ',Bactologix.Setup.Payload',
                   '/resource:' + str(manifest_path) + ',Bactologix.Setup.Manifest',
                   str(root / 'scripts/windows/SetupBootstrap.cs'), str(metadata)]
        subprocess.run(command, check=True)
    if not managed_windows_executable(executable.read_bytes()):
        raise ValueError('Compiler did not produce a Windows executable.')
    receipt = {**manifest, 'artifact': {'name': executable.name, 'size': executable.stat().st_size,
                                      'sha256': sha256(executable.read_bytes())},
               'payload_name': payload.name, 'source_hashes': source_hashes(root)}
    (output / package['receipt']).write_text(json.dumps(receipt, indent=2, sort_keys=True)+'\n', encoding='utf-8')
    return executable


def verify_build(root, dist, commit, version, channel='stable', wheel=None):
    """Accept only the Windows job artifact for this exact checked source."""
    root, dist = Path(root), Path(dist)
    package = names(channel)
    executable, receipt_path, payload = (dist / package[key] for key in ('exe', 'receipt', 'zip'))
    if not all(p.is_file() for p in (executable, receipt_path, payload)):
        raise ValueError('The checked Windows executable, build receipt and ZIP are required.')
    receipt = json.loads(receipt_path.read_text())
    expected_resolution = 'embedded-development-wheel' if channel == 'dev' else 'verified-current-release'
    expected = {'schema': SCHEMA, 'product': package['product'], 'channel': channel,
                'setup_version': version, 'source_commit': commit,
                'application_resolution': expected_resolution, 'payload_name': package['zip'],
                'source_hashes': source_hashes(root)}
    for key, value in expected.items():
        if receipt.get(key) != value:
            raise ValueError('Windows executable build receipt does not match this source: ' + key)
    content = executable.read_bytes()
    actual = {'name': executable.name, 'size': len(content), 'sha256': sha256(content)}
    if receipt.get('artifact') != actual or not managed_windows_executable(content):
        raise ValueError('Windows executable checksum/size or format mismatch.')
    if not 0 < len(content) <= MAX_PAYLOAD_BYTES:
        raise ValueError('Windows executable exceeds the supported size.')
    if receipt.get('archive_sha256') != sha256(payload.read_bytes()):
        raise ValueError('Windows payload checksum mismatch.')
    if receipt.get('files') != payload_files(payload, package['product']):
        raise ValueError('Windows payload file manifest mismatch.')
    with tempfile.TemporaryDirectory(prefix='bactologix-payload-review-') as folder:
        expected_payload, manifest = make_payload(root, Path(folder), channel, commit, wheel)
        if expected_payload.read_bytes() != payload.read_bytes():
            raise ValueError('Windows payload differs from the checked source and wheel.')
        embedded_manifest = json.dumps(manifest, sort_keys=True, separators=(',', ':')).encode('utf-8')
        if payload.read_bytes() not in content or embedded_manifest not in content:
            raise ValueError('Windows executable does not contain the checked setup payload.')
    return [executable, receipt_path]


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=Path('dist'))
    parser.add_argument('--commit', required=True)
    parser.add_argument('--channel', choices=['stable', 'dev'], default='stable')
    parser.add_argument('--wheel', type=Path)
    args = parser.parse_args()
    print(build(Path(__file__).resolve().parents[1], args.output, args.commit, args.channel, args.wheel))
