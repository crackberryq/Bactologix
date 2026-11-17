"""Read-only capacity checks for workflow writes and WSL backing volumes.

Linux statvfs alone cannot detect a full Windows volume containing a growing
WSL disk. Discover that volume and swap storage through Windows, then use its
observed DrvFs mapping for fresh measurements. No automount root is assumed.
Unverifiable required volumes produce errors, never unlimited-capacity values.
"""
from __future__ import annotations

import base64
import json
import ntpath
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import threading

import yaml

from .store import now

WINDOWS_TIMEOUT = 3.0
MAX_WINDOWS_OUTPUT = 65536
_PREFIX = 'BACTOLOGIX_CAPACITY_V1:'
_TEMP_KEYS = {'tmpdir', 'tmp_dir', 'temp_dir', 'temp_directory', 'tmp_directory', 'scratch_dir'}

# Payload values are base64 JSON, never interpolated PowerShell source. The
# script only reads selected registration/configuration metadata and capacity.
_WINDOWS_SCRIPT = r'''
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
$payload = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('__PAYLOAD__')) | ConvertFrom-Json
Add-Type -TypeDefinition @'
using System;
using System.Text;
using System.Runtime.InteropServices;
public static class BactologixDiskCapacity {
 [DllImport("kernel32.dll", CharSet=CharSet.Unicode, SetLastError=true)]
 public static extern bool GetDiskFreeSpaceEx(string path, out ulong available, out ulong total, out ulong free);
 [DllImport("kernel32.dll", CharSet=CharSet.Unicode, SetLastError=true)]
 public static extern bool GetVolumePathName(string path, StringBuilder volume, uint length);
 [DllImport("kernel32.dll", CharSet=CharSet.Unicode, SetLastError=true)]
 public static extern bool GetVolumeNameForVolumeMountPoint(string path, StringBuilder volume, uint length);
}
'@ | Out-Null
$errors = @()
$targets = @()
if ($payload.operation -eq 'discover') {
 try {
  $keys = @(Get-ChildItem -LiteralPath 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Lxss' |
   Where-Object { $_.GetValue('DistributionName') -ceq $payload.distribution })
  if ($keys.Count -ne 1) { throw 'Current distribution registration is missing or ambiguous.' }
  $base = [string]$keys[0].GetValue('BasePath')
  if (-not $base -or -not [IO.Directory]::Exists($base)) { throw 'Current distribution storage directory is unavailable.' }
  $targets += @{ path=$base; label=('Windows storage for ' + $payload.distribution); role='wsl_backing' }
 } catch { $errors += ('Cannot identify WSL backing storage: ' + $_.Exception.Message) }
 try {
  $swap = $null; $swapFile = $null; $section = ''
  $config = Join-Path $env:USERPROFILE '.wslconfig'
  if ([IO.File]::Exists($config)) {
   if ((Get-Item -LiteralPath $config).Length -gt 65536) { throw 'WSL configuration exceeds the bounded read limit.' }
   foreach ($line in [IO.File]::ReadAllLines($config)) {
    $text = $line.Trim()
    if ($text -match '^\[([^\]]+)\]\s*$') { $section = $Matches[1].ToLowerInvariant(); continue }
    if ($section -ne 'wsl2' -or $text.StartsWith('#') -or $text.StartsWith(';')) { continue }
    if ($text -match '^swap\s*=\s*(.*?)\s*$') { $swap = $Matches[1] }
    if ($text -match '^swapfile\s*=\s*(.*?)\s*$') { $swapFile = $Matches[1] }
   }
  }
  if ($null -eq $swap -or $swap -notmatch '^0+(?:B|KB|MB|GB|TB)?$') {
   if (-not $swapFile) { $swapFile = Join-Path ([IO.Path]::GetTempPath()) 'swap.vhdx' }
   else {
    if (($swapFile.StartsWith('"') -and $swapFile.EndsWith('"')) -or ($swapFile.StartsWith("'") -and $swapFile.EndsWith("'"))) {
     $swapFile = $swapFile.Substring(1, $swapFile.Length - 2)
    }
    $swapFile = $swapFile.Replace('\\', '\').Replace('/', '\')
   }
   if ($swapFile -notmatch '^(?:[A-Za-z]:\\|\\\\[^\\]+\\[^\\]+\\)') { throw 'Swap path is not an absolute Windows path.' }
   $targets += @{ path=$swapFile; label='Configured Windows swap storage'; role='wsl_swap' }
  }
 } catch { $errors += ('Cannot identify WSL swap storage: ' + $_.Exception.Message) }
} elseif ($payload.operation -eq 'sample') { $targets = @($payload.targets) }
else { throw 'Unknown capacity probe operation.' }
$volumes = @()
foreach ($target in $targets) {
 try {
  $path = [string]$target.path
  if ($target.probe_path) {
   $existing = [string]$target.probe_path
   if (-not [IO.Directory]::Exists($existing)) { throw 'Previously identified storage directory is unavailable.' }
  } else {
   $existing = $path
   if ([IO.File]::Exists($existing)) { $existing = [IO.Path]::GetDirectoryName($existing) }
   while (-not [IO.Directory]::Exists($existing)) {
    $parent = [IO.Path]::GetDirectoryName($existing.TrimEnd('\'))
    if (-not $parent -or $parent -eq $existing) { throw 'No accessible directory for storage path.' }
    $existing = $parent
   }
  }
  [UInt64]$available = 0; [UInt64]$total = 0; [UInt64]$free = 0
  if (-not [BactologixDiskCapacity]::GetDiskFreeSpaceEx($existing, [ref]$available, [ref]$total, [ref]$free)) {
   throw ('Windows free-space query failed (' + [Runtime.InteropServices.Marshal]::GetLastWin32Error() + ').')
  }
  $mount = New-Object Text.StringBuilder 32768
  if ([BactologixDiskCapacity]::GetVolumePathName($existing, $mount, [UInt32]$mount.Capacity)) { $volumePath = $mount.ToString() }
  else { $volumePath = [IO.Path]::GetPathRoot($existing) }
  $name = New-Object Text.StringBuilder 1024
  if ([BactologixDiskCapacity]::GetVolumeNameForVolumeMountPoint($volumePath, $name, [UInt32]$name.Capacity)) { $identity = $name.ToString() }
  else { $identity = $volumePath }
  if (-not $identity -or $total -eq 0) { throw 'Windows storage identity or capacity is unavailable.' }
  $volumes += @{ id=('windows:' + $identity.ToLowerInvariant()); label=$target.label; path=$path;
   probe_path=$existing; volume_path=$volumePath; role=$target.role; kind='windows_host';
   available_bytes=$available; total_bytes=$total }
 } catch { $errors += ([string]$target.label + ': ' + $_.Exception.Message) }
}
[Console]::WriteLine('BACTOLOGIX_CAPACITY_V1:' + (@{ volumes=@($volumes); errors=@($errors) } | ConvertTo-Json -Depth 5 -Compress))
'''


