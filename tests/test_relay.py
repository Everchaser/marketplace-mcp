import importlib
import socket
import subprocess
import sys
import threading
import time

import pytest


def relay(nic="lo"):
    return importlib.import_module("marketplace_mcp.relay").YandexRelay(nic)


def test_relay_binds_loopback_ephemeral_and_rejects_non_yandex_connect():
    with relay() as r:
        assert r.address[0] == "127.0.0.1" and r.address[1] > 0
        for request in [
            b"CONNECT example.com:443 HTTP/1.1\r\n\r\n",
            b"GET http://market.yandex.ru/ HTTP/1.1\r\n\r\n",
            b"CONNECT market.yandex.ru:80 HTTP/1.1\r\n\r\n",
        ]:
            with socket.create_connection(r.address, timeout=2) as s:
                s.sendall(request)
                assert b"403" in s.recv(1024)
        assert r.proxy_url == f"http://127.0.0.1:{r.address[1]}"
    assert not r.thread.is_alive()


def test_relay_invalid_interface_fails_closed():
    with pytest.raises(OSError):
        relay("missing-mcp-nic").start()


def test_socket_binding_happens_before_connect_and_never_falls_back(monkeypatch):
    module = importlib.import_module("marketplace_mcp.relay")
    calls = []

    class FakeSocket:
        def settimeout(self, t):
            calls.append("timeout")

        def setsockopt(self, level, opt, value):
            assert opt == socket.SO_BINDTODEVICE and value == b"lo\0"
            calls.append("bind")

        def connect(self, address):
            assert calls[-1] == "bind"
            calls.append("connect")

        def close(self):
            calls.append("close")

    monkeypatch.setattr(
        module.socket,
        "getaddrinfo",
        lambda *a, **k: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("1.2.3.4", 443))],
    )
    monkeypatch.setattr(module.socket, "socket", lambda *a, **k: FakeSocket())
    module.connect_bound("lo")
    assert calls == ["timeout", "bind", "connect"]
    calls.clear()

    class Bad(FakeSocket):
        def setsockopt(self, *args):
            calls.append("bad-bind")
            raise OSError("no permission")

    monkeypatch.setattr(module.socket, "socket", lambda *a, **k: Bad())
    with pytest.raises(OSError):
        module.connect_bound("lo")
    assert "connect" not in calls and "close" in calls


def test_allowed_connect_relays_bytes_and_closes_threads(monkeypatch):
    module = importlib.import_module("marketplace_mcp.relay")
    remote, peer = socket.socketpair()
    monkeypatch.setattr(module, "connect_bound", lambda nic, **kwargs: remote)
    with relay() as r:
        with socket.create_connection(r.address, timeout=2) as client:
            client.sendall(b"CONNECT market.yandex.ru:443 HTTP/1.1\r\nHost: market.yandex.ru:443\r\n\r\n")
            assert b"200" in client.recv(1024)
            client.sendall(b"hello")
            peer.settimeout(2)
            assert peer.recv(5) == b"hello"
            peer.sendall(b"reply")
            assert client.recv(5) == b"reply"
        peer.close()
    assert not r.thread.is_alive()


def test_relay_closes_partial_header_connections_on_shutdown():
    r = relay().start()
    s = socket.create_connection(r.address, timeout=2)
    s.sendall(b"CONNECT market.yandex.ru:443 HTTP/1.1\r\n")
    # Wait for the server to register the connection, not a timing assumption.
    deadline = time.monotonic() + 1
    while time.monotonic() < deadline:
        with r.server.lock:
            active = bool(r.server.active)
        if active:
            break
        time.sleep(0.001)
    assert active
    start = time.monotonic()
    r.close()
    assert time.monotonic() - start < 1
    assert not r.thread.is_alive()
    s.close()


def test_relay_header_size_is_bounded():
    with relay() as r, socket.create_connection(r.address, timeout=2) as s:
        s.sendall(b"CONNECT market.yandex.ru:443 HTTP/1.1\r\nX: " + b"x" * 16384 + b"\r\n\r\n")
        assert b"431" in s.recv(1024)


