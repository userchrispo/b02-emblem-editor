"""The MITM proxy the PS5 points its network proxy setting at.

HTTPS (CONNECT) traffic is tunneled raw and never decrypted, so PSN sign-in
and normal console functionality keep working untouched. Plain HTTP requests
to the Demonware emblem-storage endpoint are the only thing inspected: in
CAPTURE mode the real response is saved, in Show mode it's replaced with
whichever emblem is currently selected (see broadcast.py).
"""
import os
import re
import socket
import threading
import time

from . import config
from .activity import record_event
from .broadcast import read_selected_data
from .state import read_state
from .storage import get_or_create_capture_group, write_slot_bytes

# matches ".../u51b08745d269.slot_504?..." -> userhash, slot number
PATH_RE = re.compile(r"/(u[0-9a-fA-F]+)\.slot_(\d+)")


def log(msg):
    print(f"{time.strftime('%H:%M:%S')} {msg}", flush=True)


def is_target_host(host):
    """Exact case-insensitive host match against the configured emblem server."""
    return (host or "").strip().lower() == config.TARGET_HOST_SUBSTR.strip().lower()


def is_client_allowed(client_ip):
    """Return True if client_ip is allowed by config.PROXY_ALLOW_IPS (or allowlist is unset)."""
    allow = config.PROXY_ALLOW_IPS
    if not allow:
        return True
    return client_ip in allow


def _parse_port(raw, default):
    if not raw:
        return default
    port = int(raw)
    if not (1 <= port <= 65535):
        raise ValueError(f"invalid port: {port}")
    return port


def _recv_headers(sock, max_bytes=None):
    """Read from sock until CRLFCRLF or EOF, enforcing a maximum header size."""
    limit = max_bytes or config.PROXY_MAX_HEADER_BYTES
    data = b""
    while b"\r\n\r\n" not in data:
        chunk = sock.recv(4096)
        if not chunk:
            break
        data += chunk
        if len(data) > limit:
            raise ValueError("HTTP headers exceed maximum allowed size")
    return data


def _read_chunked_body(sock, initial, max_bytes):
    """Read an HTTP/1.1 Transfer-Encoding: chunked body and return raw decoded bytes."""
    buf = bytearray(initial)
    out = bytearray()

    def ensure(n):
        while len(buf) < n:
            chunk = sock.recv(4096)
            if not chunk:
                return False
            buf.extend(chunk)
        return True

    while True:
        while b"\r\n" not in buf:
            chunk = sock.recv(4096)
            if not chunk:
                return bytes(out)
            buf.extend(chunk)
            if len(buf) > max_bytes:
                raise ValueError("chunked response exceeds maximum body size")
        line_end = buf.find(b"\r\n")
        size_token = bytes(buf[:line_end]).split(b";", 1)[0].strip()
        del buf[:line_end + 2]
        chunk_size = int(size_token, 16)
        if chunk_size == 0:
            break
        if len(out) + chunk_size > max_bytes:
            raise ValueError("chunked response exceeds maximum body size")
        if not ensure(chunk_size + 2):
            out.extend(buf[:chunk_size])
            break
        out.extend(buf[:chunk_size])
        del buf[:chunk_size + 2]
    return bytes(out)


def pipe(src, dst):
    try:
        while True:
            data = src.recv(65536)
            if not data:
                break
            dst.sendall(data)
    except OSError:
        pass
    finally:
        for s in (src, dst):
            try:
                s.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass


