#!/usr/bin/env python3
"""Local-only companion server for the job-scout dashboard.

The dashboard (output/jobs.html) is a static file you can open straight from
disk. This tiny helper exists for one reason: so the static dashboard can
*persist* your Interested / Applied / Pass choices back into output/jobs.csv
instead of only keeping them in the browser's localStorage.

It is deliberately localhost-ONLY (binds 127.0.0.1, never 0.0.0.0) and uses
nothing but the Python standard library — no Flask, no third-party deps. Run it,
open the printed URL, and the buttons on each card will POST their status here.

    python scripts/serve.py            # http://127.0.0.1:8765/
    python scripts/serve.py --port 9000

Routes:
    GET  /         -> regenerate + serve output/jobs.html
    POST /status   -> {"apply_url": "...", "status": "interested"} updates the CSV
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import pathlib
import sys
import tempfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# The package lives under src/ — make it importable when run from the repo root.
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from job_scout.models import SHEET_COLUMNS  # noqa: E402
from job_scout.sinks import html_report  # noqa: E402

CSV_PATH = "output/jobs.csv"
HTML_PATH = "output/jobs.html"

# Statuses the dashboard buttons are allowed to set. A subset of the full
# lifecycle (no "stale"/"new" — those are pipeline-managed, not user choices).
ALLOWED_STATUSES = frozenset(
    {"new", "reviewing", "interested", "applied", "pass", "rejected", "archived"}
)


def set_status(csv_path, apply_url: str, status: str) -> bool:
    """Set the ``status`` cell of the row whose ``apply_url`` matches.

    Pure + importable: reads the CSV, flips exactly one cell, and rewrites the
    file atomically (temp file + os.replace) preserving every other column and
    the exact SHEET_COLUMNS order. Returns True if a row was updated, False if
    no row had that apply_url.
    """
    csv_path = pathlib.Path(csv_path)
    with csv_path.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames or list(SHEET_COLUMNS)
        rows = [dict(r) for r in reader]

    found = False
    for row in rows:
        if row.get("apply_url") == apply_url:
            row["status"] = status
            found = True
    if not found:
        return False

    # Write to a temp file in the same directory, then atomically replace so a
    # crash mid-write can never leave a half-written tracker behind.
    fd, tmp_name = tempfile.mkstemp(
        dir=str(csv_path.parent), prefix=csv_path.name + ".", suffix=".tmp"
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=SHEET_COLUMNS)
            writer.writeheader()
            for row in rows:
                writer.writerow({col: row.get(col, "") for col in SHEET_COLUMNS})
        os.replace(tmp_name, str(csv_path))
    except BaseException:
        # Best-effort cleanup of the temp file if anything went wrong.
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise
    return True


class _Handler(BaseHTTPRequestHandler):
    server_version = "JobScoutServe/1.0"

    def _send_json(self, code: int, payload: dict) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802 — name fixed by BaseHTTPRequestHandler
        if self.path != "/":
            self._send_json(404, {"ok": False, "error": "not found"})
            return
        # Regenerate the dashboard from the current CSV, then serve it.
        out = html_report.render(CSV_PATH)
        if out is None:
            self._send_json(404, {"ok": False, "error": f"no CSV at {CSV_PATH}"})
            return
        body = pathlib.Path(HTML_PATH).read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:  # noqa: N802
        if self.path != "/status":
            self._send_json(404, {"ok": False, "error": "not found"})
            return
        try:
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length) if length else b""
            data = json.loads(raw.decode("utf-8")) if raw else {}
        except (ValueError, json.JSONDecodeError):
            self._send_json(400, {"ok": False, "error": "invalid JSON body"})
            return

        apply_url = (data.get("apply_url") or "").strip()
        status = (data.get("status") or "").strip()
        if status not in ALLOWED_STATUSES:
            self._send_json(400, {"ok": False, "error": f"invalid status: {status!r}"})
            return
        if not apply_url:
            self._send_json(400, {"ok": False, "error": "missing apply_url"})
            return

        try:
            updated = set_status(CSV_PATH, apply_url, status)
        except FileNotFoundError:
            self._send_json(400, {"ok": False, "error": f"no CSV at {CSV_PATH}"})
            return
        if not updated:
            self._send_json(400, {"ok": False, "error": "unknown apply_url"})
            return
        self._send_json(200, {"ok": True})

    def log_message(self, fmt, *args) -> None:  # quieter console
        sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="job-scout-serve", description=__doc__)
    p.add_argument("--port", type=int, default=8765, help="Port (default: 8765).")
    args = p.parse_args(argv)

    # 127.0.0.1 ONLY — never bind 0.0.0.0. This is a personal, local-only helper.
    server = ThreadingHTTPServer(("127.0.0.1", args.port), _Handler)
    print(f"Open http://127.0.0.1:{args.port}/ in your browser")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