def _unescape_mount(value):
    return re.sub(r'\\([0-7]{3})', lambda m: chr(int(m[1], 8)), value)


def _windows_path(value):
    value = str(value).replace('/', '\\')
    if value.lower().startswith('\\\\?\\unc\\'):
        value = '\\\\' + value[8:]
    elif value.startswith('\\\\?\\'):
        value = value[4:]
    return ntpath.normpath(value)


def _drvfs_mounts():
    """Return observed Windows roots and Linux mount points, including custom roots."""
    result = []
    try:
        text = Path('/proc/self/mountinfo').read_text()
    except OSError:
        return result
    for line in text.splitlines():
        before, separator, after = line.partition(' - ')
        left, right = before.split(), after.split()
        if not separator or len(left) < 5 or len(right) < 3 or right[0] not in {'drvfs', '9p'}:
            continue
        source = _unescape_mount(right[1])
        if not ntpath.isabs(_windows_path(source)):
            match = re.search(r'(?:^|[;,])path=([^;]+)', _unescape_mount(right[2]))
            source = match[1] if match else ''
        source = _windows_path(source)
        if not re.match(r'^(?:[A-Za-z]:\\|\\\\[^\\]+\\[^\\]+)', source):
            continue
        subtree = _unescape_mount(left[3]).strip('/')
        source = ntpath.join(source, subtree.replace('/', '\\')) if subtree else source
        result.append((source, Path(_unescape_mount(left[4]))))
    return result


