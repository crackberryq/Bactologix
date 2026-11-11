"""Payload/receipt release gates; actual compiled PE execution runs on Windows CI."""
import copy
import importlib.util
import json
from pathlib import Path
import shutil
import struct
import zipfile

import pytest

ROOT = Path(__file__).parents[1]
spec = importlib.util.spec_from_file_location('windows_executable', ROOT / 'scripts/build_windows_executable.py')
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)
COMMIT = 'a' * 40


@pytest.fixture
def source(tmp_path):
    for item in ['scripts/windows', 'src/bactologix/ui']:
        shutil.copytree(ROOT / item, tmp_path / item)
    for item in ['scripts/build_windows_bundle.py', 'scripts/build_windows_executable.py',
                 'scripts/install_bactologix.py', 'docs/windows-desktop.txt']:
        target = tmp_path / item
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / item, target)
    (tmp_path / 'pyproject.toml').write_text('[project]\nname="bactologix"\nversion="0.1.0a104"\n')
    return tmp_path


def pe_fixture(payload):
    """Minimal managed-GUI headers only, never executed as a Windows program."""
    result = bytearray(512)
    result[:2] = b'MZ'
    struct.pack_into('<I', result, 0x3c, 128)
    result[128:132] = b'PE\0\0'
    struct.pack_into('<H', result, 152, 0x10b)
    struct.pack_into('<H', result, 152 + 68, 2)
    struct.pack_into('<II', result, 152 + 96 + 14 * 8, 8192, 72)
    return bytes(result) + payload


def artifacts(source, channel='stable', wheel=None):
    dist = source / 'dist'
    payload, manifest = builder.make_payload(source, dist, channel, COMMIT, wheel)
    embedded = json.dumps(manifest, sort_keys=True, separators=(',', ':')).encode()
    package = builder.names(channel)
    exe = dist / package['exe']
    exe.write_bytes(pe_fixture(payload.read_bytes() + embedded))
    receipt = {**manifest, 'artifact': {'name': exe.name, 'size': exe.stat().st_size,
                                      'sha256': builder.sha256(exe.read_bytes())},
               'payload_name': payload.name, 'source_hashes': builder.source_hashes(source)}
    (dist / package['receipt']).write_text(json.dumps(receipt))
    return dist, receipt


def test_release_gate_binds_executable_payload_and_source(source):
    dist, receipt = artifacts(source)
    accepted = builder.verify_build(source, dist, COMMIT, '0.1.0a104')
    assert [p.name for p in accepted] == ['Bactologix-Setup.exe', 'Bactologix-Setup.build.json']
    assert receipt['application_resolution'] == 'verified-current-release'
    assert not (source / 'installation.json').exists()


@pytest.mark.parametrize('field,value', [('source_commit', 'b' * 40), ('setup_version', '0.1.0a105'),
                                        ('channel', 'dev'), ('application_resolution', 'embedded-development-wheel')])
def test_wrong_build_receipt_blocks_publication(source, field, value):
    dist, receipt = artifacts(source)
    receipt[field] = value
    (dist / 'Bactologix-Setup.build.json').write_text(json.dumps(receipt))
    with pytest.raises(ValueError, match='receipt does not match'):
        builder.verify_build(source, dist, COMMIT, '0.1.0a104')


def test_changed_exe_or_source_blocks_publication(source):
    dist, receipt = artifacts(source)
    exe = dist / 'Bactologix-Setup.exe'
    exe.write_bytes(exe.read_bytes() + b'changed')
    with pytest.raises(ValueError, match='checksum/size'):
        builder.verify_build(source, dist, COMMIT, '0.1.0a104')
    dist, receipt = artifacts(source)
    script = source / 'scripts/windows/Bactologix.ps1'
    script.write_bytes(script.read_bytes() + b'\n# altered source\n')
    with pytest.raises(ValueError, match='source_hashes'):
        builder.verify_build(source, dist, COMMIT, '0.1.0a104')


