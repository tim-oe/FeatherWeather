"""Pull files from the device filesystem to a local directory.

Uses mpremote over USB-serial (default) or the CircuitPython Web Workflow
HTTP API (--wifi) to copy files from the device to a local directory.

The CircuitPython web workflow only serves the CIRCUITPY root filesystem;
files on the SD card mounted at /sd are not visible there.  The serial
transport (mpremote) accesses the full VFS including /sd, making it the
reliable option for retrieving SD card data.

Usage:
    poetry run fetch-sd                           # pull all of /sd/ via serial
    poetry run fetch-sd --path /sd/               # same, explicit path
    poetry run fetch-sd --path /sd/diag_latest.txt     # single file
    poetry run fetch-sd --path /                   # pull all of CIRCUITPY
    poetry run fetch-sd --out ./local_sd           # custom output directory
    poetry run fetch-sd --port /dev/ttyACM1        # non-default serial port
    poetry run fetch-sd --wifi                     # WiFi pull (CIRCUITPY only)
    poetry run fetch-sd --wifi --path code.py      # single file via WiFi
"""

import argparse
import base64
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

REPO_ROOT     = Path(__file__).resolve().parent.parent
SETTINGS_PATH = REPO_ROOT / "settings.toml"

_DEFAULT_PORT       = "/dev/ttyACM0"
_DEFAULT_OUT_DIR    = REPO_ROOT / "device_files"
_DEFAULT_DEVICE_PATH = "/sd/"


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------


def _load_settings() -> tuple[str, str]:
    if not SETTINGS_PATH.exists():
        print(
            f"Error: {SETTINGS_PATH} not found — copy settings.toml.example and fill it in",
            file=sys.stderr,
        )
        sys.exit(1)
    try:
        import tomllib  # noqa: PLC0415
    except ImportError:
        import tomli as tomllib  # noqa: PLC0415
    with open(SETTINGS_PATH, "rb") as f:
        settings = tomllib.load(f)
    host = settings.get("ESP32_IP", "").strip()
    if not host:
        print("Error: ESP32_IP is not set in settings.toml", file=sys.stderr)
        sys.exit(1)
    return host, settings.get("CIRCUITPY_WEB_API_PASSWORD", "").strip()


def _auth_header(password: str) -> dict[str, str]:
    token = base64.b64encode(f":{password}".encode()).decode()
    return {"Authorization": f"Basic {token}"}


# ---------------------------------------------------------------------------
# Serial transport (mpremote) — works for /sd/ and CIRCUITPY
# ---------------------------------------------------------------------------


def _run(cmd: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True)


def _list_remote(port: str, remote_path: str) -> list[tuple[str, int]]:
    """Return [(name, size), ...] for entries at remote_path via mpremote ls."""
    result = _run(["mpremote", "connect", port, "ls", f":{remote_path}"])
    if result.returncode != 0:
        raise RuntimeError(result.stderr or result.stdout)
    entries = []
    for line in result.stdout.splitlines():
        parts = line.strip().split()
        if not parts:
            continue
        # mpremote ls format: "     <size> <name>"  or  "         <name>/"
        name = parts[-1].rstrip("/")
        size = int(parts[0]) if len(parts) >= 2 and parts[0].isdigit() else 0
        is_dir = parts[-1].endswith("/")
        entries.append((name, size, is_dir))
    return entries


def _copy_file(port: str, remote_path: str, local_path: Path) -> bool:
    local_path.parent.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        ["mpremote", "connect", port, "cp", f":{remote_path}", str(local_path)],
    )
    return result.returncode == 0


def _fetch_tree(port: str, remote_path: str, local_base: Path, rel: str = "") -> tuple[int, int]:
    """Recursively copy remote_path to local_base/rel. Returns (copied, failed)."""
    copied = failed = 0
    remote_path = remote_path.rstrip("/") + "/"
    try:
        entries = _list_remote(port, remote_path)
    except RuntimeError as exc:
        print(f"  error listing {remote_path}: {exc}", file=sys.stderr)
        return 0, 1

    for name, size, is_dir in entries:
        remote_item = remote_path + name
        local_item  = local_base / rel / name if rel else local_base / name
        if is_dir:
            c, f = _fetch_tree(port, remote_item + "/", local_base, (rel + "/" + name).lstrip("/"))
            copied += c
            failed += f
        else:
            ok = _copy_file(port, remote_item, local_item)
            if ok:
                print(f"  {remote_item}  →  {local_item}  ({size} B)")
                copied += 1
            else:
                print(f"  FAILED: {remote_item}", file=sys.stderr)
                failed += 1
    return copied, failed


def fetch_serial(port: str, remote_path: str, out_dir: Path) -> int:
    out_dir.mkdir(parents=True, exist_ok=True)
    remote_path = remote_path if remote_path.startswith("/") else "/" + remote_path
    is_dir = remote_path.endswith("/")

    print(f"Serial  {port}  →  {remote_path}")
    print(f"Output  {out_dir}\n")

    if is_dir:
        copied, failed = _fetch_tree(port, remote_path, out_dir)
    else:
        # Single file
        name = Path(remote_path).name
        local = out_dir / name
        ok = _copy_file(port, remote_path, local)
        copied, failed = (1, 0) if ok else (0, 1)
        if ok:
            print(f"  {remote_path}  →  {local}")
        else:
            print(f"  FAILED: {remote_path}", file=sys.stderr)

    print(f"\n{copied} file(s) saved, {failed} failed.")
    return 0 if failed == 0 else 1


