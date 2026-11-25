"""Read-only capacity checks for workflow writes and WSL backing volumes.

Linux statvfs alone cannot detect a full Windows volume containing a growing
WSL disk. Discover that volume and swap storage through Windows, then use its
observed DrvFs mapping for fresh measurements. No automount root is assumed.
Unverifiable required volumes produce errors, never unlimited-capacity values.
"""
from __future__ import annotations

import base64
from datetime import datetime, timezone
import json
import math
import ntpath
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import threading
import time

import yaml

from .desktop import STORAGE_SESSION_DIR
from .store import now

WINDOWS_TIMEOUT = 3.0
MAX_WINDOWS_OUTPUT = 65536
SAMPLE_MAX_AGE_SECONDS = 5.0
DESKTOP_BINDING_SECONDS = 30.0
_PREFIX = 'BACTOLOGIX_CAPACITY_V1:'
_TEMP_KEYS = {'tmpdir', 'tmp_dir', 'temp_dir', 'temp_directory', 'tmp_directory', 'scratch_dir'}

# Payload values are base64 JSON, never interpolated PowerShell source. The
# script only reads selected registration/configuration metadata and capacity.
_WINDOWS_SCRIPT = Path(__file__).with_name('data').joinpath('windows_capacity.ps1').read_text(encoding='utf-8')


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
    return _validate_windows_response(json.loads(records[0]))


def _validate_windows_response(response):
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


class _DesktopReadingGap(ValueError):
    def __init__(self, message, targets=None):
        super().__init__(message)
        self.targets = targets