def test_missing_windows_artifact_blocks_publication(source):
    dist, _ = artifacts(source)
    (dist / 'Bactologix-Setup.exe').unlink()
    with pytest.raises(ValueError, match='executable, build receipt and ZIP'):
        builder.verify_build(source, dist, COMMIT, '0.1.0a104')


@pytest.mark.parametrize('entry', ['Bactologix/../outside.ps1', 'Bactologix/a/b.ps1',
                                  'Other/Bactologix.ps1', 'Bactologix/CON.ps1', 'Bactologix/trailing.'])
def test_unsafe_payload_names_are_rejected(tmp_path, entry):
    path = tmp_path / 'bad.zip'
    with zipfile.ZipFile(path, 'w') as archive:
        archive.writestr(entry, 'inert fixture')
    with pytest.raises(ValueError, match='payload path'):
        builder.payload_files(path, 'Bactologix')


def test_managed_gui_header_required():
    assert builder.managed_windows_executable(pe_fixture(b''))
    assert not builder.managed_windows_executable(b'MZnot a Windows executable')
    damaged = bytearray(pe_fixture(b''))
    struct.pack_into('<H', damaged, 152 + 68, 3)  # Console subsystem.
    assert not builder.managed_windows_executable(damaged)


def test_embedded_payload_must_match_source_even_with_rewritten_receipts(source):
    dist, receipt = artifacts(source)
    payload = dist / 'Bactologix-Windows.zip'
    original = payload.read_bytes()
    with zipfile.ZipFile(payload, 'a') as archive:
        archive.writestr('Bactologix/unexpected.txt', 'inert fixture')
    receipt['archive_sha256'] = builder.sha256(payload.read_bytes())
    receipt['files'] = builder.payload_files(payload, 'Bactologix')
    exe = dist / 'Bactologix-Setup.exe'
    exe.write_bytes(exe.read_bytes().replace(original, payload.read_bytes()))
    receipt['artifact'].update(size=exe.stat().st_size, sha256=builder.sha256(exe.read_bytes()))
    (dist / 'Bactologix-Setup.build.json').write_text(json.dumps(receipt))
    with pytest.raises(ValueError, match='differs from the checked source'):
        builder.verify_build(source, dist, COMMIT, '0.1.0a104')


def test_dev_receipt_binds_pinned_wheel_and_channel(source):
    version = '0.1.0a104.dev1'
    (source / 'pyproject.toml').write_text(f'[project]\nname="bactologix"\nversion="{version}"\n')
    shutil.copyfile(ROOT / 'docs/development-install.txt', source / 'docs/development-install.txt')
    wheel = source / f'bactologix-{version}-py3-none-any.whl'
    with zipfile.ZipFile(wheel, 'w') as package:
        package.writestr(f'bactologix-{version}.dist-info/METADATA', f'Name: bactologix\nVersion: {version}\n')
    dist, receipt = artifacts(source, 'dev', wheel)
    assert receipt['application_resolution'] == 'embedded-development-wheel'
    accepted = builder.verify_build(source, dist, COMMIT, version, 'dev', wheel)
    assert accepted[0].name == 'Bactologix-Dev-Setup.exe'
    with zipfile.ZipFile(dist / 'Bactologix-Dev-Windows.zip') as package:
        manifest = json.loads(package.read('Bactologix-Dev/dev-package.json'))
        assert manifest['sha256'] == builder.sha256(wheel.read_bytes())
        assert package.read('Bactologix-Dev/' + wheel.name) == wheel.read_bytes()
    with zipfile.ZipFile(wheel, 'a') as package:
        package.writestr('changed.txt', 'different application contents')
    with pytest.raises(ValueError, match='differs from the checked source and wheel'):
        builder.verify_build(source, dist, COMMIT, version, 'dev', wheel)
    wrong_wheel = source / 'bactologix-0.1.0a105.dev1-py3-none-any.whl'
    shutil.copyfile(wheel, wrong_wheel)
    with pytest.raises(ValueError, match='exact setup version'):
        builder.make_payload(source, source / 'wrong', 'dev', COMMIT, wrong_wheel)
