# Statement Sorter

Sort your bank statement transactions into categories by dragging and dropping, then see where your money went.

Everything runs in your browser from a single HTML file. **Your statements never leave your computer**: the page is blocked from making any network requests, and the PDF reader and chart library are bundled in `vendor/`, so it works fully offline.

## Use it

1. Download or clone this repository.
2. Open `index.html` in Chrome (or any modern browser).
3. Drop in one or more PDF bank statements.

No install, no server, no account.

## What it does

- **Reads PDF statements**: dates, descriptions spread over several lines, money in/out (from +/- signs, CR/DR, debit/credit columns or the running balance) and balances. Password-protected PDFs are supported.
- **Pools similar transactions**: payments to the same person or shop are grouped by name (e.g. every "Money Transferred to ABC XYZ…" lands in one *Abc Xyz* pool), whatever lead-in, account number or wallet name the bank adds.
- **Drag-and-drop sorting** into categories and sub-categories (e.g. *Groceries › Chicken*). You can also tick several pools and click a category.
- **Remembers your choices**: tagged pools and name rules (e.g. anything mentioning "Ahmed" → Groceries) are applied automatically to future statements.
- **Summary**: money in/out, net, average monthly spend, a donut chart and table with percentages per category and sub-category, and a monthly stacked bar chart.
- **Manage your data**: transaction details, flip in/out, delete a transaction or a whole pool (with undo), view or remove an imported statement, CSV export, and backup/restore.

## Privacy

- PDFs are parsed locally with [pdf.js](https://mozilla.github.io/pdf.js/). A Content-Security-Policy blocks every outgoing connection.
- Your sorted data is saved in your browser's local storage on your computer (unencrypted). Use **Backup** to keep a copy and **Reset all** to wipe it.

## Limitations

- Scanned (image-only) PDFs can't be read; there is no OCR.
- European number formats such as `1.234,56` aren't supported yet.
- Amounts are not converted between currencies.

## Third-party code

- [pdf.js](https://github.com/mozilla/pdf.js) 3.11.174, © Mozilla Foundation, Apache License 2.0
- [Chart.js](https://github.com/chartjs/Chart.js) 4.4.1, © Chart.js Contributors, MIT License

See [`vendor/LICENSES.md`](vendor/LICENSES.md).