def _mapped_path(windows_path, mounts):
    target = _windows_path(windows_path)
    candidates = []
    for source, mount in mounts:
        try:
            common = ntpath.commonpath([source, target])
            if common.casefold().rstrip('\\') != source.casefold().rstrip('\\'):
                continue
            relative = ntpath.relpath(target, source)
            parts = [] if relative == '.' else relative.split('\\')
            if '..' not in parts:
                candidates.append((len(source), mount.joinpath(*parts)))
        except ValueError:
            continue
    return max(candidates, key=lambda x: x[0])[1] if candidates else None


def _powershell(mounts):
    found = shutil.which('powershell.exe')
    if found:
        return found
    # Interop PATH can be disabled while interop itself remains enabled.
    for _, mount in mounts:
        candidate = mount / 'Windows/System32/WindowsPowerShell/v1.0/powershell.exe'
        if candidate.is_file():
            return str(candidate)
    raise OSError('Windows PowerShell is unavailable; WSL backing-drive capacity cannot be verified.')


def _windows_query(payload, mounts):
    encoded_payload = base64.b64encode(json.dumps(payload, ensure_ascii=True).encode()).decode()
    script = _WINDOWS_SCRIPT.replace('__PAYLOAD__', encoded_payload)
    encoded = base64.b64encode(script.encode('utf-16-le')).decode()
    result = subprocess.run([_powershell(mounts), '-NoLogo', '-NoProfile', '-NonInteractive', '-EncodedCommand', encoded],
                            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            timeout=WINDOWS_TIMEOUT, check=False)
    if result.returncode:
        raise OSError('Windows capacity probe failed (exit ' + str(result.returncode) + ').')
    if len(result.stdout) > MAX_WINDOWS_OUTPUT:
        raise ValueError('Windows capacity probe exceeded the response limit.')
    lines = result.stdout.decode('utf-8-sig').splitlines()
    records = [line[len(_PREFIX):] for line in lines if line.startswith(_PREFIX)]
    if len(records) != 1:
        raise ValueError('Windows capacity probe did not return one verified response.')
    response = json.loads(records[0])
    if not isinstance(response, dict) or not isinstance(response.get('volumes'), list) or not isinstance(response.get('errors'), list):
        raise ValueError('Windows capacity probe returned an invalid response.')
    if len(response['volumes']) > 4 or len(response['errors']) > 8:
        raise ValueError('Windows capacity probe returned too many entries.')
    for row in response['volumes']:
        if (not isinstance(row, dict) or any(not isinstance(row.get(k), str) or not row[k] for k in ('id', 'path', 'probe_path', 'label', 'role'))
                or any(type(row.get(k)) is not int or row[k] < 0 for k in ('available_bytes', 'total_bytes'))
                or row['total_bytes'] == 0 or row['available_bytes'] > row['total_bytes']):
            raise ValueError('Windows capacity probe returned an invalid volume.')
    if any(not isinstance(item, str) for item in response['errors']):
        raise ValueError('Windows capacity probe returned invalid errors.')
    return response


def _wsl_context():
    name = os.environ.get('WSL_DISTRO_NAME', '')
    release = platform.release().lower()
    return bool(name or os.environ.get('WSL_INTEROP') or 'microsoft' in release or 'wsl' in release), name


