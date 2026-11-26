"""Paths, ports, and other fixed settings used across the toolkit."""
import os
import sys

# Where captures and the current mode are written. When running from source
# this is the repo root; when running as a frozen exe (PyInstaller), it's the
# folder the exe itself sits in, so saved emblems survive between runs
# instead of landing in the temp folder the exe unpacks itself into.
if getattr(sys, "frozen", False):
    ROOT_DIR = os.path.dirname(os.path.abspath(sys.executable))
else:
    ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Where read-only bundled resources (shape reference images, LICENSE) live.
# PyInstaller unpacks these into a temp folder at startup (sys._MEIPASS);
# running from source, they're just files in the repo.
RESOURCE_DIR = getattr(sys, "_MEIPASS", ROOT_DIR)

SAVED_DIR = os.path.join(ROOT_DIR, "saved")
SHAPES_DIR = os.path.join(RESOURCE_DIR, "reference_shapes")
STATE_FILE = os.path.join(ROOT_DIR, "state.txt")
ACTIVITY_FILE = os.path.join(ROOT_DIR, "activity.json")

ACTIVE_NAME = "_active"  # reserved pseudo-group: the currently armed injection set

PROXY_HOST = "0.0.0.0"
PROXY_PORT = 8080
WEB_HOST = "127.0.0.1"
WEB_PORT = 8090

PROXY_ALLOW_IPS = None  # optional set[str] of allowed client IPs (e.g. PS5 IP)
PROXY_SOCKET_TIMEOUT = 15.0
PROXY_MAX_HEADER_BYTES = 65536
PROXY_MAX_BODY_BYTES = 16 * 1024 * 1024

TARGET_HOST_SUBSTR = "ops2-ps3-cs.prod.demonware.net"


def configure_data_dir(data_dir):
    """Redirect mutable runtime files (saved emblems, state, activity) to data_dir."""
    global SAVED_DIR, STATE_FILE, ACTIVITY_FILE
    base = os.path.abspath(data_dir)
    SAVED_DIR = os.path.join(base, "saved")
    STATE_FILE = os.path.join(base, "state.txt")
    ACTIVITY_FILE = os.path.join(base, "activity.json")
