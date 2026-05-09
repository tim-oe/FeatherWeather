"""Minimal non-blocking HTTP file server for the /sd/ mount point.

Runs inside the code.py main loop via poll().  A single listening socket
accepts one connection at a time, reads the HTTP request, and streams the
requested file back — no threading, no extra libraries required.

Supported requests (from fetch_sd.py or a browser):
    GET  /sd/<path>      — download a file from /sd/
    GET  /sd/            — list files as JSON (same format as web workflow)
    DELETE /sd/<path>    — delete a file from /sd/

Port: 8080 (configurable).

Usage in code.py:
    from featherweather.storage.sd_file_server import SdFileServer
    sd_server = SdFileServer(wifi.radio)

    # in the main loop:
    sd_server.poll()
"""

import json
import os

import socketpool
import wifi

__all__ = ["SdFileServer"]

_SD_ROOT = "/sd"
_CHUNK = 1024  # bytes per send() call — keep small to avoid blocking too long


class SdFileServer:
    """Non-blocking HTTP file server that exposes /sd/ over WiFi.

    Call poll() on every main-loop iteration.  Each call does at most one
    non-blocking accept() and, if a connection is waiting, handles the full
    request synchronously (the request + response are small so this is fast).

    Args:
        radio:  wifi.radio instance (already connected in code.py)
        port:   TCP port to listen on (default 8080)
    """

    def __init__(self, radio, port: int = 8080) -> None:
        self._pool = socketpool.SocketPool(radio)
        self._server = self._pool.socket(self._pool.AF_INET, self._pool.SOCK_STREAM)
        self._server.setsockopt(self._pool.SOL_SOCKET, self._pool.SO_REUSEADDR, 1)
        self._server.bind(("0.0.0.0", port))
        self._server.listen(1)
        self._server.setblocking(False)
        ip = str(radio.ipv4_address)
        self._check_sd()
        print(f"[sd-server] listening on http://{ip}:{port}/sd/")

    @staticmethod
    def _check_sd() -> None:
        """Verify /sd is mounted and log what's there — same logic as diagnostic._verify_sd."""
        try:
            entries = os.listdir(_SD_ROOT)
            print(f"[sd-server] /sd OK — {len(entries)} file(s): {entries}")
        except OSError as exc:
            print(f"[sd-server] WARNING: os.listdir('{_SD_ROOT}') failed: {exc}")
            print("[sd-server] SD card may not be mounted in this VM.")

    def poll(self) -> None:
        """Accept and handle one pending connection, or return immediately."""
        try:
            conn, _addr = self._server.accept()
        except OSError:
            return  # EAGAIN — no connection waiting, that's fine

        try:
            conn.setblocking(True)
            conn.settimeout(2.0)
            self._handle(conn)
        except Exception as exc:  # noqa: BLE001
            print(f"[sd-server] error: {exc}")
        finally:
            conn.close()

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _handle(self, conn) -> None:
        buf = bytearray(1024)
        try:
            n = conn.recv_into(buf)
        except OSError:
            return
        if not n:
            return

        request = str(buf[:n], "utf-8", "replace")
        lines = request.split("\r\n")
        if not lines:
            return
        parts = lines[0].split(" ")
        if len(parts) < 2:
            return
        method = parts[0].upper()
        raw_path = parts[1]

        # Strip query string
        path = raw_path.split("?")[0]

        # Detect if the caller wants JSON (fetch-sd) or HTML (browser)
        want_json = any(
            "application/json" in line
            for line in lines
            if line.lower().startswith("accept:")
        )

        if method == "GET":
            if path == "/" or path == "":
                self._send_redirect(conn, "/sd/")
            elif path == "/debug":
                self._serve_debug(conn)
            elif path.endswith("/"):
                if want_json:
                    self._serve_listing_json(conn, path)
                else:
                    self._serve_listing_html(conn, path)
            else:
                self._serve_file(conn, path)
        elif method == "DELETE":
            self._delete_file(conn, path)
        else:
            self._send_header(conn, 405, "Method Not Allowed")

    def _fs_path(self, url_path: str) -> str:
        """Convert a URL path like /sd/foo/ to a filesystem path like /sd/foo."""
        # url_path starts with /sd; strip that prefix to get the sub-path
        sub = url_path[len("/sd") :].rstrip("/")
        return (_SD_ROOT + sub) if sub else _SD_ROOT

    def _serve_debug(self, conn) -> None:
        """Diagnostic page — shows mount status and raw os.listdir output."""
        lines = []
        # Test direct listdir
        for path in (_SD_ROOT, "/", "/sd"):
            try:
                entries = os.listdir(path)
                lines.append(f"os.listdir({path!r}) = {entries}")
            except OSError as exc:
                lines.append(f"os.listdir({path!r}) ERROR: {exc}")
        # statvfs
        try:
            st = os.statvfs(_SD_ROOT)
            total_mb = st[1] * st[2] / 1024 / 1024
            free_mb = st[0] * st[3] / 1024 / 1024
            lines.append(f"statvfs /sd: {total_mb:.1f} MB total, {free_mb:.1f} MB free")
        except OSError as exc:
            lines.append(f"statvfs /sd ERROR: {exc}")

        body = "<br>".join(lines)
        html = (
            f"<!DOCTYPE html><html><head><meta charset=utf-8><title>SD debug</title>"
            f"<style>body{{font-family:monospace;padding:1em}}</style></head>"
            f"<body><h2>SD Server Debug</h2>{body}</body></html>"
        ).encode()
        self._send_header(conn, 200, "OK", len(html), "text/html")
        conn.sendall(html)

    def _list_entries(self, fs_path: str):
        """Return [(name, is_dir, size), ...] for a directory."""
        try:
            names = sorted(os.listdir(fs_path))
            print(f"[sd-server] list {fs_path!r}: {len(names)} entries")
        except OSError as exc:
            print(f"[sd-server] list {fs_path!r} ERROR: {exc}")
            return None
        entries = []
        for name in names:
            full = fs_path.rstrip("/") + "/" + name
            try:
                st = os.stat(full)
                is_dir = bool(st[0] & 0x4000)
                size = st[6]
            except OSError:
                is_dir = False
                size = 0
            entries.append((name, is_dir, size))
        return entries

    def _serve_listing_json(self, conn, path: str) -> None:
        """JSON listing — for fetch-sd (Accept: application/json)."""
        entries = self._list_entries(self._fs_path(path))
        if entries is None:
            self._send_header(conn, 404, "Not Found")
            return
        files = [{"name": n, "directory": d} for n, d, _ in entries]
        body = json.dumps({"files": files}).encode()
        self._send_header(conn, 200, "OK", len(body), "application/json")
        conn.sendall(body)

    @staticmethod
    def _fmt_bytes(b):
        if b >= 1073741824:
            return str(round(b / 1073741824, 1)) + " GB"
        if b >= 1048576:
            return str(round(b / 1048576, 1)) + " MB"
        return str(round(b / 1024, 1)) + " KB"

    def _disk_stats(self):
        try:
            st = os.statvfs(_SD_ROOT)
            bsize = st[0]
            total_b = bsize * st[2]
            free_b = bsize * st[3]
            used_b = total_b - free_b
            return (
                self._fmt_bytes(used_b)
                + " used / "
                + self._fmt_bytes(free_b)
                + " free / "
                + self._fmt_bytes(total_b)
                + " total"
            )
        except OSError:
            return "disk stats unavailable"

    def _serve_listing_html(self, conn, path):
        fs_path = self._fs_path(path)
        entries = self._list_entries(fs_path)
        if entries is None:
            self._send_header(conn, 404, "Not Found")
            return

        disk = self._disk_stats()

        rows = []
        if path.rstrip("/") != "/sd":
            parent = path.rstrip("/").rsplit("/", 1)[0] + "/"
            rows.append(
                '<tr><td colspan="3"><a href="' + parent + '">../</a></td></tr>'
            )

        for name, is_dir, size in entries:
            href = path.rstrip("/") + "/" + name + ("/" if is_dir else "")
            label = name + ("/" if is_dir else "")
            sz = "&mdash;" if is_dir else str(size) + " B"
            if is_dir:
                del_td = "<td></td>"
            else:
                del_td = (
                    "<td><button onclick=\"rmFile('" + href + "')\""
                    ' style="color:red;background:none;border:1px solid red;'
                    'border-radius:3px;cursor:pointer;padding:1px 6px">'
                    "del</button></td>"
                )
            rows.append(
                '<tr><td><a href="'
                + href
                + '">'
                + label
                + "</a></td>"
                + '<td style="text-align:right">'
                + sz
                + "</td>"
                + del_td
                + "</tr>"
            )

        _CSS = (
            "body{font-family:monospace;padding:1em;max-width:800px}"
            "h2{margin-bottom:0.2em}"
            "p.s{color:#666;font-size:0.85em;margin:0 0 1em}"
            "table{border-collapse:collapse;width:100%}"
            "td{padding:3px 10px;vertical-align:middle}"
            "a{text-decoration:none}"
        )
        _JS = (
            "function rmFile(p){"
            "if(!confirm('Delete '+p+'?'))return;"
            "fetch(p,{method:'DELETE'}).then(function(r){"
            "r.ok?location.reload():alert('Failed: '+r.status);});}"
        )
        parts = [
            "<!DOCTYPE html><html><head><meta charset=utf-8>",
            "<title>SD: ",
            path,
            "</title>",
            "<style>",
            _CSS,
            "</style>",
            "<script>",
            _JS,
            "</script>",
            "</head><body>",
            "<h2>SD card &#8212; ",
            path,
            "</h2>",
            '<p class="s">',
            disk,
            "</p>",
            "<table>",
            "\n".join(rows),
            "</table>",
            "</body></html>",
        ]
        body = "".join(parts).encode()
        self._send_header(conn, 200, "OK", len(body), "text/html")
        conn.sendall(body)

    def _serve_file(self, conn, path: str) -> None:
        """Stream a file from /sd/."""
        fs_path = self._fs_path(path)
        try:
            st = os.stat(fs_path)
            size = st[6]
        except OSError:
            self._send_header(conn, 404, "Not Found")
            return

        self._send_header(conn, 200, "OK", size, "application/octet-stream")
        try:
            with open(fs_path, "rb") as f:
                while True:
                    chunk = f.read(_CHUNK)
                    if not chunk:
                        break
                    conn.sendall(chunk)
        except OSError as exc:
            print(f"[sd-server] read error {fs_path}: {exc}")

    def _delete_file(self, conn, path: str) -> None:
        """Delete a file from /sd/."""
        fs_path = self._fs_path(path)
        try:
            os.remove(fs_path)
            self._send_header(conn, 200, "OK")
        except OSError:
            self._send_header(conn, 404, "Not Found")

    @staticmethod
    def _send_redirect(conn, location: str) -> None:
        header = (
            f"HTTP/1.0 302 Found\r\n"
            f"Location: {location}\r\n"
            "Content-Length: 0\r\n"
            "\r\n"
        )
        conn.sendall(header.encode())

    @staticmethod
    def _send_header(
        conn,
        status: int,
        reason: str,
        content_length: int = 0,
        content_type: str = "text/plain",
    ) -> None:
        header = (
            f"HTTP/1.0 {status} {reason}\r\n"
            f"Content-Type: {content_type}\r\n"
            f"Content-Length: {content_length}\r\n"
            "Access-Control-Allow-Origin: *\r\n"
            "\r\n"
        )
        conn.sendall(header.encode())
