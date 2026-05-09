"""Pull files from the device filesystem to a local directory.

/sd/ paths — served by SdFileServer running in code.py on port 8080.
    No reboot needed. Works while the device is actively measuring.
    code.py must be running and connected to WiFi.

/  paths  — served by the CircuitPython built-in web workflow on port 80.
    Serves CIRCUITPY (internal flash) only — /sd/ returns 404 there.

SERIAL (--serial) — uses mpremote over USB.
    Interrupts code.py, so /sd/ is inaccessible. Use only for CIRCUITPY files.

Usage:
    poetry run fetch-sd                                # pull all of /sd/ (port 8080)
    poetry run fetch-sd --path /sd/                    # same, explicit
    poetry run fetch-sd --path /sd/diag_latest.txt     # single file
    poetry run fetch-sd --clear                        # fetch + delete files from /sd/
    poetry run fetch-sd --path /sd/diag_latest.txt --clear  # fetch + delete one file
    poetry run fetch-sd --out ./local_sd               # custom output directory
    poetry run fetch-sd --path /diag_report.txt        # CIRCUITPY file via web workflow
    poetry run fetch-sd --serial --path /code.py       # serial mode for CIRCUITPY files
"""

import argparse
import base64
import os
import subprocess
import sys
import tempfile
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
# Serial transport — single-exec approach
#
# mpremote interrupts code.py on connect, which unmounts the SD card.
# To access /sd we run ONE exec script on the device that mounts the SD card
# itself, walks the requested path, streams each file base64-encoded, and
# optionally deletes files — all within a single REPL session.
#
# SD wiring (ESP32 Feather V2 + Adalogger FeatherWing):
#   SPI bus : board.SCK / board.MOSI / board.MISO
#   CS pin  : board.D33
# ---------------------------------------------------------------------------

# Device-side script template.  Placeholders:
#   {remote_path!r}  — the device path requested (file or directory)
#   {clear_flag}     — "True" or "False"
_DEVICE_SCRIPT = """\
import os, binascii, board, busio, digitalio, storage
try:
    import adafruit_sdcard as _sd_mod
    _spi = busio.SPI(board.SCK, board.MOSI, board.MISO)
    _cs  = digitalio.DigitalInOut(board.D33)
    _sd  = _sd_mod.SDCard(_spi, _cs)
    _vfs = storage.VfsFat(_sd)
    storage.mount(_vfs, '/sd')
    _sd_mounted = True
except Exception as _e:
    print('MOUNT_ERROR:' + str(_e))
    _sd_mounted = False

_CLEAR = {clear_flag}
_ROOT  = {remote_path!r}

def _walk(p):
    try:
        names = os.listdir(p)
    except Exception:
        return
    for n in names:
        fp = p.rstrip('/') + '/' + n
        try:
            st = os.stat(fp)
            if st[0] & 0x4000:
                _walk(fp)
            else:
                yield fp, st[6]
        except Exception:
            pass

def _send(fpath, fsize):
    print('FILE:' + fpath + ':' + str(fsize))
    try:
        with open(fpath, 'rb') as _f:
            while True:
                chunk = _f.read(48)
                if not chunk:
                    break
                print(binascii.b2a_base64(chunk).decode().rstrip())
        if _CLEAR:
            try:
                os.remove(fpath)
                print('DELETED:' + fpath)
            except Exception as _e:
                print('DELETE_FAILED:' + fpath + ':' + str(_e))
    except Exception as _e:
        print('READ_ERROR:' + fpath + ':' + str(_e))
    print('END:' + fpath)

try:
    _st = os.stat(_ROOT)
    if _st[0] & 0x4000:
        for _fp, _fsz in _walk(_ROOT):
            _send(_fp, _fsz)
    else:
        _send(_ROOT, _st[6])
except Exception as _e:
    print('STAT_ERROR:' + str(_e))

if _sd_mounted:
    try:
        storage.umount('/sd')
        _sd.deinit()
        _cs.deinit()
    except Exception:
        pass
print('DONE')
"""


def _run_script_on_device(port: str, script: str) -> str:
    """Write script to a temp file and run it on the device via mpremote run.

    mpremote run is more reliable than exec for multi-line scripts — it uploads
    the file content to the raw REPL in chunks rather than passing a raw string
    on the command line, which avoids 'could not enter raw repl' failures.
    """
    tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False)
    try:
        tmp.write(script)
        tmp.close()
        result = subprocess.run(
            ["mpremote", "connect", port, "run", tmp.name],
            capture_output=True, text=True, timeout=120,
        )
        if result.returncode != 0:
            raise RuntimeError(result.stderr or result.stdout)
        return result.stdout
    finally:
        os.unlink(tmp.name)