def test_relay_partial_header_has_absolute_deadline(monkeypatch):
    module = importlib.import_module("marketplace_mcp.relay")
    monkeypatch.setattr(module, "HEADER_TIMEOUT", 0.03)
    with relay() as r, socket.create_connection(r.address, timeout=1) as s:
        s.sendall(b"C")
        assert b"408" in s.recv(1024)


def test_network_address_failure_tries_next_address_still_bound(monkeypatch):
    module = importlib.import_module("marketplace_mcp.relay")
    sockets = []

    class FakeSocket:
        def __init__(self, *args):
            self.bound = False
            sockets.append(self)

        def settimeout(self, value):
            assert 0 < value <= 20

        def setsockopt(self, *args):
            self.bound = True

        def connect(self, address):
            assert self.bound
            if len(sockets) == 1:
                raise OSError(101, "Network unreachable")

        def close(self):
            pass

    monkeypatch.setattr(
        module.socket,
        "getaddrinfo",
        lambda *a, **k: [
            (socket.AF_INET6, socket.SOCK_STREAM, 6, "", ("::1", 443, 0, 0)),
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("1.2.3.4", 443)),
        ],
    )
    monkeypatch.setattr(module.socket, "socket", FakeSocket)
    result = module.connect_bound("lo")
    assert result is sockets[1] and all(s.bound for s in sockets)


def test_relay_shutdown_does_not_wait_for_stalled_dns(monkeypatch):
    module = importlib.import_module("marketplace_mcp.relay")
    entered = threading.Event()
    release = threading.Event()
    exited = threading.Event()

    def stalled_dns(host, port, **kwargs):
        assert (host, port) == ("market.yandex.ru", 443)
        entered.set()
        try:
            release.wait()
            return []
        finally:
            exited.set()

    monkeypatch.setattr(module.socket, "getaddrinfo", stalled_dns)
    # Use a literal address socket, since create_connection also resolves locally.
    r = relay().start()
    client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    closer = threading.Thread(target=r.close, daemon=True)
    try:
        client.connect(r.address)
        client.sendall(b"CONNECT market.yandex.ru:443 HTTP/1.1\r\n\r\n")
        assert entered.wait(1)
        closer.start()
        closer.join(timeout=0.5)
        assert not closer.is_alive(), "close() waited for an unbounded host DNS call"
        assert not exited.is_set(), "DNS fixture must remain blocked during shutdown"
        assert not r.thread.is_alive()
    finally:
        release.set()
        assert exited.wait(1)
        closer.join(timeout=2)
        client.close()
        if r.server is not None:
            r.close()


def test_dns_wait_is_included_in_connection_deadline(monkeypatch):
    module = importlib.import_module("marketplace_mcp.relay")
    monkeypatch.setattr(module, "CONNECT_TIMEOUT", 0.03, raising=False)
    entered = threading.Event()
    release = threading.Event()
    finished = threading.Event()
    errors = []

    def stalled_dns(*args, **kwargs):
        entered.set()
        release.wait()
        return []

    def connect():
        try:
            module.connect_bound("lo")
        except OSError as exc:
            errors.append(exc)
        finally:
            finished.set()

    monkeypatch.setattr(module.socket, "getaddrinfo", stalled_dns)
    caller = threading.Thread(target=connect, daemon=True)
    caller.start()
    try:
        assert entered.wait(1)
        assert finished.wait(0.3), "connection deadline did not bound stalled DNS"
        assert not release.is_set()
        assert len(errors) == 1 and isinstance(errors[0], TimeoutError)
    finally:
        release.set()
        caller.join(timeout=1)


