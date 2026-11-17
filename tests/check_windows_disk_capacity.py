"""Exercise the production native probe on Windows without a WSL installation.

Run with ``python tests/check_windows_disk_capacity.py``. Only synthetic
registration metadata and temporary .wslconfig fixtures are supplied; native
Windows capacity calls are real. No user WSL settings or registration change.
The script literal is extracted without importing Linux-only application code.
"""
from __future__ import annotations

import ast
import base64
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest


SOURCE = Path(__file__).resolve().parents[1] / 'src/bactologix/disk_capacity.py'
TREE = ast.parse(SOURCE.read_text(encoding='utf-8'))
SCRIPT = next(ast.literal_eval(node.value) for node in TREE.body
              if isinstance(node, ast.Assign) and any(
                  isinstance(target, ast.Name) and target.id == '_WINDOWS_SCRIPT' for target in node.targets))
PREFIX = 'BACTOLOGIX_CAPACITY_V1:'

MOCK_REGISTRY = r'''
$fixture = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('__FIXTURE__')) | ConvertFrom-Json
function Get-ChildItem {
 param([string]$LiteralPath)
 if ($LiteralPath -ne 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Lxss') { throw 'Unexpected registry read in test.' }
 foreach ($entry in @($fixture.registrations)) {
  $item = New-Object PSObject -Property @{ DistributionName=$entry.name; BasePath=$entry.path }
  $item | Add-Member -MemberType ScriptMethod -Name GetValue -Value { param($key) return $this.$key }
  Write-Output $item
 }
}
'''


@unittest.skipUnless(os.name == 'nt', 'native Windows integration probe')
class NativeWindowsCapacityTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='bactologix-capacity-')
        self.addCleanup(self.temporary.cleanup)
        self.folder = Path(self.temporary.name)
        self.backing = self.folder / 'Ubuntu fixture'
        self.backing.mkdir()
        self.config = self.folder / '.wslconfig'

    def query(self, payload, registrations=None):
        encoded = base64.b64encode(json.dumps(payload).encode()).decode()
        script = SCRIPT.replace('__PAYLOAD__', encoded)
        if registrations is not None:
            fixture = base64.b64encode(json.dumps({'registrations': registrations}).encode()).decode()
            script = MOCK_REGISTRY.replace('__FIXTURE__', fixture) + script
        env = dict(os.environ, USERPROFILE=str(self.folder))
        completed = subprocess.run(['powershell.exe', '-NoLogo', '-NoProfile', '-NonInteractive',
                                    '-EncodedCommand', base64.b64encode(script.encode('utf-16-le')).decode()],
                                   stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   env=env, timeout=15, check=False)
        self.assertEqual(completed.returncode, 0, completed.stderr.decode(errors='replace'))
        records = [line[len(PREFIX):] for line in completed.stdout.decode('utf-8-sig').splitlines()
                   if line.startswith(PREFIX)]
        self.assertEqual(len(records), 1, completed.stdout.decode(errors='replace'))
        return json.loads(records[0])

    def discover(self, registrations=None):
        if registrations is None:
            registrations = [{'name': 'Ubuntu Fixture', 'path': str(self.backing)}]
        return self.query({'operation': 'discover', 'distribution': 'Ubuntu Fixture'}, registrations)

    def test_real_native_available_capacity_and_identity(self):
        result = self.query({'operation': 'sample', 'targets': [
            {'path': str(self.backing), 'label': 'Test backing storage', 'role': 'wsl_backing'}]})
        self.assertEqual(result['errors'], [])
        self.assertEqual(len(result['volumes']), 1)
        row = result['volumes'][0]
        self.assertGreater(row['total_bytes'], 0)
        self.assertGreater(row['available_bytes'], 0)
        self.assertLessEqual(row['available_bytes'], row['total_bytes'])
        self.assertTrue(row['id'].startswith('windows:'))
        self.assertEqual(row['probe_path'], str(self.backing))

    def test_swap_disabled_only_backing_is_required(self):
        self.config.write_text('[wsl2]\nswap=0\n', encoding='utf-8')
        result = self.discover()
        self.assertEqual(result['errors'], [])
        self.assertEqual([row['role'] for row in result['volumes']], ['wsl_backing'])

    def test_configured_escaped_swap_path_with_spaces(self):
        swap = self.folder / 'swap fixture' / 'wsl_swap.vhdx'
        swap.parent.mkdir()
        escaped = str(swap).replace('\\', '\\\\')
        self.config.write_text('[wsl2]\nmemory=50GB\nswap=100GB\nswapfile="' + escaped + '"\n', encoding='utf-8')
        result = self.discover()
        self.assertEqual(result['errors'], [])
        rows = {row['role']: row for row in result['volumes']}
        self.assertEqual(set(rows), {'wsl_backing', 'wsl_swap'})
        self.assertEqual(rows['wsl_swap']['path'], str(swap))
        self.assertEqual(rows['wsl_swap']['probe_path'], str(swap.parent))
        self.assertFalse(swap.exists(), 'Capacity probing must not create swap files.')

    def test_default_swap_and_other_config_section(self):
        self.config.write_text('[experimental]\nswap=0\n[wsl2]\n# swap=0\n', encoding='utf-8')
        result = self.discover()
        self.assertEqual(result['errors'], [])
        self.assertEqual({row['role'] for row in result['volumes']}, {'wsl_backing', 'wsl_swap'})
        self.assertTrue(next(row['path'] for row in result['volumes'] if row['role'] == 'wsl_swap').endswith('swap.vhdx'))

    def test_missing_registration_is_explicit_error(self):
        self.config.write_text('[wsl2]\nswap=0\n', encoding='utf-8')
        result = self.discover([{'name': 'Other distro', 'path': str(self.backing)}])
        self.assertEqual(result['volumes'], [])
        self.assertTrue(any('backing storage' in error for error in result['errors']))

    def test_missing_backing_directory_is_explicit_error(self):
        self.config.write_text('[wsl2]\nswap=0\n', encoding='utf-8')
        result = self.discover([{'name': 'Ubuntu Fixture', 'path': str(self.folder / 'missing')}])
        self.assertEqual(result['volumes'], [])
        self.assertTrue(result['errors'])

    def test_configuration_read_is_bounded(self):
        self.config.write_text('[wsl2]\n#' + 'x' * 65536, encoding='utf-8')
        result = self.discover()
        self.assertTrue(any('bounded read limit' in error for error in result['errors']))

    def test_previously_observed_directory_must_not_fall_back(self):
        result = self.query({'operation': 'sample', 'targets': [
            {'path': str(self.backing), 'probe_path': str(self.folder / 'missing'),
             'label': 'Missing backing', 'role': 'wsl_backing'}]})
        self.assertEqual(result['volumes'], [])
        self.assertTrue(result['errors'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
