#!/usr/bin/env python3
"""Start the BO2 Emblem Toolkit.

Starts the selective HTTP MITM proxy and the local web control panel together,
then opens the control panel in your browser (unless --no-browser is passed).
See README.md for usage and docs/INSTALL.md for PS5 setup.
"""
import argparse
import threading
import time
import webbrowser

from emblemtool import config
from emblemtool.proxy import ProxyServer
from emblemtool.web.netinfo import get_lan_ip
from emblemtool.web.server import make_server


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        prog="bo2-emblem-toolkit",
        description="BO2 Emblem Toolkit: selective PS5 HTTP proxy + desktop emblem editor.",
    )
    parser.add_argument(
        "--proxy-host", default=config.PROXY_HOST,
        help=f"Proxy bind address (default: {config.PROXY_HOST})",
    )
    parser.add_argument(
        "--proxy-port", type=int, default=config.PROXY_PORT,
        help=f"Proxy TCP port for PS5 network settings (default: {config.PROXY_PORT})",
    )
    parser.add_argument(
        "--web-host", default=config.WEB_HOST,
        help=f"Web control panel bind address (default: {config.WEB_HOST})",
    )
    parser.add_argument(
        "--web-port", type=int, default=config.WEB_PORT,
        help=f"Web control panel TCP port (default: {config.WEB_PORT})",
    )
    parser.add_argument(
        "--allow-ip", action="append", default=[],
        help="Restrict proxy access to specific client IP(s) (repeatable, e.g. your PS5 IP)",
    )
    parser.add_argument(
        "--data-dir", default=None,
        help="Directory for saved emblems, state.txt, and activity.json",
    )
    parser.add_argument(
        "--no-browser", action="store_true",
        help="Do not automatically open the control panel in a browser",
    )
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    config.PROXY_HOST = args.proxy_host
    config.PROXY_PORT = args.proxy_port
    config.WEB_HOST = args.web_host
    config.WEB_PORT = args.web_port
    config.PROXY_ALLOW_IPS = set(args.allow_ip) if args.allow_ip else None
    if args.data_dir:
        config.configure_data_dir(args.data_dir)

    proxy = ProxyServer(host=config.PROXY_HOST, port=config.PROXY_PORT)
    proxy_thread = threading.Thread(target=proxy.serve_forever, daemon=True)
    proxy_thread.start()

    lan_ip = get_lan_ip()
    panel_host = "localhost" if config.WEB_HOST in ("127.0.0.1", "0.0.0.0") else config.WEB_HOST
    panel_url = f"http://{panel_host}:{config.WEB_PORT}"

    print("=" * 60)
    print(" BO2 Emblem Toolkit")
    print("=" * 60)
    print(f" Control panel : {panel_url} (bound to {config.WEB_HOST})")
    if lan_ip:
        print(f" PS5 proxy     : {lan_ip} : {config.PROXY_PORT}")
    else:
        print(f" PS5 proxy     : <this PC's LAN IP> : {config.PROXY_PORT}")
        print("   (couldn't auto-detect your LAN IP - see docs/INSTALL.md)")
    if config.PROXY_ALLOW_IPS:
        print(f" Allowlist     : {', '.join(sorted(config.PROXY_ALLOW_IPS))}")
    print("=" * 60)
    print(" Press Ctrl+C to stop.")
    print()

    if not args.no_browser:
        time.sleep(0.3)
        try:
            webbrowser.open(panel_url)
        except Exception:
            pass

    server = make_server(host=config.WEB_HOST, port=config.WEB_PORT)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping...")
    finally:
        proxy.shutdown()
        server.server_close()


if __name__ == "__main__":
    main()
