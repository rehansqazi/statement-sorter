"""Server tests: the access checks, save/load round trip, and stale-save protection.

    .venv/bin/python -m unittest discover tests
"""

import http.client
import json
import sys
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import server  # noqa: E402

STATE = {
    "cats": [{"id": "c1", "name": "Groceries", "exclude": False}, {"id": "c2", "name": "Chicken", "exclude": False, "parent": "c1"},
             {"id": "c3", "name": "Transfers", "exclude": True}],
    "files": [{"id": "f1", "name": "jan.pdf", "added": 2, "at": "2026-01-31T10:00:00Z"}],
    "txns": [
        {"id": "t1", "date": "2026-01-02", "desc": "Money Transferred to ABC XYZ", "amount": -1500.0, "balance": 98500.0,
         "pool": "ABC XYZ", "cat": "c2", "source": "jan.pdf", "fileId": "f1"},
        {"id": "t2", "date": "2026-01-03", "desc": "Online Cash Deposit", "amount": 20000.0, "balance": None,
         "pool": "CASH DEPOSIT", "cat": None, "source": "jan.pdf", "fileId": "f1"},
    ],
    "rules": {"ABC XYZ": "c2"},
    "keywords": [{"id": "k1", "text": "Ahmed", "cat": "c1"}],
    "settings": {"currency": "PKR ", "dateFmt": "auto", "poolVersion": 3},
}


class ServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        server.DB_PATH = Path(cls.tmp.name) / "test.db"
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        cls.port = cls.httpd.server_address[1]
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.tmp.cleanup()

    def request(self, method, path, body=None, headers=None, app=True):
        con = http.client.HTTPConnection("127.0.0.1", self.port)
        h = {"X-Statement-Sorter": "1"} if app else {}
        h.update(headers or {})
        con.request(method, path, body=json.dumps(body) if isinstance(body, dict) else body, headers=h)
        r = con.getresponse()
        data = r.read()
        con.close()
        return r.status, (json.loads(data) if r.getheader("Content-Type", "").startswith("application/json") else data)

    def test_access_checks(self):
        self.assertEqual(self.request("GET", "/api/state", app=False)[0], 403, "API call without the app header")
        self.assertEqual(self.request("GET", "/api/state", headers={"Origin": "https://evil.example"})[0], 403, "other website")
        self.assertEqual(self.request("GET", "/api/state", headers={"Origin": "http://127.0.0.1:1"})[0], 403, "other local port")
        self.assertEqual(self.request("GET", "/", headers={"Host": "evil.example"})[0], 403, "DNS rebinding")
        self.assertEqual(self.request("GET", "/server.py", app=False)[0], 404, "source files are not served")
        self.assertEqual(self.request("GET", "/", app=False)[0], 200, "the app itself is served")

    def test_round_trip_and_stale_save(self):
        status, r = self.request("GET", "/api/state")
        self.assertEqual(status, 200)
        rev = r["revision"]

        status, r = self.request("PUT", "/api/state", {"revision": rev, "state": STATE})
        self.assertEqual(status, 200)
        self.assertEqual(r["revision"], rev + 1)
        status, r = self.request("GET", "/api/state")
        self.assertEqual(r["state"], STATE)  # every field, including sub-category parents and null balances

        # A tab still holding the old revision must not overwrite the newer data.
        status, _ = self.request("PUT", "/api/state", {"revision": rev, "state": {**STATE, "txns": []}})
        self.assertEqual(status, 409)
        self.assertEqual(self.request("GET", "/api/state")[1]["state"], STATE)

        # A save without a revision (page whose first load failed) is refused too.
        self.assertEqual(self.request("PUT", "/api/state", {"state": STATE})[0], 400)


if __name__ == "__main__":
    unittest.main()
