"""Run tests, then deploy code to the CircuitPython device.

Three transport modes are supported:

  WiFi (default)
    Uses the CircuitPython Web Workflow REST API (HTTP PUT):
        code.py              → http://<ESP32_IP>/fs/code.py
        src/featherweather/  → http://<ESP32_IP>/fs/lib/featherweather/
    The CIRCUITPY_WEB_API_PASSWORD from settings.toml is sent as HTTP Basic
    Auth (empty username, password as configured on the device).

  Serial / mpremote  (--serial)
    Transfers files over the USB-serial port using mpremote.
    Use when WiFi is unavailable or the device is in a broken state.
    The Feather ESP32 V2 uses a CH340 USB-to-serial chip; it never exposes a
    CIRCUITPY mass-storage drive, so this is the correct USB recovery path.
    Requires: poetry run mpremote (already a project dependency).

  USB mass storage  (--usb / --usb-path)
    Copies files directly to a CIRCUITPY drive mounted as a filesystem.
    Only relevant for boards with native USB (e.g. ESP32-S2 / S3).
    Not applicable to the Feather ESP32 V2.

Lint and tests run automatically before a normal deploy and must pass.
Pass --skip-tests to bypass both checks (e.g. for quick iteration).

settings.toml is NOT deployed by default (it contains credentials).
Pass --settings to explicitly upload it alongside the rest of the deploy.

Usage:
    poetry run deploy
    python scripts/deploy.py
    python scripts/deploy.py --settings             # also deploy settings.toml
    python scripts/deploy.py --skip-tests           # skip tests before deploy
    python scripts/deploy.py --diagnostic           # deploy diagnostic.py as code.py
    python scripts/deploy.py --serial               # serial deploy via mpremote
    python scripts/deploy.py --serial --settings    # serial + settings.toml
    python scripts/deploy.py --serial --diagnostic  # serial + diagnostic mode
    python scripts/deploy.py --serial --port /dev/ttyACM1  # non-default port
    python scripts/deploy.py --usb                  # USB drive deploy (ESP32-S2/S3)
    python scripts/deploy.py --usb-path /media/you/CIRCUITPY
"""

import argparse
import getpass
import shutil
import subprocess
import sys
import tomllib
import urllib.request
import urllib.error
import base64
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PYPROJECT_PATH = REPO_ROOT / "pyproject.toml"
SETTINGS_PATH = REPO_ROOT / "settings.toml"

CODE_PY = REPO_ROOT / "code.py"
DIAGNOSTIC_PY = REPO_ROOT / "diagnostic.py"
BOOT_PY = REPO_ROOT / "boot.py"
LIB_SRC = REPO_ROOT / "src" / "featherweather"


def _resolve_source(mode: str) -> tuple[Path, str]:
    """Return (source_file, label) for mode in {'normal', 'diagnostic'}."""
    if mode == "diagnostic":
        return DIAGNOSTIC_PY, "diagnostic.py (as code.py)"
    return CODE_PY, "code.py"


def _mode_banner(mode: str) -> str:
    return {
        "diagnostic": "Mode: DIAGNOSTIC — device will run self-test on next boot",
        "normal":     "",
    }.get(mode, "")


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------


def load_settings() -> tuple[str, str]:
    """Return (host, password) from settings.toml."""
    if not SETTINGS_PATH.exists():
        print(
            f"Error: {SETTINGS_PATH} not found — copy settings.toml.example and fill it in",
            file=sys.stderr,
        )
        sys.exit(1)

    with open(SETTINGS_PATH, "rb") as f:
        settings = tomllib.load(f)

    host = settings.get("ESP32_IP", "").strip()
    if not host:
        print("Error: ESP32_IP is not set in settings.toml", file=sys.stderr)
        sys.exit(1)

    password = settings.get("CIRCUITPY_WEB_API_PASSWORD", "").strip()
    return host, password


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def run_lint() -> bool:
    """Run the project linter. Returns True if all checks pass."""
    print("=" * 60)
    print("Running lint...")
    print("=" * 60)
    result = subprocess.run(["poetry", "run", "lint"])
    if result.returncode == 0:
        print("\nLint passed.\n")
        return True
    print("\nLint FAILED — aborting deploy.", file=sys.stderr)
    return False


def run_tests() -> bool:
    """Run pytest. Returns True if tests pass (or no tests collected)."""
    print("=" * 60)
    print("Running tests...")
    print("=" * 60)
    result = subprocess.run(["pytest", "tests/", "-v", "--tb=short"])
    # exit code 5 = no tests collected — treat as pass
    if result.returncode in (0, 5):
        print("\nTests passed.\n")
        return True
    print("\nTests FAILED — aborting deploy.", file=sys.stderr)
    return False


