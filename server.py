"""Statement Sorter local server.

Serves the app on http://127.0.0.1:8765, reads PDF statements with Python (pdfplumber)
and keeps your data in a SQLite database next to this file (statement-sorter.db).

    .venv/bin/python server.py            # then open http://127.0.0.1:8765
    .venv/bin/python server.py --port 9000 --no-browser

Only this computer can reach it: it listens on 127.0.0.1, rejects requests whose Host
isn't localhost (DNS-rebinding), and API calls must carry a custom header that other
websites can't send without a CORS preflight this server never approves.
"""

from __future__ import annotations

import argparse
import errno
import json
import mimetypes
import os
import sqlite3
import threading
import webbrowser
from contextlib import contextmanager
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from statement_parser import PasswordRequired, parse_pdf

ROOT = Path(__file__).resolve().parent
DB_PATH = Path(os.environ.get("STATEMENT_SORTER_DB") or ROOT / "statement-sorter.db")  # override for testing
STATIC = {"/": "index.html", "/index.html": "index.html"} | {
    f"/vendor/{p.name}": f"vendor/{p.name}" for p in (ROOT / "vendor").glob("*.js")
}
MAX_UPLOAD = 60 * 1024 * 1024
API_HEADER = "X-Statement-Sorter"

SCHEMA = """
CREATE TABLE IF NOT EXISTS categories (
  id TEXT PRIMARY KEY, name TEXT NOT NULL, parent_id TEXT, exclude INTEGER NOT NULL DEFAULT 0, position INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS statements (
  id TEXT PRIMARY KEY, name TEXT NOT NULL, added INTEGER, imported_at TEXT
);
CREATE TABLE IF NOT EXISTS transactions (
  id TEXT PRIMARY KEY, date TEXT NOT NULL, description TEXT NOT NULL, amount REAL NOT NULL, balance REAL,
  pool TEXT, category_id TEXT, source TEXT, statement_id TEXT
);
CREATE TABLE IF NOT EXISTS pool_rules (pool TEXT PRIMARY KEY, category_id TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS name_rules (id TEXT PRIMARY KEY, text TEXT NOT NULL, category_id TEXT NOT NULL, position INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS transactions_date ON transactions(date);
CREATE INDEX IF NOT EXISTS transactions_category ON transactions(category_id);
"""

db_lock = threading.Lock()


def connect() -> sqlite3.Connection:
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    con.executescript(SCHEMA)
    return con


@contextmanager
def db():
    """One connection per call, inside a transaction (committed, or rolled back on error), then closed."""
    with db_lock:
        con = connect()
        try:
            with con:
                yield con
        finally:
            con.close()


# ---------------------------------------------------------------------------
# App state <-> database tables
# ---------------------------------------------------------------------------
class StaleSave(Exception):
    """The page saving was loaded before the latest save (another tab, or a failed load)."""


# The revision is SQLite's built-in user_version counter: +1 on every save, checked in the same transaction.
def load_state() -> tuple[dict | None, int]:
    with db() as con:
        rev = con.execute("PRAGMA user_version").fetchone()[0]
        if not con.execute("SELECT 1 FROM settings LIMIT 1").fetchone() and not con.execute("SELECT 1 FROM categories LIMIT 1").fetchone():
            return None, rev  # nothing saved yet
        return {
            "cats": [
                {"id": r["id"], "name": r["name"], "exclude": bool(r["exclude"]), **({"parent": r["parent_id"]} if r["parent_id"] else {})}
                for r in con.execute("SELECT * FROM categories ORDER BY position")
            ],
            "files": [
                {"id": r["id"], "name": r["name"], "added": r["added"], "at": r["imported_at"]}
                for r in con.execute("SELECT * FROM statements ORDER BY imported_at")
            ],
            "txns": [
                {"id": r["id"], "date": r["date"], "desc": r["description"], "amount": r["amount"], "balance": r["balance"],
                 "pool": r["pool"], "cat": r["category_id"], "source": r["source"], "fileId": r["statement_id"]}
                for r in con.execute("SELECT * FROM transactions ORDER BY rowid")
            ],
            "rules": {r["pool"]: r["category_id"] for r in con.execute("SELECT * FROM pool_rules")},
            "keywords": [
                {"id": r["id"], "text": r["text"], "cat": r["category_id"]}
                for r in con.execute("SELECT * FROM name_rules ORDER BY position")
            ],
            "settings": {r["key"]: json.loads(r["value"]) for r in con.execute("SELECT * FROM settings")},
        }, rev