def _parse_device_output(output: str, out_dir: Path, clear: bool) -> tuple[int, int]:
    """Parse the streamed base64 output from the device script into local files."""
    copied = failed = 0
    current_path: str | None = None
    current_chunks: list[str] = []
    current_size = 0

    for raw_line in output.splitlines():
        line = raw_line.strip()

        if line.startswith("MOUNT_ERROR:"):
            print(f"  SD mount error: {line[len('MOUNT_ERROR:'):]}", file=sys.stderr)
            failed += 1

        elif line.startswith("FILE:"):
            # FILE:/sd/diag_latest.txt:1234
            parts = line[5:].rsplit(":", 1)
            current_path = parts[0]
            current_size = int(parts[1]) if len(parts) == 2 and parts[1].isdigit() else 0
            current_chunks = []

        elif line.startswith("END:") and current_path:
            # Reconstruct and save the file
            rel = current_path.lstrip("/")
            local = out_dir / rel
            local.parent.mkdir(parents=True, exist_ok=True)
            try:
                raw = b"".join(base64.b64decode(c) for c in current_chunks)
                local.write_bytes(raw)
                suffix = ""
                if clear:
                    # deletion already happened on-device; we learn from DELETED: lines
                    suffix = "  [deleted on device]"
                print(f"  {current_path}  →  {local}  ({current_size} B){suffix}")
                copied += 1
            except Exception as exc:
                print(f"  FAILED decoding {current_path}: {exc}", file=sys.stderr)
                failed += 1
            current_path = None
            current_chunks = []

        elif line.startswith("DELETED:") or line.startswith("DELETE_FAILED:"):
            pass  # already shown via the END: suffix; ignore duplicate noise

        elif line.startswith("READ_ERROR:") or line.startswith("STAT_ERROR:"):
            print(f"  device error: {line}", file=sys.stderr)
            failed += 1
            current_path = None

        elif line == "DONE" or not line:
            pass

        elif current_path is not None:
            # base64 chunk
            current_chunks.append(line)

    return copied, failed


def fetch_serial(port: str, remote_path: str, out_dir: Path, clear: bool = False) -> int:
    out_dir.mkdir(parents=True, exist_ok=True)
    remote_path = remote_path if remote_path.startswith("/") else "/" + remote_path

    print(f"Serial  {port}  →  {remote_path}")
    print(f"Output  {out_dir}")
    if clear:
        print("Mode    fetch + clear (files deleted from device after copy)")
    print()

    # For stat/walk, the device script needs the path without trailing slash
    # (os.stat('/sd/') can fail on CircuitPython; os.stat('/sd') is fine)
    script = _DEVICE_SCRIPT.format(
        remote_path=remote_path.rstrip("/") or "/sd",
        clear_flag="True" if clear else "False",
    )

    try:
        output = _run_script_on_device(port, script)
    except RuntimeError as exc:
        print(f"  mpremote error: {exc}", file=sys.stderr)
        return 1

    copied, failed = _parse_device_output(output, out_dir, clear)
    print(f"\n{copied} file(s) saved, {failed} failed.")
    return 0 if failed == 0 else 1


# ---------------------------------------------------------------------------
# WiFi transport — CIRCUITPY only (SD card not served by web workflow)
# ---------------------------------------------------------------------------


def _http_get(url: str, auth: dict[str, str]) -> bytes:
    req = urllib.request.Request(url, headers=auth)
    with urllib.request.urlopen(req, timeout=10) as resp:
        return resp.read()


