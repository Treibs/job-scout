#!/usr/bin/env python3
"""Local-only companion server — the dashboard's backend.

The dashboard (output/jobs.html) is a static file you can open straight from
disk (read-only). Run this and open the printed URL to turn it into a live app:
persist your pipeline status + notes, add a company to watch, or paste a job link
to scrape + score it straight into your shortlist.

Deliberately localhost-ONLY (binds 127.0.0.1) and stdlib-only — no Flask, no deps.

    python scripts/serve.py            # http://127.0.0.1:8765/

Routes:
    GET  /             -> regenerate + serve output/jobs.html
    GET  /news         -> regenerate + serve output/news.html
    POST /status       -> {"apply_url","status"}                      (back-compat)
    POST /update       -> {"apply_url","status"?,"notes"?,"applied_on"?}
    POST /add-company  -> {"url": "<careers page URL>"}  parse ATS, verify, watch it
    POST /add-job      -> {"url": "<job posting URL>"}   scrape + score + shortlist
    POST /news-feedback-> {"url","useful"?,"valuable"?,"status"?,"notes"?}
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import pathlib
import sys
import tempfile
import threading
from datetime import date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from job_scout.models import SHEET_COLUMNS, VALID_STATUSES, job_to_row  # noqa: E402
from job_scout.news import store as news_store  # noqa: E402
from job_scout.sinks import html_report, news_report  # noqa: E402

CSV_PATH = "output/jobs.csv"
HTML_PATH = "output/jobs.html"
NEWS_HTML = "output/news.html"
NEWS_STORE = "state/news.json"
CONFIG_PATH = "config/search.yaml"

# Statuses the dashboard may set (the pipeline + exits; not "new"/"stale").
ALLOWED_STATUSES = frozenset(VALID_STATUSES - {"new", "stale"})
_EDITABLE = {"status", "notes", "applied_on"}

# Serialize CSV read-modify-write across the ThreadingHTTPServer's worker threads
# (and the GET render, which reads the CSV) so concurrent requests can't interleave
# and clobber each other. NOTE: this is in-process only — it does not coordinate
# with a separate scan/cron process writing the same CSV; stop the server during a
# full re-scan, or add a cross-process file lock if you run both at once.
_CSV_LOCK = threading.RLock()
_render_state = {"csv_mtime": None}
_news_render_state = {"mtime": None}


# ── CSV helpers (pure + importable) ──────────────────────────────────────────
def _read(csv_path):
    with pathlib.Path(csv_path).open("r", encoding="utf-8", newline="") as f:
        return [dict(r) for r in csv.DictReader(f)]


def _write(csv_path, rows):
    csv_path = pathlib.Path(csv_path)
    # Write the union of the known schema and ANY columns already present in the
    # file. This makes the server forward-compatible: an older server build can
    # never silently drop columns a newer pipeline added (which once clobbered
    # day_to_day/company_blurb when a stale server handled a click).
    extra = [c for r in rows for c in r if c not in SHEET_COLUMNS]
    fields = list(SHEET_COLUMNS) + list(dict.fromkeys(extra))
    fd, tmp = tempfile.mkstemp(dir=str(csv_path.parent), prefix=csv_path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=fields)
            w.writeheader()
            for row in rows:
                w.writerow({c: row.get(c, "") for c in fields})
        os.replace(tmp, str(csv_path))
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def set_status(csv_path, apply_url: str, status: str) -> bool:
    """Back-compat single-field setter (kept; tested)."""
    return update_row(csv_path, apply_url, {"status": status})


def update_row(csv_path, apply_url: str, fields: dict) -> bool:
    """Set the editable cells of the row matching ``apply_url``. Auto-stamps
    ``applied_on`` with today's date the first time a row becomes 'applied'."""
    with _CSV_LOCK:
        rows = _read(csv_path)
        found = False
        for row in rows:
            if row.get("apply_url") == apply_url:
                for k, v in fields.items():
                    if k in _EDITABLE:
                        row[k] = v
                if fields.get("status") == "applied" and not (row.get("applied_on") or "").strip():
                    row["applied_on"] = date.today().isoformat()
                found = True
        if found:
            _write(csv_path, rows)
        return found


def append_job(csv_path, job) -> dict:
    """Upsert a scraped+scored Job into the tracker. Returns the written row."""
    with _CSV_LOCK:
        rows = _read(csv_path)
        new_row = {c: ("" if v is None else v) for c, v in zip(SHEET_COLUMNS, job_to_row(job))}
        today = date.today().isoformat()
        for i, row in enumerate(rows):
            if row.get("apply_url") == job.url:  # already tracked — refresh, keep user fields
                for keep in ("first_seen", "notes", "applied_on"):
                    if row.get(keep):
                        new_row[keep] = row[keep]
                if (row.get("status") or "") in ALLOWED_STATUSES:
                    new_row["status"] = row["status"]
                rows[i] = new_row
                break
        else:
            new_row.setdefault("first_seen", today)
            if not new_row.get("first_seen"):
                new_row["first_seen"] = today
            rows.append(new_row)
        _write(csv_path, rows)
        return new_row


# ── HTML render (cached by CSV mtime) ────────────────────────────────────────
def _ensure_html() -> bool:
    """Regenerate output/jobs.html only when the CSV changed since the last render.
    Held under _CSV_LOCK so a render never interleaves with a concurrent write."""
    with _CSV_LOCK:
        try:
            mtime = os.path.getmtime(CSV_PATH)
        except OSError:
            return False
        if _render_state["csv_mtime"] == mtime and pathlib.Path(HTML_PATH).exists():
            return True
        if html_report.render(CSV_PATH) is None:
            return False
        _render_state["csv_mtime"] = mtime
        return True


