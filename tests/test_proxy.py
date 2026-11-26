"""Integration and security tests for emblemtool/proxy.py."""
import http.server
import os
import shutil
import socket
import sys
import tempfile
import threading
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from emblemtool import activity, broadcast, config, proxy, state, storage
from emblemtool.shapes.render import encode_layers, encode_slot_file_bytes


class _UpstreamHandler(http.server.BaseHTTPRequestHandler):
    payload = b""
    use_chunked = False

    def log_message(self, fmt, *args):
        pass

    def do_GET(self):
        body = self.payload
        self.send_response(200)
        self.send_header("Content-Type", "application/octet-stream")
        if self.use_chunked:
            self.send_header("Transfer-Encoding", "chunked")
            self.end_headers()
            mid = len(body) // 2
            for part in (body[:mid], body[mid:]):
                if part:
                    self.wfile.write(f"{len(part):x}\r\n".encode("ascii") + part + b"\r\n")
            self.wfile.write(b"0\r\n\r\n")
        else:
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)


def _raw_proxy_request(proxy_port, raw_req):
    with socket.create_connection(("127.0.0.1", proxy_port), timeout=5) as s:
        try:
            s.sendall(raw_req)
            s.shutdown(socket.SHUT_WR)
        except OSError:
            pass
        chunks = []
        while True:
            try:
                data = s.recv(65536)
            except ConnectionResetError:
                break
            if not data:
                break
            chunks.append(data)
        return b"".join(chunks)


class ProxyUnitHelpers(unittest.TestCase):
    def test_exact_host_match_rejects_lookalike(self):
        old = config.TARGET_HOST_SUBSTR
        try:
            config.TARGET_HOST_SUBSTR = "ops2-ps3-cs.prod.demonware.net"
            self.assertTrue(proxy.is_target_host("ops2-ps3-cs.prod.demonware.net"))
            self.assertTrue(proxy.is_target_host("OPS2-PS3-CS.PROD.DEMONWARE.NET"))
            self.assertFalse(proxy.is_target_host("ops2-ps3-cs.prod.demonware.net.evil.com"))
            self.assertFalse(proxy.is_target_host("evil-ops2-ps3-cs.prod.demonware.net"))
        finally:
            config.TARGET_HOST_SUBSTR = old

    def test_allowlist_helper(self):
        old = config.PROXY_ALLOW_IPS
        try:
            config.PROXY_ALLOW_IPS = None
            self.assertTrue(proxy.is_client_allowed("192.168.1.50"))
            config.PROXY_ALLOW_IPS = {"192.168.1.50"}
            self.assertTrue(proxy.is_client_allowed("192.168.1.50"))
            self.assertFalse(proxy.is_client_allowed("192.168.1.99"))
        finally:
            config.PROXY_ALLOW_IPS = old


