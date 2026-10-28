# Statement Sorter

Sort your bank statement transactions into categories by dragging and dropping, then see where your money went.

**Your statements never leave your computer.** It works fully offline, in one of two ways:

| | Python engine (recommended) | Browser mode |
|---|---|---|
| How to start | Double-click `start.command` (or run `server.py`) | Double-click `index.html` |
| Reads PDFs with | Python + [pdfplumber](https://github.com/jsvine/pdfplumber) | [pdf.js](https://mozilla.github.io/pdf.js/) in the browser |
| Saves your data in | `statement-sorter.db` (SQLite) next to the app | the browser's local storage |
| Needs | Python 3 | nothing |

## Use it

**Python engine (macOS):**

1. Download or clone this repository.
2. Double-click `start.command`. The first time, it creates a private Python environment (`.venv`) and installs pdfplumber.
3. Your browser opens at http://127.0.0.1:8765. Drop in your PDF statements.

Keep the Terminal window open while you use the app; close it (or press Ctrl+C) to stop.

On other systems, or by hand:

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python server.py
```

**Browser mode:** just open `index.html`. No install, no server.

**Moving your data between the two:** they store data separately. In the one you're leaving, click **Backup**; in the other, click **Restore**.

## What it does

- **Reads PDF statements**: dates, descriptions spread over several lines, money in/out (from +/- signs, CR/DR, debit/credit columns or the running balance) and balances. Password-protected PDFs are supported.
- **Pools similar transactions**: payments to the same person or shop are grouped by name (e.g. every "Money Transferred to ABC XYZ…" lands in one *Abc Xyz* pool), whatever lead-in, account number or wallet name the bank adds.
- **Drag-and-drop sorting** into categories and sub-categories (e.g. *Groceries › Chicken*). You can also tick several pools and click a category.
- **Remembers your choices**: tagged pools and name rules (e.g. anything mentioning "Ahmed" → Groceries) are applied automatically to future statements.
- **Summary**: money in/out, net, average monthly spend, a donut chart and table with percentages per category and sub-category, and a monthly stacked bar chart.
- **Manage your data**: transaction details, flip in/out, delete a transaction or a whole pool (with undo), view or remove an imported statement, CSV export, and backup/restore.

## Privacy

- The page can only talk to the server it came from (a Content-Security-Policy blocks every other connection). In browser mode that means nothing at all.
- The Python server listens on `127.0.0.1` only, so other computers can't reach it. It refuses requests addressed to any other host name (DNS rebinding) and API calls from other websites.
- PDFs sent to the server are read in memory and never written to disk.
- Your data is stored unencrypted, in `statement-sorter.db` (Python engine) or the browser's local storage (browser mode). Use **Backup** to keep a copy and **Reset all** to wipe it.

## Limitations

- Scanned (image-only) PDFs can't be read; there is no OCR.
- European number formats such as `1.234,56` aren't supported yet.
- Amounts are not converted between currencies.

## Development

The statement parser exists twice and the two must agree: `statement_parser.py` (Python engine) and the parser inside `index.html` (browser mode). Regression tests run the Python one over made-up statements in `tests/fixtures`:

```bash
.venv/bin/python -m unittest discover tests
.venv/bin/python tests/test_parser.py --update   # accept new output as the expected results
```

## Third-party code

- [pdf.js](https://github.com/mozilla/pdf.js) 3.11.174, © Mozilla Foundation, Apache License 2.0
- [Chart.js](https://github.com/chartjs/Chart.js) 4.4.1, © Chart.js Contributors, MIT License
- [pdfplumber](https://github.com/jsvine/pdfplumber) (installed by `start.command`, not bundled), MIT License

See [`vendor/LICENSES.md`](vendor/LICENSES.md).