def _list_remote_wifi(host: str, auth: dict[str, str], path: str) -> list[dict]:
    """Return file entries listed at path via web workflow JSON API."""
    import json  # noqa: PLC0415
    url = f"http://{host}/fs/{path.strip('/')}/"
    req = urllib.request.Request(url, headers={**auth, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        data = json.loads(resp.read())
    return data.get("files", [])


def _delete_remote_wifi(host: str, auth: dict[str, str], path: str) -> bool:
    """Delete a file on the device via HTTP DELETE."""
    url = f"http://{host}/fs/{path.lstrip('/')}"
    req = urllib.request.Request(url, headers=auth, method="DELETE")
    try:
        with urllib.request.urlopen(req, timeout=10):
            return True
    except Exception:
        return False


def _fetch_tree_wifi(
    host: str, auth: dict[str, str], remote_path: str,
    local_base: Path, rel: str = "", clear: bool = False,
) -> tuple[int, int]:
    copied = failed = 0
    try:
        entries = _list_remote_wifi(host, auth, remote_path)
    except Exception as exc:  # noqa: BLE001
        print(f"  error listing {remote_path}: {exc}", file=sys.stderr)
        return 0, 1

    for entry in entries:
        name   = entry.get("name", "")
        is_dir = entry.get("directory", False)
        sub_path = remote_path.rstrip("/") + "/" + name
        local    = local_base / rel / name if rel else local_base / name
        if is_dir:
            c, f = _fetch_tree_wifi(
                host, auth, sub_path + "/", local_base,
                (rel + "/" + name).lstrip("/"), clear=clear,
            )
            copied += c
            failed += f
        else:
            url = f"http://{host}/fs/{sub_path.lstrip('/')}"
            try:
                content = _http_get(url, auth)
                local.parent.mkdir(parents=True, exist_ok=True)
                local.write_bytes(content)
                suffix = ""
                if clear:
                    deleted = _delete_remote_wifi(host, auth, sub_path)
                    suffix = "  [deleted]" if deleted else "  [delete FAILED]"
                print(f"  {sub_path}  →  {local}  ({len(content)} B){suffix}")
                copied += 1
            except Exception as exc:  # noqa: BLE001
                print(f"  FAILED {url}: {exc}", file=sys.stderr)
                failed += 1
    return copied, failed


def _sd_server_url(host: str, path: str) -> str:
    """Build URL for the SdFileServer (port 8080) from an /sd/... path."""
    # path starts with /sd — server expects /sd/...
    return f"http://{host}:8080{path}"


def _web_workflow_url(host: str, path: str) -> str:
    """Build URL for the CircuitPython web workflow (port 80, /fs/...) from a path."""
    return f"http://{host}/fs/{path.lstrip('/')}"


def fetch_wifi(remote_path: str, out_dir: Path, clear: bool = False) -> int:
    """Fetch files over WiFi.

    /sd/ paths  → SdFileServer on port 8080 (runs inside code.py, no reboot needed)
    other paths → CircuitPython web workflow on port 80 (CIRCUITPY files only)
    """
    host, password = _load_settings()
    auth = _auth_header(password)
    out_dir.mkdir(parents=True, exist_ok=True)

    use_sd_server = remote_path.rstrip("/").startswith("/sd")
    is_dir = remote_path.endswith("/") or remote_path == "/sd"

    if use_sd_server:
        base_url = f"http://{host}:8080"
        url_for  = lambda p: f"{base_url}{p}"          # noqa: E731
        del_for  = lambda p: _delete_remote_wifi_url(f"{base_url}{p}", {})  # noqa: E731
        print(f"WiFi    {base_url}{remote_path}")
    else:
        auth_hdr = auth
        url_for  = lambda p: _web_workflow_url(host, p)    # noqa: E731
        print(f"WiFi    http://{host}/fs/{remote_path.lstrip('/')}")

    print(f"Output  {out_dir}")
    if clear:
        print("Mode    fetch + clear (files deleted from device after copy)")
    print()

    if is_dir:
        copied, failed = _fetch_tree_sd_server(host, auth, remote_path, out_dir, clear, use_sd_server)
    else:
        url = url_for(remote_path) if use_sd_server else _web_workflow_url(host, remote_path)
        try:
            content = _http_get(url, auth if not use_sd_server else {})
            rel = remote_path.lstrip("/")
            local = out_dir / Path(rel).name
            local.parent.mkdir(parents=True, exist_ok=True)
            local.write_bytes(content)
            suffix = ""
            if clear:
                if use_sd_server:
                    deleted = _delete_sd_server(host, remote_path)
                else:
                    deleted = _delete_remote_wifi(host, auth, remote_path)
                suffix = "  [deleted]" if deleted else "  [delete FAILED]"
            print(f"  {remote_path}  →  {local}  ({len(content)} B){suffix}")
            copied, failed = 1, 0
        except urllib.error.HTTPError as exc:
            print(f"  HTTP {exc.code}: {url}", file=sys.stderr)
            copied, failed = 0, 1
        except Exception as exc:  # noqa: BLE001
            print(f"  error: {exc}", file=sys.stderr)
            copied, failed = 0, 1

    print(f"\n{copied} file(s) saved, {failed} failed.")
    return 0 if failed == 0 else 1


def _delete_sd_server(host: str, path: str) -> bool:
    """HTTP DELETE to the SdFileServer on port 8080."""
    url = f"http://{host}:8080{path}"
    req = urllib.request.Request(url, method="DELETE")
    try:
        with urllib.request.urlopen(req, timeout=10):
            return True
    except Exception:
        return False


def _list_sd_server(host: str, path: str) -> list[dict]:
    """GET directory listing from SdFileServer (returns same JSON as web workflow)."""
    import json  # noqa: PLC0415
    url = f"http://{host}:8080{path.rstrip('/')}/"
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read()).get("files", [])


def _fetch_tree_sd_server(
    host: str, auth: dict[str, str], remote_path: str,
    local_base: Path, clear: bool, use_sd_server: bool,
    rel: str = "",
) -> tuple[int, int]:
    copied = failed = 0
    try:
        if use_sd_server:
            entries = _list_sd_server(host, remote_path)
        else:
            entries = _list_remote_wifi(host, auth, remote_path)
    except Exception as exc:  # noqa: BLE001
        print(f"  error listing {remote_path}: {exc}", file=sys.stderr)
        return 0, 1

    for entry in entries:
        name   = entry.get("name", "")
        is_dir = entry.get("directory", False)
        sub    = remote_path.rstrip("/") + "/" + name
        local  = local_base / rel / name if rel else local_base / name
        if is_dir:
            c, f = _fetch_tree_sd_server(
                host, auth, sub + "/", local_base, clear, use_sd_server,
                (rel + "/" + name).lstrip("/"),
            )
            copied += c
            failed += f
        else:
            if use_sd_server:
                url = f"http://{host}:8080{sub}"
                fetch_auth = {}
            else:
                url = _web_workflow_url(host, sub)
                fetch_auth = auth
            try:
                content = _http_get(url, fetch_auth)
                local.parent.mkdir(parents=True, exist_ok=True)
                local.write_bytes(content)
                suffix = ""
                if clear:
                    deleted = _delete_sd_server(host, sub) if use_sd_server \
                              else _delete_remote_wifi(host, auth, sub)
                    suffix = "  [deleted]" if deleted else "  [delete FAILED]"
                print(f"  {sub}  →  {local}  ({len(content)} B){suffix}")
                copied += 1
            except Exception as exc:  # noqa: BLE001
                print(f"  FAILED {url}: {exc}", file=sys.stderr)
                failed += 1
    return copied, failed


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def _has_wifi_settings() -> bool:
    """Return True if settings.toml contains a valid ESP32_IP."""
    if not SETTINGS_PATH.exists():
        return False
    try:
        try:
            import tomllib  # noqa: PLC0415
        except ImportError:
            import tomli as tomllib  # noqa: PLC0415
        with open(SETTINGS_PATH, "rb") as f:
            s = tomllib.load(f)
        return bool(s.get("ESP32_IP", "").strip())
    except Exception:  # noqa: BLE001
        return False


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Pull files from the device filesystem to a local directory.\n\n"
            "WiFi mode (default when ESP32_IP is set in settings.toml) accesses\n"
            "/sd/ via the CircuitPython web workflow while code.py is running.\n"
            "Serial mode (--serial) interrupts code.py and cannot reach /sd/."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
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
    transport = parser.add_mutually_exclusive_group()
    transport.add_argument(
        "--wifi",
        action="store_true",
        help=(
            "Force WiFi mode — requires code.py running and ESP32_IP in settings.toml. "
            "Supports /sd/ because code.py has it mounted."
        ),
    )
    transport.add_argument(
        "--serial",
        action="store_true",
        help=(
            "Force serial mode via mpremote. Interrupts code.py on connect so "
            "/sd/ is not accessible. Use only for CIRCUITPY root files."
        ),
    )
    parser.add_argument(
        "--port",
        metavar="PORT",
        default=_DEFAULT_PORT,
        help=f"Serial port (default: {_DEFAULT_PORT}) — serial mode only",
    )
    parser.add_argument(
        "--clear",
        action="store_true",
        help=(
            "Delete each file from the device after it is successfully copied locally. "
            "Supported in both WiFi and serial modes. Directories are left in place."
        ),
    )
    args = parser.parse_args()

    use_wifi = args.wifi or (not args.serial and _has_wifi_settings())

    if use_wifi:
        return fetch_wifi(args.path, Path(args.out), clear=args.clear)
    return fetch_serial(args.port, args.path, Path(args.out), clear=args.clear)


if __name__ == "__main__":
    sys.exit(main())