# ---------------------------------------------------------------------------
# Web Workflow upload helpers
# ---------------------------------------------------------------------------


def _auth_header(password: str) -> dict[str, str]:
    token = base64.b64encode(f":{password}".encode()).decode()
    return {"Authorization": f"Basic {token}"}


def _put(url: str, data: bytes, headers: dict[str, str]) -> None:
    req = urllib.request.Request(
        url,
        data=data,
        headers={**headers, "Content-Type": "application/octet-stream"},
        method="PUT",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            status = resp.status
    except urllib.error.HTTPError as exc:
        status = exc.code
        if status not in (200, 201, 204):
            raise RuntimeError(f"PUT {url} failed with HTTP {status}") from exc


def mkdir_remote(host: str, remote_dir: str, auth: dict[str, str]) -> None:
    """Create a directory on the device (trailing slash signals directory)."""
    url = f"http://{host}/fs/{remote_dir.strip('/')}/"
    _put(url, b"", auth)


def upload_file(host: str, remote_path: str, local_path: Path, auth: dict[str, str]) -> None:
    """Upload a single file to the device."""
    url = f"http://{host}/fs/{remote_path}"
    data = local_path.read_bytes()
    _put(url, data, auth)
    print(f"  uploaded  {remote_path}")


# ---------------------------------------------------------------------------
# WiFi deployment
# ---------------------------------------------------------------------------


def deploy_wifi(host: str, password: str, mode: str = "normal", deploy_settings: bool = False) -> int:
    auth = _auth_header(password)

    source_file, mode_label = _resolve_source(mode)
    banner = _mode_banner(mode)

    print("=" * 60)
    print(f"Deploying via WiFi → {host}")
    if banner:
        print(banner)
    print("=" * 60)

    step = 1
    total = 4 if deploy_settings else 3

    # 1. Upload boot.py
    print(f"\n[{step}/{total}] Uploading boot.py ...")
    if not BOOT_PY.exists():
        print(f"Error: {BOOT_PY} not found", file=sys.stderr)
        return 1
    upload_file(host, "boot.py", BOOT_PY, auth)
    step += 1

    # 2. Upload settings.toml (optional)
    if deploy_settings:
        print(f"\n[{step}/{total}] Uploading settings.toml ...")
        if not SETTINGS_PATH.exists():
            print(f"Error: {SETTINGS_PATH} not found", file=sys.stderr)
            return 1
        upload_file(host, "settings.toml", SETTINGS_PATH, auth)
        step += 1

    # 3. Upload code.py (or diagnostic.py renamed to code.py)
    print(f"\n[{step}/{total}] Uploading {mode_label} ...")
    if not source_file.exists():
        print(f"Error: {source_file} not found", file=sys.stderr)
        return 1
    upload_file(host, "code.py", source_file, auth)
    step += 1

    # 4. Upload the featherweather package to /lib/featherweather/
    print(f"\n[{step}/{total}] Uploading featherweather package ...")
    if not LIB_SRC.exists():
        print(f"Error: {LIB_SRC} not found", file=sys.stderr)
        return 1

    py_files = sorted(LIB_SRC.rglob("*.py"))
    dirs_needed: list[str] = []
    for f in py_files:
        rel = f.relative_to(LIB_SRC)
        parts = rel.parts[:-1]
        for depth in range(len(parts) + 1):
            candidate = "lib/featherweather/" + "/".join(parts[:depth])
            if candidate not in dirs_needed:
                dirs_needed.append(candidate)

    for d in dirs_needed:
        mkdir_remote(host, d, auth)

    for f in py_files:
        rel = f.relative_to(LIB_SRC)
        remote = "lib/featherweather/" + "/".join(rel.parts)
        upload_file(host, remote, f, auth)

    print(f"\nDeploy complete — {len(py_files)} library file(s) uploaded.")
    return 0


# ---------------------------------------------------------------------------
# Serial deployment (mpremote)
# ---------------------------------------------------------------------------

_DEFAULT_SERIAL_PORT = "/dev/ttyACM0"



def deploy_serial(port: str, mode: str = "normal", deploy_settings: bool = False) -> int:
    source_file, mode_label = _resolve_source(mode)
    banner = _mode_banner(mode)

    print("=" * 60)
    print(f"Deploying via serial → {port}")
    if banner:
        print(banner)
    print("=" * 60)

    if not source_file.exists():
        print(f"Error: {source_file} not found", file=sys.stderr)
        return 1
    if not BOOT_PY.exists():
        print(f"Error: {BOOT_PY} not found", file=sys.stderr)
        return 1
    if not LIB_SRC.exists():
        print(f"Error: {LIB_SRC} not found", file=sys.stderr)
        return 1

    py_files = sorted(LIB_SRC.rglob("*.py"))

    # Collect every directory path needed under lib/featherweather/ on the device.
    # "lib" is omitted — it always exists on any CircuitPython device.
    dirs_to_create: list[str] = ["lib/featherweather"]
    for f in py_files:
        rel = f.relative_to(LIB_SRC)
        for depth in range(1, len(rel.parts)):
            d = "lib/featherweather/" + "/".join(rel.parts[:depth])
            if d not in dirs_to_create:
                dirs_to_create.append(d)

    # -----------------------------------------------------------------------
    # Single mpremote session: exec (mkdir + touch) then cp all files.
    #
    # Why a single session matters:
    #   Each subprocess call causes a soft reset, which runs boot.py (I2C RTC
    #   sync).  Combining everything into one chain cuts that from 2 resets to 1.
    #
    # Why we pre-touch every destination file in the exec:
    #   mpremote's _convert_filesystem_error can't parse CircuitPython's
    #   "OSError: [Errno 2] No such file/directory" format, so for absent files
    #   it returns TransportExecError instead of OSError.  fs_exists only catches
    #   OSError, so the error escapes and kills the cp before any write happens.
    #   Pre-touching ensures fs_exists always returns True, sidestepping the bug.
    #
    # Why -f on the featherweather files:
    #   With files pre-touched (empty), the hash fallback in fs_hashfile would
    #   read 0 bytes, compute the hash of empty content, see a mismatch, and copy
    #   anyway — but that's an extra read round-trip per file.  -f skips the hash
    #   check entirely (fs_exists still runs and succeeds since files are touched).
    # -----------------------------------------------------------------------
    print("\n[4/4] featherweather package")
    print(f"  Creating {len(dirs_to_create)} directories + touching {len(py_files)} files ...")

    mkdir_lines = ["import os"]
    for d in dirs_to_create:
        mkdir_lines += [
            "try:",
            f"    os.mkdir('/{d}')",
            "except OSError:",
            "    pass",
        ]
    for f in py_files:
        rel = f.relative_to(LIB_SRC)
        dest_path = "/lib/featherweather/" + "/".join(rel.parts)
        mkdir_lines.append(f"open('{dest_path}','wb').close()")
    mkdir_code = "\n".join(mkdir_lines) + "\n"

    # Build a single chain: exec (mkdir+touch) then cp everything with -f.
    # -f skips the hash check on all files.  The hash check reads the entire
    # remote file back over serial to compute a local hash — slower than just
    # overwriting, since a deploy always implies something changed.
    chain: list[str] = ["mpremote", "connect", port, "+", "exec", mkdir_code]

    if deploy_settings:
        if not SETTINGS_PATH.exists():
            print(f"Error: {SETTINGS_PATH} not found", file=sys.stderr)
            return 1
        chain += ["+", "cp", "-f", str(SETTINGS_PATH), ":settings.toml"]

    chain += ["+", "cp", "-f", str(BOOT_PY), ":boot.py"]
    chain += ["+", "cp", "-f", str(source_file), ":code.py"]

    print(f"  Copying {len(py_files)} files ...")
    for f in py_files:
        rel = f.relative_to(LIB_SRC)
        chain += ["+", "cp", "-f", str(f), ":lib/featherweather/" + "/".join(rel.parts)]

    result = subprocess.run(chain)
    if result.returncode != 0:
        print("\nError: mpremote file transfer failed.", file=sys.stderr)
        return 1

    print(f"\nDeploy complete — {len(py_files)} library file(s) uploaded.")
    return 0


# ---------------------------------------------------------------------------
# USB deployment
# ---------------------------------------------------------------------------

_USB_CANDIDATE_ROOTS = [
    Path("/media") / getpass.getuser() / "CIRCUITPY",
    Path("/media/CIRCUITPY"),
    Path("/Volumes/CIRCUITPY"),           # macOS
    Path("D:/"),                          # Windows (common first guess)
]


def _find_circuitpy() -> Path | None:
    """Return the CIRCUITPY mount point or None if not found."""
    for p in _USB_CANDIDATE_ROOTS:
        if p.is_dir() and (p / "boot_out.txt").exists():
            return p
    return None


def deploy_usb(mount: Path | None, mode: str = "normal", deploy_settings: bool = False) -> int:
    if mount is None:
        mount = _find_circuitpy()

    if mount is None:
        checked = "\n  ".join(str(p) for p in _USB_CANDIDATE_ROOTS)
        print(
            "Error: CIRCUITPY drive not found. Checked:\n  " + checked,
            file=sys.stderr,
        )
        print(
            "Connect the device via USB and try again, or specify the mount "
            "point with --usb-path.",
            file=sys.stderr,
        )
        return 1

    if not mount.is_dir():
        print(f"Error: USB path {mount} does not exist or is not a directory.", file=sys.stderr)
        return 1

    source_file, mode_label = _resolve_source(mode)
    banner = _mode_banner(mode)

    print("=" * 60)
    print(f"Deploying via USB → {mount}")
    if banner:
        print(banner)
    print("=" * 60)

    step = 1
    total = 4 if deploy_settings else 3

    # 1. boot.py
    print(f"\n[{step}/{total}] Copying boot.py ...")
    if not BOOT_PY.exists():
        print(f"Error: {BOOT_PY} not found", file=sys.stderr)
        return 1
    shutil.copy2(BOOT_PY, mount / "boot.py")
    print(f"  copied  boot.py → {mount / 'boot.py'}")
    step += 1

    # 2. settings.toml (optional)
    if deploy_settings:
        print(f"\n[{step}/{total}] Copying settings.toml ...")
        if not SETTINGS_PATH.exists():
            print(f"Error: {SETTINGS_PATH} not found", file=sys.stderr)
            return 1
        shutil.copy2(SETTINGS_PATH, mount / "settings.toml")
        print(f"  copied  settings.toml → {mount / 'settings.toml'}")
        step += 1

    # 3. code.py
    print(f"\n[{step}/{total}] Copying {mode_label} ...")
    if not source_file.exists():
        print(f"Error: {source_file} not found", file=sys.stderr)
        return 1
    shutil.copy2(source_file, mount / "code.py")
    print(f"  copied  {source_file.name} → {mount / 'code.py'}")
    step += 1

    # 4. featherweather package → lib/featherweather/
    print(f"\n[{step}/{total}] Copying featherweather package ...")
    if not LIB_SRC.exists():
        print(f"Error: {LIB_SRC} not found", file=sys.stderr)
        return 1

    dest_lib = mount / "lib" / "featherweather"
    if dest_lib.exists():
        shutil.rmtree(dest_lib)
    shutil.copytree(LIB_SRC, dest_lib)

    py_count = len(list(dest_lib.rglob("*.py")))
    print(f"  copied  featherweather/ → {dest_lib}  ({py_count} files)")

    print(f"\nDeploy complete — eject {mount} safely before unplugging.")
    return 0


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(description="Test then deploy to CircuitPython device.")
    parser.add_argument(
        "--skip-tests",
        action="store_true",
        help="Deploy without running tests first",
    )
    parser.add_argument(
        "--settings",
        action="store_true",
        help="Also deploy settings.toml to the device (contains credentials — opt-in only)",
    )
    parser.add_argument(
        "--diagnostic",
        action="store_true",
        help="Deploy diagnostic.py as code.py (device runs self-test on next boot)",
    )
    parser.add_argument(
        "--serial",
        action="store_true",
        help=(
            "Deploy over USB-serial using mpremote (use when WiFi is unavailable). "
            "Required for Feather ESP32 V2 which has no USB mass-storage drive."
        ),
    )
    parser.add_argument(
        "--port",
        metavar="PORT",
        default=_DEFAULT_SERIAL_PORT,
        help=f"Serial port for --serial mode (default: {_DEFAULT_SERIAL_PORT})",
    )
    parser.add_argument(
        "--usb",
        action="store_true",
        help="Deploy over USB mass-storage CIRCUITPY drive (ESP32-S2/S3 only)",
    )
    parser.add_argument(
        "--usb-path",
        metavar="PATH",
        help="Explicit path to the CIRCUITPY mount point (implies --usb)",
    )
    args = parser.parse_args()

    use_serial = args.serial
    use_usb = args.usb or bool(args.usb_path)

    mode = "diagnostic" if args.diagnostic else "normal"

    # Lint + tests only matter for normal-mode code.py deploys; diagnostic is a
    # debug payload where running checks first is just friction.
    if not args.skip_tests and mode == "normal":
        if not run_lint():
            return 1
        if not run_tests():
            return 1

    if use_serial:
        return deploy_serial(args.port, mode=mode, deploy_settings=args.settings)

    if use_usb:
        mount = Path(args.usb_path) if args.usb_path else None
        return deploy_usb(mount, mode=mode, deploy_settings=args.settings)

    host, password = load_settings()
    return deploy_wifi(host, password, mode=mode, deploy_settings=args.settings)


if __name__ == "__main__":
    sys.exit(main())