def _existing_directory(path):
    current = path
    while True:
        try:
            info = current.stat()
            if current.is_dir():
                return current.resolve(), info
            return current.parent.resolve(), current.parent.stat()
        except FileNotFoundError:
            if current == current.parent:
                raise
            current = current.parent


class CapacityProbe:
    """Read fresh capacity; missing required measurements remain explicit errors.

    Add actual subprocess cwd/environment with ``include_command`` before launch.
    Future output paths use an existing ancestor; later disappearance of that
    ancestor is an error rather than silently switching to another filesystem.
    """

    def __init__(self, store, run, *, cwd=None, extra_env=None):
        self.store, self.run = store, run or {}
        self.paths = {}
        self.command_temporary = {}
        self._paths_lock = threading.RLock()
        self.anchors = {}
        self.path_errors = []
        self.host_targets = None
        self.is_wsl, self.distribution = _wsl_context()
        self._add(store.home, 'Workspace and logs')
        folder = self.run.get('folder')
        if folder:
            self._add(folder, 'Saved run')
            self._add(Path(folder) / 'workflow', 'Workflow working directory')
        if self.run.get('output'):
            self._add(self.run['output'], 'Run outputs')
        for key, label in [('controller', 'Runner environment'), ('rule_cache', 'Tool environments')]:
            if self.run.get(key):
                self._add(self.run[key], label)
        if self.run.get('run_type') == 'followup':
            parent = store.get('runs', self.run.get('parent'), {})
            if parent.get('folder'):
                self._add(Path(parent['folder']) / 'followup-state', 'Shared follow-up working directory')
            else:
                self.path_errors.append('The follow-up working directory cannot be identified.')
        self.include_command(cwd=cwd, extra_env=extra_env)
        try:
            config = self.run.get('config') or {}
            config = yaml.safe_load(config) if isinstance(config, str) else config
            self._config_temporary(config, Path(cwd or (Path(folder) / 'workflow' if folder else store.home)))
        except (ValueError, TypeError, yaml.YAMLError):
            self.path_errors.append('Saved temporary-directory settings cannot be read.')

    def _config_temporary(self, config, base, depth=0):
        if not isinstance(config, dict):
            return
        if depth > 8:
            self.path_errors.append('Nested saved temporary-directory settings exceed the inspection limit.')
            return
        for key, value in config.items():
            if str(key).lower() in _TEMP_KEYS:
                if isinstance(value, str) and value.strip():
                    self._add(value, 'Configured temporary files', base=base)
                elif value not in (None, ''):
                    self.path_errors.append('A saved temporary-directory setting is not a path.')
            elif isinstance(value, dict):
                self._config_temporary(value, base, depth + 1)

    def _add(self, value, label, base=None, destination=None):
        try:
            path = Path(value).expanduser()
            if not path.is_absolute():
                path = Path(base or self.store.home) / path
            path = Path(os.path.abspath(path))
            with self._paths_lock:
                (self.paths if destination is None else destination).setdefault(str(path), label)
        except (ValueError, TypeError, OSError):
            self.path_errors.append('Cannot identify storage for ' + label + '.')

    def include_paths(self, paths):
        """Add paths or a mapping of paths to user-facing labels."""
        if isinstance(paths, dict):
            for path, label in paths.items():
                self._add(path, str(label))
        else:
            for path in paths:
                self._add(path, 'Workflow write location')

    def include_command(self, cwd=None, extra_env=None):
        if cwd:
            self._add(cwd, 'Command working directory')
        env = dict(os.environ)
        env.update(extra_env or {})
        temporary = {}
        for key in ('TMPDIR', 'TEMP', 'TMP'):
            if env.get(key):
                self._add(env[key], key + ' temporary files', base=cwd or Path.cwd(), destination=temporary)
                break
        if not temporary:
            self._add('/tmp', 'System temporary files', destination=temporary)
        elif any(not Path(path).is_dir() or not os.access(path, os.W_OK | os.X_OK) for path in temporary):
            # Libraries may fall back to the system temporary directory when an
            # explicitly selected location does not exist or is inaccessible.
            self._add('/tmp', 'Fallback temporary files', destination=temporary)
        with self._paths_lock:
            self.command_temporary = temporary

    @staticmethod
    def _reading(path):
        info, capacity = path.stat(), os.statvfs(path)
        total = capacity.f_blocks * capacity.f_frsize
        available = max(0, capacity.f_bavail) * capacity.f_frsize
        if total <= 0:
            raise OSError('Filesystem capacity is unavailable.')
        if capacity.f_flag & getattr(os, 'ST_RDONLY', 1):
            raise OSError('Filesystem is read-only.')
        return info.st_dev, available, total

    def sample(self):
        rows, errors = [], list(self.path_errors)
        with self._paths_lock:
            paths = dict(self.paths)
            paths.update(self.command_temporary)
        for value, label in paths.items():
            path = Path(value)
            try:
                previous = self.anchors.get(value)
                if previous:
                    previous_path, previous_device = previous
                    if not previous_path.is_dir() or previous_path.stat().st_dev != previous_device:
                        raise OSError('Previously identified storage directory is unavailable or its mounted filesystem changed.')
                anchor, _ = _existing_directory(path)
                device, available, total = self._reading(anchor)
                if previous and device != previous[1]:
                    raise OSError('The workflow storage filesystem changed after monitoring started.')
                self.anchors[value] = (anchor, device)
                rows.append({'id': 'linux:' + str(device), 'kind': 'linux', 'label': label,
                             'path': str(anchor), 'available_bytes': available, 'total_bytes': total})
            except (OSError, ValueError, RuntimeError) as exc:
                errors.append(label + ': ' + str(exc))
        if self.is_wsl:
            mounts = _drvfs_mounts()
            if not self.distribution:
                errors.append('WSL distribution identity is missing; its Windows backing drive cannot be verified.')
            else:
                try:
                    if self.host_targets is None:
                        discovery = _windows_query({'operation': 'discover', 'distribution': self.distribution}, mounts)
                        errors.extend(discovery['errors'])
                        has_backing = any(row.get('role') == 'wsl_backing' for row in discovery['volumes'])
                        if not has_backing:
                            errors.append('WSL backing-drive capacity could not be identified.')
                        if not discovery['errors'] and has_backing:
                            self.host_targets = discovery['volumes']
                        rows.extend(discovery['volumes'])
                    else:
                        fallback = []
                        for target in self.host_targets:
                            mapped = _mapped_path(target['probe_path'], mounts)
                            try:
                                if mapped is None:
                                    raise OSError('No accessible DrvFs mapping.')
                                _, available, total = self._reading(mapped)
                                rows.append({**target, 'kind': 'windows_host', 'available_bytes': available,
                                             'total_bytes': total})
                            except (OSError, ValueError, RuntimeError):
                                fallback.append(target)
                        if fallback:
                            result = _windows_query({'operation': 'sample', 'targets': fallback}, mounts)
                            rows.extend(result['volumes'])
                            errors.extend(result['errors'])
                            if len(result['volumes']) != len(fallback) and not result['errors']:
                                errors.append('A required Windows storage volume did not return a capacity reading.')
                except (OSError, ValueError, UnicodeError, subprocess.SubprocessError) as exc:
                    errors.append('Cannot verify Windows backing storage: ' + str(exc))
        volumes = {}
        for row in rows:
            key = row['id']
            if key not in volumes:
                volumes[key] = dict(row, labels=[row['label']], paths=[row['path']])
            else:
                old = volumes[key]
                old['available_bytes'] = min(old['available_bytes'], row['available_bytes'])
                if row['label'] not in old['labels']:
                    old['labels'].append(row['label'])
                if row['path'] not in old['paths']:
                    old['paths'].append(row['path'])
        return {'volumes': list(volumes.values()), 'errors': list(dict.fromkeys(errors)), 'checked_at': now()}