# ---------------------------------------------------------------------------
# WiFi transport — CIRCUITPY only (SD card not served by web workflow)
# ---------------------------------------------------------------------------


def _http_get(url: str, auth: dict[str, str]) -> bytes:
    req = urllib.request.Request(url, headers=auth)
    with urllib.request.urlopen(req, timeout=10) as resp:
        return resp.read()


def _list_remote_wifi(host: str, auth: dict[str, str], path: str) -> list[str]:
    """Return file/dir names listed at path via web workflow JSON API."""
    import json  # noqa: PLC0415
    url = f"http://{host}/fs/{path.strip('/')}/"
    req = urllib.request.Request(url, headers={**auth, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=10) as resp:
        data = json.loads(resp.read())
    # Response is {"files": [{"name": ..., "directory": bool}, ...]}
    return data.get("files", [])


def _fetch_tree_wifi(host: str, auth: dict[str, str], remote_path: str, local_base: Path, rel: str = "") -> tuple[int, int]:
    copied = failed = 0
    try:
        entries = _list_remote_wifi(host, auth, remote_path)
    except Exception as exc:  # noqa: BLE001
        print(f"  error listing {remote_path}: {exc}", file=sys.stderr)
        return 0, 1

    for entry in entries:
        name     = entry.get("name", "")
        is_dir   = entry.get("directory", False)
        sub_path = remote_path.rstrip("/") + "/" + name
        local    = local_base / rel / name if rel else local_base / name
        if is_dir:
            c, f = _fetch_tree_wifi(host, auth, sub_path + "/", local_base, (rel + "/" + name).lstrip("/"))
            copied += c
            failed += f
        else:
            url = f"http://{host}/fs/{sub_path.lstrip('/')}"
            try:
                content = _http_get(url, auth)
                local.parent.mkdir(parents=True, exist_ok=True)
                local.write_bytes(content)
                print(f"  {sub_path}  →  {local}  ({len(content)} B)")
                copied += 1
            except Exception as exc:  # noqa: BLE001
                print(f"  FAILED {url}: {exc}", file=sys.stderr)
                failed += 1
    return copied, failed


def fetch_wifi(remote_path: str, out_dir: Path) -> int:
    host, password = _load_settings()
    auth = _auth_header(password)
    out_dir.mkdir(parents=True, exist_ok=True)
    remote_path = remote_path.lstrip("/")
    is_dir = remote_path == "" or remote_path.endswith("/")

    print(f"WiFi  http://{host}/fs/{remote_path}")
    print(f"Output  {out_dir}")
    print("Note: web workflow only serves CIRCUITPY — /sd/ files are not available via WiFi.\n")

    if is_dir:
        copied, failed = _fetch_tree_wifi(host, auth, remote_path or "/", out_dir)
    else:
        url = f"http://{host}/fs/{remote_path}"
        try:
            content = _http_get(url, auth)
            local = out_dir / Path(remote_path).name
            local.write_bytes(content)
            print(f"  {remote_path}  →  {local}  ({len(content)} B)")
            copied, failed = 1, 0
        except urllib.error.HTTPError as exc:
            print(f"  HTTP {exc.code}: {url}", file=sys.stderr)
            copied, failed = 0, 1
        except Exception as exc:  # noqa: BLE001
            print(f"  error: {exc}", file=sys.stderr)
            copied, failed = 0, 1

    print(f"\n{copied} file(s) saved, {failed} failed.")
    return 0 if failed == 0 else 1


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Pull files from the device filesystem to a local directory."
    )
    parser.add_argument(
        "--path",
        metavar="DEVICE_PATH",
        default=_DEFAULT_DEVICE_PATH,
        help=(
            f"Device path to pull (default: {_DEFAULT_DEVICE_PATH!r}). "
            "Trailing / means directory (recursive). "
            "Examples: /sd/  /sd/diag_latest.txt  /"
        ),
    )
    parser.add_argument(
        "--out",
        metavar="DIR",
        default=str(_DEFAULT_OUT_DIR),
        help=f"Local directory to save files into (default: {_DEFAULT_OUT_DIR})",
    )
    parser.add_argument(
        "--wifi",
        action="store_true",
        help="Pull via WiFi web workflow instead of serial (CIRCUITPY only — no /sd/)",
    )
    parser.add_argument(
        "--port",
        metavar="PORT",
        default=_DEFAULT_PORT,
        help=f"Serial port for serial mode (default: {_DEFAULT_PORT})",
    )
    args = parser.parse_args()

    if args.wifi:
        return fetch_wifi(args.path, Path(args.out))
    return fetch_serial(args.port, args.path, Path(args.out))


if __name__ == "__main__":
    sys.exit(main())
