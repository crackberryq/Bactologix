"""Linux process-owned TCP traffic and unavailable observation contracts."""
from contextlib import ExitStack
import errno
import os
import socket
import struct
import subprocess
import sys
import time

import psutil
import pytest

from bactologix import network_usage as net


def test_delta_baselines_reconnect_and_read_gap(monkeypatch):
    sampler = net.NetworkSampler()
    monkeypatch.setattr(net, '_owned_inodes', lambda *_: ({100}, False))
    measurements = iter([
        {(100, 1): (1000, 500)}, {(100, 1): (1010, 520)},
        {(100, 2): (4000, 1000)}, {(100, 2): (4020, 1004)},
        PermissionError(), {(100, 2): (5000, 1200)}, {(100, 2): (5000, 1200)},
    ])

    def snapshot(*_):
        result = next(measurements)
        if isinstance(result, OSError):
            raise result
        return result

    monkeypatch.setattr(net, '_tcp_counters', snapshot)
    if sys.platform != 'linux':
        pytest.skip('Linux-only sample')
    assert sampler.sample([]) is None
    assert sampler.sample([]) == dict(rx_bytes=10, tx_bytes=20, partial=False)
    assert sampler.sample([]) == dict(rx_bytes=0, tx_bytes=0, partial=True)
    assert sampler.sample([]) == dict(rx_bytes=20, tx_bytes=4, partial=False)
    assert sampler.sample([]) is None
    assert sampler.sample([]) is None
    assert sampler.sample([]) == dict(rx_bytes=0, tx_bytes=0, partial=False)


def _connected_pair(stack):
    with socket.socket() as listener:
        listener.bind(('127.0.0.1', 0))
        listener.listen()
        client = stack.enter_context(socket.create_connection(listener.getsockname(), timeout=3))
        server = stack.enter_context(listener.accept()[0])
    client.settimeout(3)
    server.settimeout(3)
    return client, server


@pytest.mark.skipif(sys.platform != 'linux', reason='Linux TCP_INFO')
@pytest.mark.parametrize('native_diag', [False, True], ids=['real-tcp-info', 'kernel-socket-dump'])
def test_counts_only_the_target_child_tcp_traffic(monkeypatch, native_diag):
    # A duplicate fd in the test process lets restricted CI read the same kernel
    # TCP_INFO as inet_diag even when its seccomp profile forbids NETLINK_SOCK_DIAG.
    with ExitStack() as stack:
        sender, receiver = _connected_pair(stack)
        other_sender, other_receiver = _connected_pair(stack)
        sockets = (sender, receiver, other_sender, other_receiver)
        child = stack.enter_context(subprocess.Popen(
            [sys.executable, '-c', '''
import os, socket, sys
with socket.socket(fileno=int(sys.argv[1])) as stream:
    print("ready", flush=True)
    sys.stdin.readline()
    assert stream.recv(32768, socket.MSG_WAITALL) == b"a" * 32768
    stream.sendall(b"b" * 4096)
    print("sent", flush=True)
    sys.stdin.readline()
''', str(receiver.fileno())], pass_fds=(receiver.fileno(),),
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True))
        stack.callback(child.kill)
        assert child.stdout.readline().strip() == 'ready'
        process = psutil.Process(child.pid)
        sampler = net.NetworkSampler()
        if native_diag:
            try:
                net._tcp_counters({os.fstat(receiver.fileno()).st_ino}, time.monotonic() + 2)
            except OSError as exc:
                if exc.errno in {errno.EPERM, errno.EACCES, errno.EPROTONOSUPPORT, errno.EAFNOSUPPORT}:
                    pytest.skip('Kernel socket diagnostics unavailable here')
                raise
        else:
            def records(inodes, deadline):
                counters = {}
                for stream in sockets:
                    inode = os.fstat(stream.fileno()).st_ino
                    info = stream.getsockopt(socket.IPPROTO_TCP, socket.TCP_INFO, 256)
                    msg = bytearray(net.DIAG_MSG_SIZE)
                    struct.pack_into('=I', msg, 68, inode)
                    struct.pack_into('=Q', msg, 44, stream.fileno())
                    attr = struct.pack('=HH', len(info) + 4, net.INET_DIAG_INFO) + info
                    net._socket_counters(msg + attr, inodes, counters)
                return counters
            monkeypatch.setattr(net, '_tcp_counters', records)
        assert sampler.sample([process]) is None
        assert sampler.sample([process]) == dict(rx_bytes=0, tx_bytes=0, partial=False)
        other_sender.sendall(b'x' * 8000)
        assert len(other_receiver.recv(8000, socket.MSG_WAITALL)) == 8000
        sender.sendall(b'a' * 32768)
        child.stdin.write('go\n')
        child.stdin.flush()
        assert child.stdout.readline().strip() == 'sent'
        assert len(sender.recv(4096, socket.MSG_WAITALL)) == 4096
        assert sampler.sample([process, process]) == dict(rx_bytes=32768, tx_bytes=4096, partial=False)


def test_process_limit_or_lost_identity_is_incomplete(monkeypatch):
    if sys.platform != 'linux':
        pytest.skip('Linux procfs')
    class Exited:
        pid = os.getpid()
        def is_running(self):
            return False

    process = psutil.Process()
    inodes, partial = net._owned_inodes([Exited()], time.monotonic() + 3)
    assert inodes is None and partial
    monkeypatch.setattr(net, 'MAX_PROCESSES', 1)
    _, partial = net._owned_inodes([process, process], time.monotonic() + 3)
    assert partial


def test_unsupported_platform_is_unavailable(monkeypatch):
    monkeypatch.setattr(sys, 'platform', 'darwin')
    assert net.NetworkSampler().sample([]) is None


def test_netlink_multipart_and_incomplete_stream():
    family = socket.AF_INET

    def message(kind, payload, flags=0):
        size = net.NL_HEADER.size + len(payload)
        return net.NL_HEADER.pack(size, kind, flags, family, 0) + payload

    def socket_message(inode):
        body = bytearray(net.DIAG_MSG_SIZE)
        struct.pack_into('=Q', body, 44, 9001)
        struct.pack_into('=I', body, 68, inode)
        info = bytearray(net.TCP_BYTES_SENT + 8)
        struct.pack_into('=Q', info, net.TCP_BYTES_RECEIVED, 234)
        struct.pack_into('=Q', info, net.TCP_BYTES_SENT, 45)
        attr = struct.pack('=HH', 4 + len(info), net.INET_DIAG_INFO) + info
        return message(net.SOCK_DIAG_BY_FAMILY, body + attr)

    counters = {}
    assert not net._parse_datagram(socket_message(78) + socket_message(10), family, {78}, counters)
    assert counters == {(78, 9001): (234, 45)}
    assert net._parse_datagram(message(3, bytes(4)), family, {78}, counters)
    with pytest.raises(OSError):
        net._parse_datagram(message(3, bytes(4), flags=0x10), family, {78}, counters)
    with pytest.raises(OSError):
        net._parse_datagram(socket_message(78)[:-10], family, {78}, counters)
    with pytest.raises(PermissionError):
        net._parse_datagram(message(2, struct.pack('=i', -errno.EPERM)), family, {78}, counters)