def _desktop_observation(directory, distribution, *, backing_only=False):
    """Read one complete, current observation from this Windows desktop session."""
    root = Path(directory)
    if not root.is_absolute() or not re.fullmatch(r'[0-9a-f]{32}', root.name):
        raise ValueError('Windows desktop storage session directory is invalid.')
    path = root / 'windows-storage.json'
    if not path.exists() and not path.is_symlink():
        raise _DesktopReadingGap('Windows desktop storage reading is unavailable.')
    if not path.is_file() or path.is_symlink():
        raise ValueError('Windows desktop storage reading is unavailable.')
    with path.open('rb') as stream:
        raw = stream.read(MAX_WINDOWS_OUTPUT + 1)
    if len(raw) > MAX_WINDOWS_OUTPUT:
        raise ValueError('Windows desktop storage reading exceeded the response limit.')
    response = _validate_windows_response(json.loads(raw.decode('utf-8-sig')))
    if (type(response.get('schema')) is not int or response['schema'] != 1
            or response.get('session') != root.name or response.get('distribution') != distribution):
        raise ValueError('Windows desktop storage reading belongs to a different session or distribution.')
    sampled, received = response.get('time'), time.time()
    if type(sampled) not in (int, float) or not math.isfinite(sampled) or sampled > received + 2:
        raise ValueError('Windows desktop storage reading is stale or has an invalid timestamp.')
    if backing_only:
        backing = [r for r in response['volumes'] if r['role'] == 'wsl_backing']
        if len(backing) != 1:
            raise ValueError('Windows backing storage reading is missing or ambiguous.')
        response['volumes'] = backing
        response['swap_required'] = False
        # A partial legacy desktop snapshot cannot identify the source of all
        # errors. Recheck the backing volume independently through interop.
    for row in response['volumes']:
        if (row.get('kind') != 'windows_host' or not row['id'].startswith('windows:')
                or any(not ntpath.splitdrive(row[key])[0] or not ntpath.isabs(row[key]) for key in ('path', 'probe_path'))):
            raise ValueError('Windows desktop storage volume identity is invalid.')
    roles = [row['role'] for row in response['volumes']]
    swap = response.get('swap_required')
    if type(swap) is not bool or sorted(roles) != (['wsl_backing', 'wsl_swap'] if swap else ['wsl_backing']):
        response['errors'].append('Windows desktop could not verify every required backing or swap volume.')
    if not received - SAMPLE_MAX_AGE_SECONDS <= sampled <= received:
        message = 'Windows desktop storage reading is stale or has an invalid timestamp.'
        if response['errors']:
            raise ValueError(message + ' ' + ' '.join(response['errors']))
        raise _DesktopReadingGap(message, response['volumes'])
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

    backing_only = False

    def __init__(self, store, run, *, cwd=None, extra_env=None):
        self.store, self.run = store, run or {}
        self.paths = {}
        self.command_temporary = {}
        self._paths_lock = threading.RLock()
        self.anchors = {}
        self.path_errors = []
        self.host_targets = None
        self.host_identity = None
        self.desktop_bindings = []
        self.desktop_binding_expires = 0.0
        self.is_wsl, self.distribution = _wsl_context()
        if self.backing_only:
            self._add('/', 'Linux root filesystem')
            return
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

    def _interop_volumes(self, mounts):
        rows, errors = [], []
        if self.host_targets is None:
            discovery = _windows_query({'operation': 'discover', 'distribution': self.distribution, **({'backing_only': True} if self.backing_only else {})}, mounts)
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
                try:
                    result = _windows_query({'operation': 'sample', 'targets': fallback}, mounts)
                except (OSError, ValueError, UnicodeError, subprocess.SubprocessError) as exc:
                    return rows, ['Cannot verify Windows backing storage: ' + str(exc)]
                rows.extend(result['volumes'])
                errors.extend(result['errors'])
                if len(result['volumes']) != len(fallback) and not result['errors']:
                    errors.append('A required Windows storage volume did not return a capacity reading.')
        return rows, errors

    @staticmethod
    def _host_identity(targets):
        return {(r['role'], r['id'].casefold(), _windows_path(r['path']).casefold()) for r in targets}

    def _verify_host_identity(self, targets):
        # A change to .wslconfig alone does not move a running VM's swap.
        # Keep monitoring the volume identities selected at the first check.
        identity = self._host_identity(targets)
        if self.host_identity is not None and identity != self.host_identity:
            return ['Windows backing or swap storage changed during monitoring.']
        self.host_identity = identity
        return []

    def _desktop_mount(self, row, mounts, path, device, total):
        # Only ordinary Windows drive roots can be independently confirmed
        # here. Nested volume mounts and different quota totals need Windows.
        root = _windows_path(row.get('volume_path', ''))
        if not re.fullmatch(r'[A-Za-z]:\\', root) or total != row['total_bytes']:
            return None
        root_path = _mapped_path(root, mounts)
        if root_path is None or not path.is_relative_to(root_path) or path.resolve(strict=True) != path:
            return None
        root_stat, path_stat = root_path.stat(), path.stat()
        if not path.is_dir() or root_stat.st_dev != device or path_stat.st_dev != device:
            return None
        return path, device, root_stat.st_ino, path_stat.st_ino

    def _sample_desktop_bindings(self, mounts, reported):
        if not self.desktop_bindings or time.monotonic() >= self.desktop_binding_expires:
            return []
        by_role = {row['role']: row for row in reported or []}
        rows = []
        for row, binding in self.desktop_bindings:
            report = by_role.get(row['role'])
            if report and (report['total_bytes'] != row['total_bytes'] or any(
                    _windows_path(report.get(key, '')).casefold() != _windows_path(row.get(key, '')).casefold()
                    for key in ('probe_path', 'volume_path'))):
                raise ValueError('Windows reported a different storage directory or volume.')
            path = _mapped_path(row['probe_path'], mounts)
            if path is None:
                raise ValueError('A verified Windows drive is no longer mounted.')
            device, available, total = self._reading(path)
            if self._desktop_mount(row, mounts, path, device, total) != binding:
                raise ValueError('The verified Windows drive mount or storage directory changed.')
            if report and report['available_bytes'] < available:
                raise ValueError('The Windows report shows a lower allowance than the mounted drive can verify.')
            rows.append({**row, 'available_bytes': available, 'observed_by': 'drvfs'})
        return rows if time.monotonic() < self.desktop_binding_expires else []

    def _host_volumes(self):
        if not self.distribution:
            return [], ['WSL distribution identity is missing; its Windows backing drive cannot be verified.'], None
        mounts = _drvfs_mounts()
        observed, desktop_errors, sampled = [], [], None
        directory = os.environ.get(STORAGE_SESSION_DIR)
        if directory:
            try:
                desktop = _desktop_observation(directory, self.distribution, backing_only=self.backing_only)
                sampled = desktop['time']
                desktop_errors = desktop['errors']
                mounted_read_started = time.time()
                bindings = []
                for row in desktop['volumes']:
                    row['observed_by'] = 'windows_desktop'
                    # Retain a low reading from either system. Some Windows storage
                    # volumes do not have a DrvFs mount; the host measures those too.
                    mapped = _mapped_path(row['probe_path'], mounts)
                    if mapped is not None:
                        try:
                            device, available, total = self._reading(mapped)
                            host_available = row['available_bytes']
                            row['available_bytes'] = min(row['available_bytes'], available)
                            if available <= host_available:
                                binding = self._desktop_mount(row, mounts, mapped, device, total)
                                if binding:
                                    bindings.append((row, binding))
                        except (OSError, ValueError, RuntimeError):
                            pass
                    observed.append(row)
                if not desktop_errors:
                    errors = self._verify_host_identity(observed)
                    self.desktop_bindings = bindings if not errors and len(bindings) == len(observed) else []
                    self.desktop_binding_expires = time.monotonic() + DESKTOP_BINDING_SECONDS - (time.time() - sampled)
                    if self.desktop_bindings:
                        # Every returned byte count was just measured on its
                        # matched mount. An aging desktop timestamp must not
                        # expire those independent readings between polls.
                        for row in observed:
                            row['observed_by'] = 'drvfs'
                        sampled = mounted_read_started
                    return observed, errors, sampled
                self.desktop_bindings.clear()
            except _DesktopReadingGap as exc:
                desktop_errors = [str(exc)]
                if exc.targets is not None and self._host_identity(exc.targets) != self.host_identity:
                    self.desktop_bindings.clear()
                    self.host_targets = None
                try:
                    measured = time.time()
                    direct = self._sample_desktop_bindings(mounts, exc.targets)
                    if direct:
                        return direct, [], measured
                except (OSError, ValueError, RuntimeError) as mapping_error:
                    self.desktop_bindings.clear()
                    self.host_targets = None
                    desktop_errors.append(str(mapping_error))
            except (OSError, ValueError, UnicodeError, RecursionError) as exc:
                self.desktop_bindings.clear()
                desktop_errors = [str(exc)]
        try:
            direct, errors = self._interop_volumes(mounts)
        except (OSError, ValueError, UnicodeError, subprocess.SubprocessError) as exc:
            direct, errors = [], ['Cannot verify Windows backing storage: ' + str(exc)]
        # A verified alternate covers a failed transport. Partial current
        # observations still count towards the minimum available capacity.
        if errors:
            errors = desktop_errors + errors
        else:
            errors = self._verify_host_identity(direct)
        return observed + direct, errors, sampled if observed else None

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
                             'role': 'linux_root' if self.backing_only else 'data',
                             'path': str(anchor), 'sampled_at': now(), 'available_bytes': available, 'total_bytes': total})
            except (OSError, ValueError, RuntimeError) as exc:
                errors.append(label + ': ' + str(exc))
        host_time = None
        if self.is_wsl:
            host_rows, host_errors, host_time = self._host_volumes()
            for row in host_rows:
                row['sampled_at'] = datetime.fromtimestamp(host_time, timezone.utc).isoformat() if host_time is not None else now()
            rows.extend(host_rows)
            errors.extend(host_errors)
        volumes = {}
        for row in rows:
            key = row['id']
            if key not in volumes:
                volumes[key] = dict(row, labels=[row['label']], paths=[row['path']], roles=[row.get('role', 'data')])
            else:
                old = volumes[key]
                old['sampled_at'] = min(old['sampled_at'], row['sampled_at'])
                old['available_bytes'] = min(old['available_bytes'], row['available_bytes'])
                role = row.get('role', 'data')
                if role not in old['roles']:
                    old['roles'].append(role)
                if row['label'] not in old['labels']:
                    old['labels'].append(row['label'])
                if row['path'] not in old['paths']:
                    old['paths'].append(row['path'])
        result = {'volumes': list(volumes.values()), 'errors': list(dict.fromkeys(errors)), 'checked_at': now()}
        if host_time is not None:
            result['windows_sampled_at'] = host_time
            result['checked_at'] = datetime.fromtimestamp(host_time, timezone.utc).isoformat()
        return result


class ProtectedCapacityProbe(CapacityProbe):
    """Guard Linux root and the registered WSL distribution's backing volume."""

    backing_only = True