class ProxyIntegration(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self._old_saved = config.SAVED_DIR
        self._old_state = config.STATE_FILE
        self._old_act = config.ACTIVITY_FILE
        self._old_target = config.TARGET_HOST_SUBSTR
        self._old_allow = config.PROXY_ALLOW_IPS
        self._old_max_hdr = config.PROXY_MAX_HEADER_BYTES

        config.configure_data_dir(self.tmp)
        config.TARGET_HOST_SUBSTR = "127.0.0.1"
        config.PROXY_ALLOW_IPS = None

        self.upstream_body = encode_layers([{
            "index": 0, "shape": 192,
            "r": 0.2, "g": 0.6, "b": 1.0, "a": 1.0,
            "x": 0.0, "y": 0.0, "sx": -1.0, "sy": -1.0, "rot": 0.0,
            "outlined": False, "flipped": False,
        }])
        _UpstreamHandler.payload = self.upstream_body
        _UpstreamHandler.use_chunked = False

        self.upstream = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _UpstreamHandler)
        self.up_port = self.upstream.server_address[1]
        self.up_thread = threading.Thread(target=self.upstream.serve_forever, daemon=True)
        self.up_thread.start()

        self.proxy_srv = proxy.ProxyServer("127.0.0.1", 0)
        self.proxy_port = self.proxy_srv.server_address[1]
        self.proxy_thread = threading.Thread(target=self.proxy_srv.serve_forever, daemon=True)
        self.proxy_thread.start()

    def tearDown(self):
        storage.wait_warm_thumbs()
        self.proxy_srv.shutdown()
        self.upstream.shutdown()
        self.upstream.server_close()
        config.SAVED_DIR = self._old_saved
        config.STATE_FILE = self._old_state
        config.ACTIVITY_FILE = self._old_act
        config.TARGET_HOST_SUBSTR = self._old_target
        config.PROXY_ALLOW_IPS = self._old_allow
        config.PROXY_MAX_HEADER_BYTES = self._old_max_hdr
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _slot_req(self, extra_headers=""):
        return (
            f"GET http://127.0.0.1:{self.up_port}/storage/u51b08745d269.slot_504 HTTP/1.1\r\n"
            f"Host: 127.0.0.1:{self.up_port}\r\n"
            f"{extra_headers}"
            "Connection: close\r\n\r\n"
        ).encode("ascii")

    def test_passthrough_forwards_and_records_activity(self):
        state.write_state("PASSTHROUGH")
        resp = _raw_proxy_request(self.proxy_port, self._slot_req())
        self.assertIn(b"200 OK", resp)
        self.assertTrue(resp.endswith(self.upstream_body))
        act = activity.read_activity()
        self.assertEqual(act["last_hit"]["kind"], "passthrough")
        self.assertEqual(act["last_hit"]["slot"], 504)
        self.assertEqual(storage.list_emblems(), [])

    def test_capture_saves_slot_to_disk(self):
        state.write_state("CAPTURE")
        resp = _raw_proxy_request(self.proxy_port, self._slot_req())
        self.assertTrue(resp.endswith(self.upstream_body))
        emblems = storage.list_emblems()
        self.assertEqual(len(emblems), 1)
        self.assertEqual(emblems[0]["slot"], 504)
        act = activity.read_activity()
        self.assertEqual(act["last_hit"]["kind"], "capture")

    def test_inject_replaces_response_and_detects_cache_check(self):
        custom = encode_slot_file_bytes([{
            "index": 0, "shape": 178,
            "r": 1.0, "g": 0.0, "b": 0.0, "a": 1.0,
            "x": 0.0, "y": 0.0, "sx": -1.0, "sy": -1.0, "rot": 45.0,
            "outlined": True, "flipped": False,
        }])
        group, slot = storage.save_authored_emblem(custom, "Injected")
        broadcast.select_emblem(group, slot)
        state.write_state("INJECT")

        resp = _raw_proxy_request(self.proxy_port, self._slot_req())
        self.assertEqual(resp, custom)
        self.assertTrue(activity.read_activity()["needs_session_reset"])

        activity.clear_session_flag()
        resp2 = _raw_proxy_request(
            self.proxy_port,
            self._slot_req(extra_headers='If-None-Match: "abc"\r\n'),
        )
        self.assertEqual(resp2, custom)
        act = activity.read_activity()
        self.assertEqual(act["last_hit"]["kind"], "show-cache-check")
        self.assertIn("If-None-Match", act["last_hit"]["cache_headers"])
        self.assertFalse(act["needs_session_reset"])

    def test_chunked_upstream_is_decoded_and_captured(self):
        _UpstreamHandler.use_chunked = True
        state.write_state("CAPTURE")
        resp = _raw_proxy_request(self.proxy_port, self._slot_req())
        self.assertIn(f"Content-Length: {len(self.upstream_body)}".encode("ascii"), resp)
        self.assertTrue(resp.endswith(self.upstream_body))
        self.assertEqual(len(storage.list_emblems()), 1)

    def test_client_ip_allowlist_blocks_unauthorized(self):
        config.PROXY_ALLOW_IPS = {"10.0.0.5"}
        resp = _raw_proxy_request(self.proxy_port, self._slot_req())
        self.assertTrue(resp.startswith(b"HTTP/1.1 403 Forbidden"))

    def test_oversized_headers_rejected(self):
        config.PROXY_MAX_HEADER_BYTES = 256
        huge = b"GET http://127.0.0.1/ HTTP/1.1\r\n" + (b"X-Pad: " + b"A" * 400 + b"\r\n\r\n")
        resp = _raw_proxy_request(self.proxy_port, huge)
        self.assertTrue(resp.startswith(b"HTTP/1.1 400 Bad Request"))

    def test_connect_tunnel_pipes_raw_bytes(self):
        # Spin up a tiny echo server to simulate a TLS endpoint
        echo = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        echo.bind(("127.0.0.1", 0))
        echo.listen(1)
        echo_port = echo.getsockname()[1]

        def run_echo():
            conn, _ = echo.accept()
            data = conn.recv(1024)
            conn.sendall(b"ECHO:" + data)
            conn.close()
            echo.close()

        t = threading.Thread(target=run_echo, daemon=True)
        t.start()

        with socket.create_connection(("127.0.0.1", self.proxy_port), timeout=5) as s:
            s.sendall(f"CONNECT 127.0.0.1:{echo_port} HTTP/1.1\r\nHost: 127.0.0.1:{echo_port}\r\n\r\n".encode())
            banner = s.recv(1024)
            self.assertIn(b"200 Connection established", banner)
            s.sendall(b"hello-tls")
            reply = s.recv(1024)
            self.assertEqual(reply, b"ECHO:hello-tls")
        t.join(timeout=2)


if __name__ == "__main__":
    unittest.main()
