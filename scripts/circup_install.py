"""Install CircuitPython libraries on the device over WiFi.

Bypasses circup's HTTP directory-scan (which times out on the ESP32 web
server when the /lib tree is deep).  Instead, extracts the needed .mpy
files from the locally-cached Adafruit CircuitPython Bundle and uploads
them via the same HTTP PUT workflow used by deploy.py.

The bundle is downloaded by circup and lives at:
    ~/.local/share/circup/adafruit-circuitpython-bundle-<ver>mpy.zip

Usage:
    poetry run circup-install
    python scripts/circup_install.py
"""

import base64
import sys
import tomllib
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PYPROJECT_PATH = REPO_ROOT / "pyproject.toml"
SETTINGS_PATH = REPO_ROOT / "settings.toml"

BUNDLE_CACHE_DIR = Path.home() / ".local" / "share" / "circup"
CP_LIB_PREFIX = "adafruit-circuitpython-"

# Explicit mapping: pyproject dependency name → bundle lib name(s).
# A lib name ending in "/" means a directory; without means a single .mpy file.
_LIB_MAP: dict[str, list[str]] = {
    "adafruit-circuitpython-bmp3xx":      ["adafruit_bmp3xx"],
    "adafruit-circuitpython-bus-device":  ["adafruit_bus_device/"],
    "adafruit-circuitpython-gps":         ["adafruit_gps"],
    "adafruit-circuitpython-neopixel":    ["neopixel"],
    "adafruit-circuitpython-ntp":         ["adafruit_ntp"],
    "adafruit-circuitpython-pcf8523":     ["adafruit_pcf8523/"],
    "adafruit-circuitpython-sd":          ["adafruit_sdcard"],
    "adafruit-circuitpython-shtc3":       ["adafruit_shtc3"],
}

# adafruit_bus_device is always required (used by HM3301 AQI sensor)
_ALWAYS_INCLUDE = ["adafruit-circuitpython-bus-device"]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _load_toml(path: Path) -> dict:
    with open(path, "rb") as f:
        return tomllib.load(f)


def _auth_header(password: str) -> dict[str, str]:
    token = base64.b64encode(f":{password}".encode()).decode()
    return {"Authorization": f"Basic {token}"}


def _put(host: str, remote_path: str, data: bytes, auth: dict) -> None:
    url = f"http://{host}/fs/{remote_path}"
    req = urllib.request.Request(
        url,
        data=data,
        headers={**auth, "Content-Type": "application/octet-stream"},
        method="PUT",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            status = resp.status
    except urllib.error.HTTPError as exc:
        status = exc.code
        if status not in (200, 201, 204):
            raise RuntimeError(f"PUT {url} → HTTP {status}") from exc


def _mkdir(host: str, remote_dir: str, auth: dict) -> None:
    url = f"http://{host}/fs/{remote_dir.rstrip('/')}/"
    req = urllib.request.Request(
        url, data=b"", headers={**auth, "Content-Type": "application/octet-stream"}, method="PUT"
    )
    try:
        with urllib.request.urlopen(req, timeout=15):
            pass
    except urllib.error.HTTPError as exc:
        if exc.code not in (200, 201, 204):
            raise RuntimeError(f"mkdir {url} → HTTP {exc.code}") from exc


def _find_bundle(cp_major: int = 10) -> Path:
    """Return the path to the best matching bundle zip for cp_major."""
    candidates = sorted(
        BUNDLE_CACHE_DIR.glob(f"adafruit-circuitpython-bundle-{cp_major}*mpy.zip"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    if candidates:
        return candidates[0]
    # Fall back to any mpy bundle
    candidates = sorted(
        BUNDLE_CACHE_DIR.glob("adafruit-circuitpython-bundle-*mpy.zip"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    if candidates:
        return candidates[0]
    raise FileNotFoundError(
        f"No Adafruit CircuitPython bundle found in {BUNDLE_CACHE_DIR}.\n"
        "Run: circup --host <device-ip> install neopixel   (once, to prime the cache)"
    )


def _extract_lib_entries(zf: zipfile.ZipFile, lib_name: str) -> list[str]:
    """Return all zip entries that belong to lib_name inside the lib/ folder."""
    is_dir = lib_name.endswith("/")
    base = lib_name.rstrip("/")

    if is_dir:
        prefix_mpy = f"/lib/{base}/"
        return [n for n in zf.namelist() if f"/lib/{base}/" in n and not n.endswith("/")]
    else:
        return [
            n for n in zf.namelist()
            if n.endswith(f"/lib/{base}.mpy") or n.endswith(f"/lib/{base}.py")
        ]


def _remote_path(zip_entry: str) -> str:
    """Strip everything up to and including '/lib/' to get the device path."""
    idx = zip_entry.find("/lib/")
    return "lib/" + zip_entry[idx + len("/lib/"):]


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> int:
    if not PYPROJECT_PATH.exists():
        print(f"Error: {PYPROJECT_PATH} not found", file=sys.stderr)
        return 1
    if not SETTINGS_PATH.exists():
        print(
            f"Error: {SETTINGS_PATH} not found — copy settings.toml.example",
            file=sys.stderr,
        )
        return 1

    pyproject = _load_toml(PYPROJECT_PATH)
    settings = _load_toml(SETTINGS_PATH)

    host = settings.get("ESP32_IP", "").strip()
    if not host:
        print("Error: ESP32_IP not set in settings.toml", file=sys.stderr)
        return 1
    password = settings.get("CIRCUITPY_WEB_API_PASSWORD", "").strip()
    auth = _auth_header(password)

    # Collect the libraries declared in pyproject.toml + always-required ones
    raw_deps: list[str] = pyproject.get("project", {}).get("dependencies", [])
    pkg_names = []
    for dep in raw_deps:
        name = dep.split()[0].split("(")[0].split(">=")[0].split("==")[0].strip().lower()
        if name.startswith(CP_LIB_PREFIX):
            pkg_names.append(name)
    for always in _ALWAYS_INCLUDE:
        if always not in pkg_names:
            pkg_names.append(always)

    # Resolve to lib names
    lib_names: list[str] = []
    unknown: list[str] = []
    for pkg in pkg_names:
        if pkg in _LIB_MAP:
            lib_names.extend(_LIB_MAP[pkg])
        else:
            unknown.append(pkg)
    if unknown:
        print(f"Warning: no lib mapping for: {unknown} — skipping", file=sys.stderr)

    bundle_path = _find_bundle()
    print(f"Bundle  : {bundle_path.name}")
    print(f"Host    : {host}")
    print(f"Libs    : {', '.join(lib_names)}")
    print()

    uploaded = 0
    skipped = 0

    with zipfile.ZipFile(bundle_path) as zf:
        for lib_name in lib_names:
            entries = _extract_lib_entries(zf, lib_name)
            if not entries:
                print(f"  [WARN] {lib_name} — not found in bundle")
                skipped += 1
                continue

            # Create parent directory on device if this is a package
            if lib_name.endswith("/"):
                _mkdir(host, f"lib/{lib_name.rstrip('/')}", auth)

            for entry in entries:
                remote = _remote_path(entry)
                # Ensure intermediate directories exist
                parts = remote.split("/")
                for depth in range(2, len(parts)):
                    _mkdir(host, "/".join(parts[:depth]), auth)

                data = zf.read(entry)
                _put(host, remote, data, auth)
                print(f"  uploaded  {remote}")
                uploaded += 1

    print(f"\nDone — {uploaded} file(s) uploaded, {skipped} skipped.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