def recv_full_response(sock):
    data = _recv_headers(sock)
    header_end = data.find(b"\r\n\r\n")
    if header_end == -1:
        return data, b""
    header_end += 4
    headers = data[:header_end]
    body = data[header_end:]

    cl = None
    is_chunked = False
    for line in headers.split(b"\r\n")[1:]:
        low = line.lower()
        if low.startswith(b"content-length:"):
            try:
                cl = int(line.split(b":", 1)[1].strip())
            except ValueError as exc:
                raise ValueError("invalid Content-Length header") from exc
            if cl < 0 or cl > config.PROXY_MAX_BODY_BYTES:
                raise ValueError(f"Content-Length out of range: {cl}")
        elif low.startswith(b"transfer-encoding:") and b"chunked" in low:
            is_chunked = True

    if is_chunked:
        decoded = _read_chunked_body(sock, body, config.PROXY_MAX_BODY_BYTES)
        # Rewrite response headers to Content-Length so saved/forwarded blobs
        # have a deterministic length and can be replayed directly in Show mode.
        status_line = headers.split(b"\r\n", 1)[0]
        filtered = [status_line]
        for line in headers.split(b"\r\n")[1:]:
            if not line:
                continue
            low = line.lower()
            if low.startswith(b"transfer-encoding:") or low.startswith(b"content-length:"):
                continue
            filtered.append(line)
        filtered.append(f"Content-Length: {len(decoded)}".encode("ascii"))
        headers = b"\r\n".join(filtered) + b"\r\n\r\n"
        return headers, decoded

    if cl is not None:
        while len(body) < cl:
            chunk = sock.recv(4096)
            if not chunk:
                break
            body += chunk
    return headers, body


def fetch_real(req, host, port):
    upstream = socket.create_connection((host, port), timeout=config.PROXY_SOCKET_TIMEOUT)
    try:
        upstream.settimeout(config.PROXY_SOCKET_TIMEOUT)
        upstream.sendall(req)
        headers, body = recv_full_response(upstream)
        return headers, body
    finally:
        upstream.close()


def _req_header(req, name):
    """Return the value of an HTTP request header (case-insensitive) or None."""
    needle = (name.lower() + ":").encode()
    for line in req.split(b"\r\n")[1:]:
        if not line:
            break
        if line.lower().startswith(needle):
            return line.split(b":", 1)[1].strip().decode("latin1", "replace")
    return None


def _cache_headers(req):
    found = {}
    for name in ("If-None-Match", "If-Modified-Since", "Range"):
        val = _req_header(req, name)
        if val:
            found[name] = val
    return found


def handle_target_request(client, req, host, port, path):
    mode = read_state()
    m = PATH_RE.search(path)
    slot_num = int(m.group(2)) if m else None
    userhash = m.group(1) if m else None
    cache_hdrs = _cache_headers(req)

    if mode == "INJECT" and slot_num is not None:
        selected = read_selected_data()
        if selected is not None:
            client.sendall(selected)
            if cache_hdrs:
                record_event(
                    "show-cache-check", slot=slot_num, host=host, path=path,
                    size=len(selected), cache_headers=cache_hdrs,
                )
                log(f"  Show: sent selected emblem for slot_{slot_num}, but the console sent a "
                    "cache-check request - it may keep using its cached copy instead")
            else:
                record_event(
                    "show", slot=slot_num, host=host, path=path, size=len(selected),
                )
                log(f"  Show: sent selected emblem for slot_{slot_num} ({len(selected)} bytes)")
            return
        log("  Show mode is on but no emblem is selected - passing real data through")

    headers, body = fetch_real(req, host, port)

    if mode == "CAPTURE" and slot_num is not None:
        group = get_or_create_capture_group(userhash)
        write_slot_bytes(group, slot_num, headers + body)
        record_event(
            "capture", slot=slot_num, group=group, host=host, path=path,
            size=len(body), cache_headers=cache_hdrs,
        )
        log(f"  Captured: group {group} slot_{slot_num} ({len(body)} bytes)")
    else:
        record_event(
            "passthrough", slot=slot_num, host=host, path=path,
            size=len(body), cache_headers=cache_hdrs,
        )

    client.sendall(headers + body)


