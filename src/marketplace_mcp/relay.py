"""Loopback-only, Yandex-only CONNECT relay for Linux interface binding."""

import select
import socket
import socketserver
import threading
import time

HEADER_TIMEOUT = 5.0
CONNECT_TIMEOUT = 20.0
# Process-wide, not per relay: timed-out/cancelled host DNS calls retain their
# permit until they actually return, including across relay restarts.
_RESOLVER_SLOTS = threading.BoundedSemaphore(8)


def _resolve_yandex(deadline, stopping):
    if stopping is not None and stopping.is_set():
        raise OSError("Yandex relay stopping")
    if not _RESOLVER_SLOTS.acquire(blocking=False):
        raise OSError("Yandex resolver capacity exhausted")
    done = threading.Event()
    result = []
    errors = []

    def resolve():
        try:
            result.extend(socket.getaddrinfo("market.yandex.ru", 443, type=socket.SOCK_STREAM))
        except OSError as exc:
            errors.append(exc)
        finally:
            _RESOLVER_SLOTS.release()
            done.set()

    # libc DNS cannot be interrupted safely. Only bounded daemon workers may
    # remain blocked; request handlers poll cancellation/deadline and can exit.
    try:
        threading.Thread(target=resolve, name="yandex-dns", daemon=True).start()
    except BaseException:
        _RESOLVER_SLOTS.release()
        raise
    while True:
        if stopping is not None and stopping.is_set():
            raise OSError("Yandex relay stopping")
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("Yandex connection deadline")
        if done.is_set():
            if errors:
                raise errors[0]
            return result
        done.wait(min(0.05, remaining))


def connect_bound(nic, *, stopping=None):
    # DNS stays with the host resolver. Each outbound socket is bound BEFORE connect.
    deadline = time.monotonic() + CONNECT_TIMEOUT
    last_error = None
    for family, kind, proto, _, address in _resolve_yandex(deadline, stopping):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("Yandex connection deadline")
        upstream = socket.socket(family, kind, proto)
        upstream.settimeout(remaining)
        try:
            upstream.setsockopt(socket.SOL_SOCKET, socket.SO_BINDTODEVICE, nic.encode() + b"\0")
        except OSError:
            upstream.close()
            # Never retry a failed NIC bind using an unbound socket or another route.
            raise
        try:
            upstream.connect(address)
            return upstream
        except OSError as exc:
            upstream.close()
            last_error = exc
    raise last_error or OSError("No Yandex address resolved")


class _Handler(socketserver.BaseRequestHandler):
    def handle(self):
        remote = None
        try:
            header_deadline = time.monotonic() + HEADER_TIMEOUT
            head = bytearray()
            while not head.endswith(b"\r\n\r\n"):
                remaining = header_deadline - time.monotonic()
                if remaining <= 0:
                    self.request.sendall(b"HTTP/1.1 408 Request Timeout\r\n\r\n")
                    return
                self.request.settimeout(remaining)
                try:
                    chunk = self.request.recv(1)
                except TimeoutError:
                    self.request.sendall(b"HTTP/1.1 408 Request Timeout\r\n\r\n")
                    return
                if not chunk:
                    return
                head.extend(chunk)
                if len(head) > 16384:
                    self.request.sendall(b"HTTP/1.1 431 Request Header Fields Too Large\r\n\r\n")
                    return
            line = bytes(head).split(b"\r\n", 1)[0]
            if line not in {
                b"CONNECT market.yandex.ru:443 HTTP/1.1",
                b"CONNECT market.yandex.ru:443 HTTP/1.0",
            }:
                self.request.sendall(b"HTTP/1.1 403 Forbidden\r\nContent-Length: 0\r\n\r\n")
                return
            remote = connect_bound(self.server.nic, stopping=self.server.stopping)
            with self.server.lock:
                self.server.active.add(remote)
            self.request.sendall(b"HTTP/1.1 200 Connection Established\r\n\r\n")
            self.request.settimeout(20)
            remote.settimeout(20)
            deadline = time.monotonic() + 60
            while time.monotonic() < deadline and not self.server.stopping.is_set():
                ready, _, _ = select.select([self.request, remote], [], [], 0.25)
                for source in ready:
                    data = source.recv(65536)
                    if not data:
                        return
                    (remote if source is self.request else self.request).sendall(data)
        except OSError:
            if remote is None:
                try:
                    self.request.sendall(b"HTTP/1.1 502 Bad Gateway\r\nContent-Length: 0\r\n\r\n")
                except OSError:
                    pass
        finally:
            if remote is not None:
                with self.server.lock:
                    self.server.active.discard(remote)
                remote.close()


class _Server(socketserver.ThreadingMixIn, socketserver.TCPServer):
    daemon_threads = False
    block_on_close = True

    def __init__(self, address, nic):
        self.nic = nic
        self.slots = threading.BoundedSemaphore(8)
        self.active = set()
        self.lock = threading.Lock()
        self.stopping = threading.Event()
        super().__init__(address, _Handler)

    def process_request(self, request, address):
        if not self.slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        with self.lock:
            self.active.add(request)
        super().process_request(request, address)

    def process_request_thread(self, request, address):
        try:
            super().process_request_thread(request, address)
        finally:
            with self.lock:
                self.active.discard(request)
            self.slots.release()


class YandexRelay:
    def __init__(self, nic):
        self.nic = nic
        self.server = None
        self.thread = None

    def start(self):
        socket.if_nametoindex(self.nic)
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_BINDTODEVICE, self.nic.encode() + b"\0")
        self.server = _Server(("127.0.0.1", 0), self.nic)
        self.address = self.server.server_address
        self.proxy_url = f"http://127.0.0.1:{self.address[1]}"
        self.thread = threading.Thread(
            target=lambda: self.server.serve_forever(poll_interval=0.05), name="yandex-relay"
        )
        self.thread.start()
        return self

    def close(self):
        if self.server:
            self.server.stopping.set()
            self.server.shutdown()
            with self.server.lock:
                for active in self.server.active:
                    try:
                        active.shutdown(socket.SHUT_RDWR)
                    except OSError:
                        pass
                    active.close()
            self.server.server_close()
            self.thread.join(timeout=2)
            self.server = None

    def __enter__(self):
        return self.start()

    def __exit__(self, *args):
        self.close()
