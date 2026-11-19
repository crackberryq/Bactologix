"""Best-effort Linux TCP byte deltas for sockets held by the observed job."""
import errno
import os
import socket
import struct
import sys
import time

try:
    from psutil import Error as ProcessError
except ImportError:
    ProcessError = OSError

# Bound observer work on large multi-user hosts. A truncated observation cannot
# establish that a job is idle.
SAMPLE_BUDGET = 0.35
MAX_FDS = 8192
MAX_PROCESSES = 512
MAX_DIAG_BYTES = 2 * 1024 * 1024

# Linux UAPI: netlink.h, inet_diag.h, tcp.h. All counters are native endian.
NL_HEADER = struct.Struct('=IHHII')
SOCK_DIAG_BY_FAMILY = 20
NETLINK_SOCK_DIAG = 4
INET_DIAG_INFO = 2
DIAG_MSG_SIZE = 72
TCP_BYTES_RECEIVED = 128
TCP_BYTES_SENT = 200


def _owned_inodes(processes, deadline):
    local_namespace = os.stat('/proc/self/ns/net').st_ino
    inodes = set()
    partial = False
    visible = inspected = 0
    for index, process in enumerate(processes):
        if index >= MAX_PROCESSES or inspected >= MAX_FDS or time.monotonic() >= deadline:
            partial = True
            break
        base = f'/proc/{process.pid}'
        owned = set()
        try:
            if os.stat(base + '/ns/net').st_ino != local_namespace:
                partial = True
                continue
            with os.scandir(base + '/fd') as entries:
                for entry in entries:
                    if inspected >= MAX_FDS or time.monotonic() >= deadline:
                        partial = True
                        break
                    inspected += 1
                    try:
                        link = os.readlink(entry.path)
                    except FileNotFoundError:
                        continue  # Closed between readdir and readlink.
                    if link.startswith('socket:['):
                        owned.add(int(link[8:-1]))
            # psutil checks both PID and start time. Discard descriptors if the PID
            # was recycled or the process exited during our walk.
            if process.is_running():
                inodes.update(owned)
                visible += 1
            else:
                partial = True
        except (OSError, ProcessError):
            partial = True
    return (inodes if visible else None), partial


def _socket_counters(row, inodes, counters):
    """Decode only records that match an owned inode; use kernel cookies for identity."""
    if len(row) < DIAG_MSG_SIZE:
        raise OSError('Incomplete socket diagnostic')
    inode, = struct.unpack_from('=I', row, 68)
    if inode not in inodes:
        return
    cookie, = struct.unpack_from('=Q', row, 44)
    offset = DIAG_MSG_SIZE
    while offset + 4 <= len(row):
        length, kind = struct.unpack_from('=HH', row, offset)
        if length < 4 or offset + length > len(row):
            raise OSError('Incomplete socket attribute')
        if kind == INET_DIAG_INFO and length >= 4 + TCP_BYTES_SENT + 8:
            start = offset + 4
            rx, = struct.unpack_from('=Q', row, start + TCP_BYTES_RECEIVED)
            tx, = struct.unpack_from('=Q', row, start + TCP_BYTES_SENT)
            counters[(inode, cookie)] = (rx, tx)
            return
        offset += (length + 3) & ~3
    raise OSError('TCP byte counters unavailable')


def _parse_datagram(data, family, inodes, counters):
    offset = 0
    while offset < len(data):
        if len(data) - offset < NL_HEADER.size:
            raise OSError('Truncated socket header')
        length, kind, flags, seq, _ = NL_HEADER.unpack_from(data, offset)
        if length < NL_HEADER.size or offset + length > len(data) or seq != family or flags & 0x10:
            raise OSError('Interrupted socket dump')
        body = memoryview(data)[offset + NL_HEADER.size:offset + length]
        if kind in (2, 3):  # NLMSG_ERROR / NLMSG_DONE
            error = struct.unpack_from('=i', body)[0] if len(body) >= 4 else 0
            unsupported = family == socket.AF_INET6 and -error in {errno.ENOENT, errno.EAFNOSUPPORT, errno.EPROTONOSUPPORT}
            if error and not unsupported:
                raise OSError(-error, os.strerror(-error))
            return True
        if kind == SOCK_DIAG_BY_FAMILY:
            _socket_counters(body, inodes, counters)
        elif kind == 4:  # NLMSG_OVERRUN
            raise OSError('Socket diagnostic overrun')
        offset += (length + 3) & ~3
    return False


def _tcp_counters(inodes, deadline):
    counters = {}
    remaining_bytes = MAX_DIAG_BYTES
    with socket.socket(socket.AF_NETLINK, socket.SOCK_RAW, NETLINK_SOCK_DIAG) as diag:
        for family in (socket.AF_INET, socket.AF_INET6):
            # Exclude TIME_WAIT, CLOSED and LISTEN; only connected TCP descriptors
            # can carry a job's data. No address from the reply is retained.
            states = 0xffe & ~((1 << 6) | (1 << 7) | (1 << 10))
            request = struct.pack('=BBBBI48x', family, socket.IPPROTO_TCP, 1 << (INET_DIAG_INFO - 1), 0, states)
            header = NL_HEADER.pack(NL_HEADER.size + len(request), SOCK_DIAG_BY_FAMILY, 0x301, family, 0)
            diag.sendto(header + request, (0, 0))
            while True:
                remaining_time = deadline - time.monotonic()
                if remaining_time <= 0 or remaining_bytes <= 0:
                    raise TimeoutError('Socket diagnostic budget exceeded')
                diag.settimeout(remaining_time)
                data, _, flags, sender = diag.recvmsg(min(65536, remaining_bytes))
                if flags & socket.MSG_TRUNC or sender[0] != 0 or not data:
                    raise OSError('Incomplete kernel socket response')
                remaining_bytes -= len(data)
                if _parse_datagram(data, family, inodes, counters):
                    break
    return counters


class NetworkSampler:
    def __init__(self):
        self.previous = None

    def sample(self, processes):
        if sys.platform != 'linux' or not hasattr(socket, 'AF_NETLINK'):
            return None
        deadline = time.monotonic() + SAMPLE_BUDGET
        try:
            inodes, partial = _owned_inodes(processes, deadline)
            if inodes is None:
                self.previous = None
                return None
            current = _tcp_counters(inodes, deadline) if inodes else {}
        except (OSError, ProcessError):
            self.previous = None
            return None
        previous, self.previous = self.previous, current
        if previous is None:
            return None
        # Reopened connections, inherited descriptors and transfers between samples
        # cannot safely contribute a lifetime count to the current interval.
        partial |= bool(previous.keys() - current.keys())
        rx = tx = 0
        for identity, (received, sent) in current.items():
            before = previous.get(identity)
            if before is None or received < before[0] or sent < before[1]:
                partial |= bool(received or sent or before is not None)
                continue
            rx += received - before[0]
            tx += sent - before[1]
        return {'rx_bytes': rx, 'tx_bytes': tx, 'partial': partial}