def save_state(s: dict, revision: int) -> int:
    with db() as con:  # one transaction: all or nothing
        current = con.execute("PRAGMA user_version").fetchone()[0]
        if revision != current:
            raise StaleSave()
        for table in ("categories", "statements", "transactions", "pool_rules", "name_rules", "settings"):
            con.execute(f"DELETE FROM {table}")
        con.executemany(
            "INSERT INTO categories VALUES (?,?,?,?,?)",
            [(c["id"], c["name"], c.get("parent"), int(bool(c.get("exclude"))), i) for i, c in enumerate(s.get("cats", []))],
        )
        con.executemany(
            "INSERT INTO statements VALUES (?,?,?,?)",
            [(f["id"], f.get("name") or "", f.get("added"), f.get("at")) for f in s.get("files", [])],
        )
        con.executemany(
            "INSERT INTO transactions VALUES (?,?,?,?,?,?,?,?,?)",
            [(t["id"], t["date"], t["desc"], t["amount"], t.get("balance"), t.get("pool"), t.get("cat"), t.get("source"), t.get("fileId"))
             for t in s.get("txns", [])],
        )
        con.executemany("INSERT INTO pool_rules VALUES (?,?)", list((s.get("rules") or {}).items()))
        con.executemany(
            "INSERT INTO name_rules VALUES (?,?,?,?)",
            [(k["id"], k["text"], k["cat"], i) for i, k in enumerate(s.get("keywords", []))],
        )
        con.executemany("INSERT INTO settings VALUES (?,?)", [(k, json.dumps(v)) for k, v in (s.get("settings") or {}).items()])
        con.execute(f"PRAGMA user_version = {current + 1}")
        return current + 1


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------
class Handler(BaseHTTPRequestHandler):
    server_version = "StatementSorter"

    def log_message(self, fmt, *args):  # keep the terminal quiet apart from errors
        if args and str(args[1]).startswith(("4", "5")):
            super().log_message(fmt, *args)

    # -- guards --------------------------------------------------------------
    def _host_ok(self) -> bool:
        host = (self.headers.get("Host") or "").rsplit(":", 1)[0].strip("[]")
        return host in {"127.0.0.1", "localhost", "::1"}

    def _api_ok(self) -> bool:
        if self.headers.get(API_HEADER) != "1":
            return False
        origin = self.headers.get("Origin")  # browsers send it on fetches; it must be this very server
        return origin is None or origin == f"http://{self.headers.get('Host')}"

    def _send(self, status: int, body: bytes = b"", ctype: str = "application/json") -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj, status: int = 200) -> None:
        self._send(status, json.dumps(obj, ensure_ascii=False).encode())

    def _body(self) -> bytes | None:
        n = int(self.headers.get("Content-Length") or 0)
        if n > MAX_UPLOAD:
            self._json({"error": "File too large"}, HTTPStatus.REQUEST_ENTITY_TOO_LARGE)
            return None
        return self.rfile.read(n)

    def _guard(self, api: bool) -> bool:
        if not self._host_ok() or (api and not self._api_ok()):
            self._json({"error": "Forbidden"}, HTTPStatus.FORBIDDEN)
            return False
        return True

    # -- routes ----------------------------------------------------------------
    def do_GET(self):
        path = urlparse(self.path).path
        if not self._guard(path.startswith("/api/")):
            return
        if path == "/api/health":
            return self._json({"ok": True, "db": DB_PATH.name})
        if path == "/api/state":
            state, rev = load_state()
            return self._json({"state": state, "revision": rev})
        rel = STATIC.get(path)
        if not rel:
            return self._send(HTTPStatus.NOT_FOUND, b"Not found", "text/plain")
        ctype = mimetypes.guess_type(rel)[0] or "application/octet-stream"
        self._send(200, (ROOT / rel).read_bytes(), ctype + ("; charset=utf-8" if ctype.startswith(("text/", "application/javascript")) else ""))

    def do_PUT(self):
        path = urlparse(self.path).path
        if not self._guard(True):
            return
        if path != "/api/state":
            return self._json({"error": "Not found"}, HTTPStatus.NOT_FOUND)
        body = self._body()
        if body is None:
            return
        try:
            req = json.loads(body)
            state = req.get("state") if isinstance(req, dict) else None
            if not isinstance(state, dict) or not isinstance(state.get("txns"), list) or not isinstance(state.get("cats"), list):
                raise ValueError("not a Statement Sorter state")
            if not isinstance(req.get("revision"), int):
                raise ValueError("missing revision")
            rev = save_state(state, req["revision"])
        except StaleSave:
            return self._json({"error": "stale"}, HTTPStatus.CONFLICT)
        except (ValueError, KeyError, TypeError, sqlite3.Error) as e:
            return self._json({"error": f"Could not save: {e}"}, HTTPStatus.BAD_REQUEST)
        self._json({"ok": True, "revision": rev})

    def do_POST(self):
        url = urlparse(self.path)
        if not self._guard(True):
            return
        if url.path != "/api/parse":
            return self._json({"error": "Not found"}, HTTPStatus.NOT_FOUND)
        data = self._body()
        if data is None:
            return
        fmt = parse_qs(url.query).get("fmt", ["auto"])[0]
        password = unquote(self.headers.get("X-PDF-Password") or "") or None
        try:
            # The PDF is only held in memory for this request; it is never written to disk.
            self._json(parse_pdf(data, fmt if fmt in {"auto", "dmy", "mdy"} else "auto", password))
        except PasswordRequired:
            self._json({"error": "password", "wrong": bool(password)}, HTTPStatus.UNAUTHORIZED)
        except Exception as e:  # a broken PDF must not take the server down
            self._json({"error": f"{type(e).__name__}: {e}"}, HTTPStatus.UNPROCESSABLE_ENTITY)


def main() -> None:
    ap = argparse.ArgumentParser(description="Run Statement Sorter locally.")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--no-browser", action="store_true", help="don't open the browser")
    args = ap.parse_args()
    connect().close()
    url = f"http://127.0.0.1:{args.port}/"
    try:
        httpd = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    except OSError as e:
        if e.errno != errno.EADDRINUSE:
            raise
        print(f"Port {args.port} is already in use, probably by Statement Sorter itself. Opening {url}")
        if not args.no_browser:
            webbrowser.open(url)
        return
    print(f"Statement Sorter is running at {url}\nData: {DB_PATH}\nPress Ctrl+C to stop.", flush=True)
    if not args.no_browser:
        threading.Timer(0.5, webbrowser.open, [url]).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()