def test_timed_out_resolvers_keep_process_wide_capacity_until_dns_returns(monkeypatch):
    module = importlib.import_module("marketplace_mcp.relay")
    monkeypatch.setattr(module, "CONNECT_TIMEOUT", 0.03)
    release = threading.Event()
    workers = []
    lock = threading.Lock()

    def stalled_dns(host, port, **kwargs):
        assert (host, port) == ("market.yandex.ru", 443)
        with lock:
            workers.append(threading.current_thread())
        release.wait()
        return []

    monkeypatch.setattr(module.socket, "getaddrinfo", stalled_dns)
    try:
        for _ in range(8):
            with pytest.raises(TimeoutError):
                module.connect_bound("lo")
        for _ in range(10):
            with pytest.raises(OSError, match="resolver capacity"):
                module.connect_bound("lo")
        assert len(workers) == 8
        assert all(worker.daemon and worker.is_alive() for worker in workers)
        # New relay instances share the cap: restarting cannot leak more workers.
        with relay() as r, socket.socket(socket.AF_INET, socket.SOCK_STREAM) as client:
            client.settimeout(1)
            client.connect(r.address)
            client.sendall(b"CONNECT market.yandex.ru:443 HTTP/1.1\r\n\r\n")
            assert b"502" in client.recv(1024)
        assert len(workers) == 8
    finally:
        release.set()
        for worker in workers:
            worker.join(timeout=1)
            assert not worker.is_alive()

    # Completed DNS calls release capacity; future requests can resolve normally.
    monkeypatch.setattr(module.socket, "getaddrinfo", lambda *args, **kwargs: [])
    with pytest.raises(OSError, match="No Yandex address"):
        module.connect_bound("lo")


def test_already_stopping_relay_does_not_start_dns(monkeypatch):
    module = importlib.import_module("marketplace_mcp.relay")
    entered = threading.Event()
    stopping = threading.Event()
    stopping.set()

    def dns(*args, **kwargs):
        entered.set()
        return []

    monkeypatch.setattr(module.socket, "getaddrinfo", dns)
    with pytest.raises(OSError, match="stopping"):
        module.connect_bound("lo", stopping=stopping)
    assert not entered.wait(0.1), "shutdown must not create new DNS work"


def test_stalled_dns_does_not_prevent_process_exit():
    code = """
import socket
import threading
from marketplace_mcp.relay import YandexRelay

entered = threading.Event()
never = threading.Event()
def stalled_dns(*args, **kwargs):
    entered.set()
    never.wait()
socket.getaddrinfo = stalled_dns
r = YandexRelay('lo').start()
client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
client.connect(r.address)
client.sendall(b'CONNECT market.yandex.ru:443 HTTP/1.1\\r\\n\\r\\n')
assert entered.wait(1)
r.close()
client.close()
assert not r.thread.is_alive()
workers = [t for t in threading.enumerate() if t.name == 'yandex-dns']
assert len(workers) == 1 and workers[0].daemon and workers[0].is_alive()
"""
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, timeout=3, check=False
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == result.stderr == ""


def test_connect_handlers_and_stalled_resolvers_are_both_bounded_at_eight(monkeypatch):
    module = importlib.import_module("marketplace_mcp.relay")
    release = threading.Event()
    entered = threading.Event()
    workers = []
    lock = threading.Lock()

    def stalled_dns(*args, **kwargs):
        with lock:
            workers.append(threading.current_thread())
            if len(workers) == 8:
                entered.set()
        release.wait()
        return []

    monkeypatch.setattr(module.socket, "getaddrinfo", stalled_dns)
    clients = []
    r = relay().start()
    server = r.server
    try:
        for _ in range(8):
            client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            clients.append(client)
            client.settimeout(1)
            client.connect(r.address)
            client.sendall(b"CONNECT market.yandex.ru:443 HTTP/1.1\r\n\r\n")
        assert entered.wait(1)
        with server.lock:
            assert len(server.active) == 8
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as extra:
            extra.settimeout(1)
            extra.connect(r.address)
            assert extra.recv(1) == b""
        assert len(workers) == 8
        start = time.monotonic()
        r.close()
        assert time.monotonic() - start < 0.5
        assert all(not thread.is_alive() for thread in server._threads)
        assert all(worker.is_alive() and worker.daemon for worker in workers)
    finally:
        release.set()
        for worker in workers:
            worker.join(timeout=1)
        for client in clients:
            client.close()
        if r.server is not None:
            r.close()
