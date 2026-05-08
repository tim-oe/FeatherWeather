"""Install CircuitPython libraries on the device.

Bypasses circup's HTTP directory-scan (which times out on the ESP32 web
server when the /lib tree is deep).  Instead, extracts the needed .mpy
files from the locally-cached Adafruit CircuitPython Bundle and uploads
them via WiFi (default) or serial/mpremote (--serial).

The bundle is downloaded by circup and lives at:
    ~/.local/share/circup/adafruit-circuitpython-bundle-<ver>mpy.zip

Usage:
    poetry run circup-install                       # WiFi upload
    poetry run circup-install --serial              # serial via mpremote
    poetry run circup-install --serial --port /dev/ttyACM1
"""

import argparse
import base64
import shutil
import subprocess
import sys
import tempfile
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

# Name overrides — only needed when the bundle module name does NOT follow the
# standard auto-derivation rule:
#   strip "adafruit-circuitpython-" prefix, replace "-" → "_", prepend "adafruit_"
# e.g. "adafruit-circuitpython-bmp3xx" → "adafruit_bmp3xx"  (no override needed)
#
# Add a new dependency to pyproject.toml and it is picked up automatically.
# Only add an entry here if the bundle name genuinely differs from the derivation.
_OVERRIDES: dict[str, list[str]] = {
    "adafruit-circuitpython-busdevice": ["adafruit_bus_device"],
    "adafruit-circuitpython-neopixel":  ["neopixel"],
    "adafruit-circuitpython-sd":        ["adafruit_sdcard"],
}


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


def _derive_module_name(pkg_name: str) -> str:
    """Derive the bundle module name from an adafruit-circuitpython-* package name.

    Strips the ``adafruit-circuitpython-`` prefix, replaces ``-`` with ``_``,
    and prepends ``adafruit_``.  Works for the vast majority of packages; the
    handful that differ are listed in ``_OVERRIDES``.
    """
    suffix = pkg_name.removeprefix(CP_LIB_PREFIX).replace("-", "_")
    return f"adafruit_{suffix}"


def _extract_lib_entries(zf: zipfile.ZipFile, module_name: str) -> list[str]:
    """Return all zip entries for *module_name*, auto-detecting file vs directory.

    Checks for a package directory (``/lib/<module_name>/``) first; falls back
    to a single ``.mpy`` / ``.py`` file.  No trailing slash required in the name.
    """
    names = zf.namelist()
    # Package directory
    entries = [n for n in names if f"/lib/{module_name}/" in n and not n.endswith("/")]
    if entries:
        return entries
    # Single file
    return [
        n for n in names
        if n.endswith(f"/lib/{module_name}.mpy") or n.endswith(f"/lib/{module_name}.py")
    ]


def _remote_path(zip_entry: str) -> str:
    """Strip everything up to and including '/lib/' to get the device path."""
    idx = zip_entry.find("/lib/")
    return "lib/" + zip_entry[idx + len("/lib/"):]


# ---------------------------------------------------------------------------
# Shared: resolve lib names from pyproject.toml
# ---------------------------------------------------------------------------


def _resolve_module_names() -> list[str]:
    """Derive bundle module names for all adafruit-circuitpython-* deps in pyproject.toml.

    For each matching dependency, checks ``_OVERRIDES`` first; if absent, applies
    the standard derivation rule via ``_derive_module_name()``.  No manual mapping
    table to maintain — adding a dep to pyproject.toml is the only required step.
    """
    pyproject = _load_toml(PYPROJECT_PATH)
    raw_deps: list[str] = pyproject.get("project", {}).get("dependencies", [])

    module_names: list[str] = []
    for dep in raw_deps:
        pkg = dep.split()[0].split("(")[0].strip().lower()
        if not pkg.startswith(CP_LIB_PREFIX):
            continue
        if pkg in _OVERRIDES:
            module_names.extend(_OVERRIDES[pkg])
        else:
            module_names.append(_derive_module_name(pkg))
    return module_names


# ---------------------------------------------------------------------------
# WiFi install
# ---------------------------------------------------------------------------


def install_wifi(host: str, password: str, module_names: list[str], bundle_path: Path) -> int:
    auth = _auth_header(password)
    print(f"Bundle  : {bundle_path.name}")
    print(f"Host    : {host}")
    print(f"Modules : {', '.join(module_names)}")
    print()

    uploaded = 0
    skipped = 0

    with zipfile.ZipFile(bundle_path) as zf:
        for module_name in module_names:
            entries = _extract_lib_entries(zf, module_name)
            if not entries:
                print(f"  [WARN] {module_name} — not found in bundle")
                skipped += 1
                continue

            for entry in entries:
                remote = _remote_path(entry)
                # Create any intermediate directories the entry requires
                parts = remote.split("/")
                for depth in range(2, len(parts)):
                    _mkdir(host, "/".join(parts[:depth]), auth)
                _put(host, remote, zf.read(entry), auth)
                print(f"  uploaded  {remote}")
                uploaded += 1

    print(f"\nDone — {uploaded} file(s) uploaded, {skipped} skipped.")
    return 0


