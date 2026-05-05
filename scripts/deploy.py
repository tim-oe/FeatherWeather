"""Run tests, then deploy code to the CircuitPython device over WiFi.

Uses the CircuitPython Web Workflow REST API (HTTP PUT) to upload files:
    code.py              → http://<ESP32_IP>/fs/code.py
    src/featherweather/  → http://<ESP32_IP>/fs/lib/featherweather/

The CIRCUITPY_WEB_API_PASSWORD from settings.toml is sent as HTTP Basic Auth
(empty username, password as configured on the device).

Usage:
    poetry run deploy
    python scripts/deploy.py
    python scripts/deploy.py --skip-tests   # deploy without running tests
    python scripts/deploy.py --diagnostic   # deploy diagnostic.py as code.py
"""

import argparse
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
# Deployment logic
# ---------------------------------------------------------------------------


def deploy(host: str, password: str, diagnostic: bool = False) -> int:
    auth = _auth_header(password)

    source_file = DIAGNOSTIC_PY if diagnostic else CODE_PY
    mode_label = "diagnostic.py (as code.py)" if diagnostic else "code.py"

    print("=" * 60)
    print(f"Deploying to {host}")
    if diagnostic:
        print("Mode: DIAGNOSTIC — device will run self-test on next boot")
    print("=" * 60)

    # 1. Upload boot.py
    print("\n[1/3] Uploading boot.py ...")
    if not BOOT_PY.exists():
        print(f"Error: {BOOT_PY} not found", file=sys.stderr)
        return 1
    upload_file(host, "boot.py", BOOT_PY, auth)

    # 2. Upload code.py (or diagnostic.py renamed to code.py)
    print(f"\n[2/3] Uploading {mode_label} ...")
    if not source_file.exists():
        print(f"Error: {source_file} not found", file=sys.stderr)
        return 1
    upload_file(host, "code.py", source_file, auth)

    # 3. Upload the featherweather package to /lib/featherweather/
    print("\n[3/3] Uploading featherweather package ...")
    if not LIB_SRC.exists():
        print(f"Error: {LIB_SRC} not found", file=sys.stderr)
        return 1

    # Collect all .py files and the unique directories they live in.
    py_files = sorted(LIB_SRC.rglob("*.py"))
    dirs_needed: list[str] = []
    for f in py_files:
        rel = f.relative_to(LIB_SRC)
        parts = rel.parts[:-1]  # directory components only
        for depth in range(len(parts) + 1):
            candidate = "lib/featherweather/" + "/".join(parts[:depth])
            if candidate not in dirs_needed:
                dirs_needed.append(candidate)

    # Ensure all remote directories exist before uploading files.
    for d in dirs_needed:
        mkdir_remote(host, d, auth)

    for f in py_files:
        rel = f.relative_to(LIB_SRC)
        remote = "lib/featherweather/" + "/".join(rel.parts)
        upload_file(host, remote, f, auth)

    print(f"\nDeploy complete — {len(py_files)} library file(s) uploaded.")
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
        "--diagnostic",
        action="store_true",
        help="Deploy diagnostic.py as code.py (device runs self-test on next boot)",
    )
    args = parser.parse_args()

    host, password = load_settings()

    if not args.skip_tests and not args.diagnostic:
        if not run_tests():
            return 1

    return deploy(host, password, diagnostic=args.diagnostic)


if __name__ == "__main__":
    sys.exit(main())