def handle(client, client_addr=None):
    try:
        if client_addr and not is_client_allowed(client_addr[0]):
            log(f"  blocked proxy client {client_addr[0]} (not in allowlist)")
            try:
                client.sendall(b"HTTP/1.1 403 Forbidden\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")
                client.shutdown(socket.SHUT_WR)
            except OSError:
                pass
            return

        client.settimeout(config.PROXY_SOCKET_TIMEOUT)
        req = _recv_headers(client)
        if b"\r\n\r\n" not in req:
            return
        line = req.split(b"\r\n", 1)[0].decode("latin1")
        parts = line.split()
        if len(parts) < 2:
            return
        method = parts[0].upper()

        if method == "CONNECT":
            host, _, port_str = parts[1].partition(":")
            if "demonware" in host.lower() and not is_target_host(host):
                log(f"  note: HTTPS traffic to {host} (not the expected emblem host '{config.TARGET_HOST_SUBSTR}') - tunneled untouched")
            port = _parse_port(port_str, 443)
            upstream = socket.create_connection((host, port), timeout=config.PROXY_SOCKET_TIMEOUT)
            try:
                # Clear short header timeout for long-lived TLS tunnels
                client.settimeout(None)
                upstream.settimeout(None)
                client.sendall(b"HTTP/1.1 200 Connection established\r\n\r\n")
                t1 = threading.Thread(target=pipe, args=(upstream, client), daemon=True)
                t2 = threading.Thread(target=pipe, args=(client, upstream), daemon=True)
                t1.start()
                t2.start()
                t1.join()
                t2.join()
            finally:
                upstream.close()
        else:
            url = parts[1]
            rest = url.split("://", 1)[1] if "://" in url else url
            host_port, _, path = rest.partition("/")
            host, _, port_str = host_port.partition(":")
            if not host:
                host_hdr = _req_header(req, "Host") or ""
                host, _, port_str = host_hdr.partition(":")
            if not host:
                return
            port = _parse_port(port_str, 80)
            path = "/" + path

            rest_of_req = req.split(b"\r\n", 1)[1]
            new_line = f"{method} {path} HTTP/1.1\r\n".encode("latin1")
            req_rewritten = new_line + rest_of_req

            if is_target_host(host):
                handle_target_request(client, req_rewritten, host, port, path)
            else:
                if "demonware" in host.lower():
                    log(f"  note: HTTP request to {host}{path} (not the expected emblem host '{config.TARGET_HOST_SUBSTR}') - passed through untouched")
                upstream = socket.create_connection((host, port), timeout=config.PROXY_SOCKET_TIMEOUT)
                try:
                    upstream.settimeout(config.PROXY_SOCKET_TIMEOUT)
                    upstream.sendall(req_rewritten)
                    client.settimeout(config.PROXY_SOCKET_TIMEOUT)
                    t1 = threading.Thread(target=pipe, args=(upstream, client), daemon=True)
                    t2 = threading.Thread(target=pipe, args=(client, upstream), daemon=True)
                    t1.start()
                    t2.start()
                    t1.join()
                    t2.join()
                finally:
                    upstream.close()
    except ValueError as e:
        log(f"  rejected request: {e}")
        try:
            client.sendall(b"HTTP/1.1 400 Bad Request\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")
        except OSError:
            pass
    except Exception as e:
        log(f"  error: {e}")
    finally:
        try:
            client.close()
        except OSError:
            pass


class ProxyServer:
    """Stoppable threaded TCP proxy server (supports ephemeral port 0 in tests)."""

    def __init__(self, host=None, port=None):
        from .state import write_state
        if not os.path.exists(config.STATE_FILE):
            write_state("PASSTHROUGH")
        self.host = host if host is not None else config.PROXY_HOST
        self.port = port if port is not None else config.PROXY_PORT
        self._stop = threading.Event()
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind((self.host, self.port))
        self._sock.listen(200)
        self._sock.settimeout(0.5)
        self.server_address = self._sock.getsockname()

    def serve_forever(self):
        log(f"Proxy listening on {self.server_address[0]}:{self.server_address[1]}  mode={read_state()}")
        while not self._stop.is_set():
            try:
                client, addr = self._sock.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            threading.Thread(target=handle, args=(client, addr), daemon=True).start()

    def shutdown(self):
        self._stop.set()
        try:
            self._sock.close()
        except OSError:
            pass


def serve(host=None, port=None):
    """Run the proxy loop. Blocks forever - call from a dedicated thread."""
    srv = ProxyServer(host=host, port=port)
    try:
        srv.serve_forever()
    finally:
        srv.shutdown()