# ---------------------------------------------------------------------------
# Serial install (mpremote)
# ---------------------------------------------------------------------------


def install_serial(port: str, module_names: list[str], bundle_path: Path) -> int:
    print(f"Bundle  : {bundle_path.name}")
    print(f"Port    : {port}")
    print(f"Modules : {', '.join(module_names)}")
    print()

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        extracted = 0
        skipped = 0

        with zipfile.ZipFile(bundle_path) as zf:
            for module_name in module_names:
                entries = _extract_lib_entries(zf, module_name)
                if not entries:
                    print(f"  [WARN] {module_name} — not found in bundle")
                    skipped += 1
                    continue
                for entry in entries:
                    rel = _remote_path(entry)            # e.g. lib/adafruit_sdcard.mpy
                    dest = tmp_path / rel[len("lib/"):]  # strip leading lib/
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    dest.write_bytes(zf.read(entry))
                    extracted += 1
                print(f"  extracted {module_name}  ({len(entries)} file(s))")

        # Collect subdirectory paths that need to be created on the device.
        # "lib" is omitted — it always exists on any CircuitPython device.
        dirs_to_create: list[str] = []
        for fn in sorted(tmp_path.rglob("*")):
            if fn.is_file():
                rel = fn.relative_to(tmp_path)
                for depth in range(1, len(rel.parts)):
                    d = "lib/" + "/".join(rel.parts[:depth])
                    if d not in dirs_to_create and d != "lib":
                        dirs_to_create.append(d)

        print(f"\nUploading {extracted} file(s) via mpremote ...")

        # Single mpremote session: exec (mkdir + touch) then cp all files.
        # Pre-touching every destination file lets fs_exists return True,
        # sidestepping the _convert_filesystem_error bug for absent files.
        # -f skips the per-file hash read round-trip (files are empty anyway).
        file_entries = sorted(fn for fn in tmp_path.rglob("*") if fn.is_file())

        mkdir_lines = ["import os"]
        for d in dirs_to_create:
            mkdir_lines += [
                "try:",
                f"    os.mkdir('/{d}')",
                "except OSError:",
                "    pass",
            ]
        for fn in file_entries:
            rel = fn.relative_to(tmp_path)
            dest_path = "/lib/" + "/".join(rel.parts)
            mkdir_lines.append(f"open('{dest_path}','wb').close()")
        mkdir_code = "\n".join(mkdir_lines) + "\n"

        if dirs_to_create or file_entries:
            print(f"  Creating {len(dirs_to_create)} directories + touching {len(file_entries)} files ...")

        chain: list[str] = ["mpremote", "connect", port, "+", "exec", mkdir_code]
        for fn in file_entries:
            rel = fn.relative_to(tmp_path)
            chain += ["+", "cp", "-f", str(fn), ":lib/" + "/".join(rel.parts)]

        result = subprocess.run(chain)
        if result.returncode != 0:
            print("Error: mpremote upload failed", file=sys.stderr)
            return 1

    print(f"\nDone — {extracted} file(s) uploaded, {skipped} skipped.")
    return 0


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(description="Install CircuitPython libraries on device.")
    parser.add_argument(
        "--serial",
        action="store_true",
        help="Install via USB-serial using mpremote (use when WiFi is unavailable)",
    )
    parser.add_argument(
        "--port",
        metavar="PORT",
        default="/dev/ttyACM0",
        help="Serial port for --serial mode (default: /dev/ttyACM0)",
    )
    args = parser.parse_args()

    if not PYPROJECT_PATH.exists():
        print(f"Error: {PYPROJECT_PATH} not found", file=sys.stderr)
        return 1

    module_names = _resolve_module_names()
    bundle_path = _find_bundle()

    if args.serial:
        return install_serial(args.port, module_names, bundle_path)

    if not SETTINGS_PATH.exists():
        print(
            f"Error: {SETTINGS_PATH} not found — copy settings.toml.example",
            file=sys.stderr,
        )
        return 1
    settings = _load_toml(SETTINGS_PATH)
    host = settings.get("ESP32_IP", "").strip()
    if not host:
        print("Error: ESP32_IP not set in settings.toml", file=sys.stderr)
        return 1
    password = settings.get("CIRCUITPY_WEB_API_PASSWORD", "").strip()

    return install_wifi(host, password, module_names, bundle_path)


if __name__ == "__main__":
    sys.exit(main())
