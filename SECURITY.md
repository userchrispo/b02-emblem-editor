# Security & Threat Model

This project runs a local HTTP proxy (`:8080`) and a local web control panel (`:8090`) on your home network to inspect or replace plain-HTTP Black Ops II emblem slot downloads.

## Scope of Traffic Interception

- **Exact-host plain HTTP only:** Only plain HTTP requests whose `Host` exactly matches `ops2-ps3-cs.prod.demonware.net` are inspected (`emblemtool/proxy.py`). Substring matches and lookalike domains are not intercepted.
- **Zero TLS decryption:** All `CONNECT` (HTTPS) traffic—including PlayStation Network sign-in, store, and matchmaking—is tunneled raw without TLS termination or certificate installation.

## Hardening Controls

| Surface | Control | Implementation |
|---|---|---|
| Control panel exposure | Bound to loopback (`127.0.0.1:8090`) by default so other LAN devices cannot call management APIs | `emblemtool/config.py`, `--web-host` in `run.py` |
| Cross-site requests & DNS rebinding | `POST` endpoints validate `Origin` and `Host` headers against loopback / bound host; responses set `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy: no-referrer` | `emblemtool/web/server.py` |
| Path traversal & arbitrary delete | Group identifiers are validated against `^[A-Za-z0-9_-]{1,64}$`, reject reserved `_active`, and assert `os.path.commonpath` stays inside `SAVED_DIR` (and `STATIC_DIR` for static files) | `emblemtool/storage.py`, `emblemtool/web/server.py` |
| Error rendering XSS | Render errors are HTML-escaped before embedding in fallback SVG responses | `emblemtool/web/server.py` |
| Open LAN proxy & DoS | Optional `--allow-ip <PS5_IP>` client allowlist; bounded HTTP header reads (64 KiB), body cap (16 MiB), and socket timeouts (15s) | `emblemtool/proxy.py`, `run.py` |

## Operational Recommendations

1. Run with `--allow-ip <your-PS5-LAN-IP>` on shared Wi-Fi networks so only your console can use port `8080`.
2. Keep the control panel on `127.0.0.1` unless you explicitly need remote control from another device.
3. Switch mode back to **Off** (or disable the PS5 proxy setting) when you are done saving emblems.

## Reporting a Vulnerability

If you discover a security issue, please open a private security advisory on GitHub or open an issue with reproduction steps.