def _ensure_news_html() -> bool:
    """Render output/news.html when the news store changed (or first time)."""
    with _CSV_LOCK:
        try:
            mtime = os.path.getmtime(NEWS_STORE)
        except OSError:
            mtime = None  # store may not exist yet — still render an empty-state page
        if _news_render_state["mtime"] == mtime and pathlib.Path(NEWS_HTML).exists():
            return True
        news_report.render()  # always writes (empty feed -> empty state)
        _news_render_state["mtime"] = mtime
        return True


# ── actions that need job_scout config / network ─────────────────────────────
def _config():
    from job_scout.config import load_config
    return load_config(CONFIG_PATH)


def add_company(url: str) -> dict:
    """Parse a careers URL → verify the ATS returns jobs → add to the watch list."""
    from job_scout import ingest, strategist
    from job_scout.config import CompanyTarget
    from job_scout.sources.ats import ATS_FETCHERS

    entry = ingest.parse_company_url(url)
    if not entry:
        return {"ok": False, "error": "Not a supported ATS careers URL (greenhouse/lever/ashby/smartrecruiters/workday)."}
    cfg = _config()
    fetch = ATS_FETCHERS.get(entry["ats"])
    try:
        rows = fetch(CompanyTarget(**entry), cfg) if fetch else []
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"verify failed: {e}"}
    if not rows:
        return {"ok": False, "error": f"{entry['ats']} returned no jobs for {entry['name']} — double-check the URL."}
    strategist.apply_changes("config", add_companies=[entry], notes="added from dashboard")
    return {"ok": True, "company": entry, "jobs_found": len(rows),
            "message": f"Watching {entry['name']} ({entry['ats']}, {len(rows)} open roles) — appears on the next scan."}


def add_job(url: str) -> dict:
    """Scrape one job URL, score it, and drop it into the shortlist."""
    from job_scout import ingest, score
    job = ingest.ingest_url(url)
    if not job:
        return {"ok": False, "error": "Couldn't read a job from that URL."}
    try:
        score.score_one_no_filter([job], _config())
    except Exception:  # noqa: BLE001 — scoring is best-effort; still add the role
        pass
    row = append_job(CSV_PATH, job)
    return {"ok": True, "row": row, "message": f"Added “{job.title}” — {job.company}."}


# ── HTTP ─────────────────────────────────────────────────────────────────────
class _Handler(BaseHTTPRequestHandler):
    server_version = "JobScoutServe/2.0"

    def _json(self, code, payload):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _body(self):
        try:
            n = int(self.headers.get("Content-Length") or 0)
            return json.loads(self.rfile.read(n).decode("utf-8")) if n else {}
        except (ValueError, json.JSONDecodeError):
            return None

    def do_GET(self):  # noqa: N802
        if self.path == "/":
            if not _ensure_html():
                return self._json(404, {"ok": False, "error": f"no CSV at {CSV_PATH}"})
            return self._serve_html(HTML_PATH)
        if self.path == "/news":
            _ensure_news_html()
            return self._serve_html(NEWS_HTML)
        return self._json(404, {"ok": False, "error": "not found"})

    def _serve_html(self, path):
        body = pathlib.Path(path).read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):  # noqa: N802
        data = self._body()
        if data is None:
            return self._json(400, {"ok": False, "error": "invalid JSON body"})
        route = self.path

        if route in ("/status", "/update"):
            apply_url = (data.get("apply_url") or "").strip()
            if not apply_url:
                return self._json(400, {"ok": False, "error": "missing apply_url"})
            fields = {k: data[k] for k in _EDITABLE if k in data}
            if "status" in fields and fields["status"] not in ALLOWED_STATUSES:
                return self._json(400, {"ok": False, "error": f"invalid status: {fields['status']!r}"})
            try:
                ok = update_row(CSV_PATH, apply_url, fields)
            except FileNotFoundError:
                return self._json(400, {"ok": False, "error": f"no CSV at {CSV_PATH}"})
            return self._json(200 if ok else 400,
                              {"ok": ok} if ok else {"ok": False, "error": "unknown apply_url"})

        if route == "/add-company":
            res = add_company((data.get("url") or "").strip())
            return self._json(200 if res["ok"] else 400, res)

        if route == "/add-job":
            res = add_job((data.get("url") or "").strip())
            return self._json(200 if res["ok"] else 400, res)

        if route == "/news-feedback":
            url = (data.get("url") or "").strip()
            if not url:
                return self._json(400, {"ok": False, "error": "missing url"})
            fields = {k: data[k] for k in ("useful", "valuable", "status", "notes") if k in data}
            ok = news_store.update_feedback(url, fields, NEWS_STORE)
            return self._json(200 if ok else 404,
                              {"ok": ok} if ok else {"ok": False, "error": "unknown url"})

        return self._json(404, {"ok": False, "error": "not found"})

    def log_message(self, fmt, *args):
        sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="job-scout-serve", description=__doc__)
    p.add_argument("--port", type=int, default=8765, help="Port (default: 8765).")
    args = p.parse_args(argv)
    server = ThreadingHTTPServer(("127.0.0.1", args.port), _Handler)  # loopback only
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
