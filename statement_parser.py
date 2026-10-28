"""Read transactions out of a PDF bank statement.

The rules mirror the in-browser parser in index.html (which is kept as the fallback
when the Python server isn't running): positioned words -> rows of segments -> dated
rows with amounts -> transactions, with wrapped description lines joined on.

    from statement_parser import parse_pdf
    result = parse_pdf(open("statement.pdf", "rb").read())
"""

from __future__ import annotations

import io
import re
from dataclasses import dataclass, field
from datetime import date

import pdfplumber
from pdfminer.pdfdocument import PDFPasswordIncorrect


class PasswordRequired(Exception):
    """The PDF is encrypted and no (or a wrong) password was given."""


# ---------------------------------------------------------------------------
# PDF -> rows of positioned text
# ---------------------------------------------------------------------------
@dataclass
class Seg:
    x: float
    x2: float
    str: str


@dataclass
class Line:
    y: float          # baseline, measured upwards from the bottom of the page
    h: float          # font size
    segs: list[Seg] = field(default_factory=list)
    text: str = ""


def _page_lines(page) -> list[Line]:
    words = page.extract_words(x_tolerance=1.5, y_tolerance=2, keep_blank_chars=False, extra_attrs=["size"])
    items = [
        {"x": w["x0"], "w": w["x1"] - w["x0"], "y": page.height - w["bottom"], "h": w.get("size") or (w["bottom"] - w["top"]) or 8, "str": w["text"]}
        for w in words if w["text"].strip()
    ]
    items.sort(key=lambda i: (-i["y"], i["x"]))

    lines: list[dict] = []
    for it in items:
        line = next((l for l in lines if abs(l["y"] - it["y"]) < max(2, min(l["h"], it["h"]) * 0.45)), None)
        if line is None:
            line = {"y": it["y"], "h": it["h"], "items": []}
            lines.append(line)
        line["items"].append(it)
    lines.sort(key=lambda l: -l["y"])

    built: list[Line] = []
    for l in lines:
        l["items"].sort(key=lambda i: i["x"])
        segs: list[Seg] = []
        for it in l["items"]:
            last = segs[-1] if segs else None
            gap = it["x"] - last.x2 if last else float("inf")
            if last and gap < max(3, it["h"] * 0.6):
                last.str += (" " if gap > it["h"] * 0.12 and not last.str.endswith(" ") else "") + it["str"]
                last.x2 = max(last.x2, it["x"] + it["w"])
            else:
                segs.append(Seg(it["x"], it["x"] + it["w"], it["str"]))
        segs = [Seg(s.x, s.x2, re.sub(r"\s+", " ", s.str).strip()) for s in segs]
        built.append(Line(l["y"], l["h"], [s for s in segs if s.str]))

    # Wrapped table cells and vertically-centred amounts sit on baselines much closer together than
    # normal line spacing: fold them into one row (overlapping segments are the same cell continuing).
    rows: list[Line] = []
    for l in built:
        if not l.segs:
            continue
        prev = rows[-1] if rows else None
        if prev and prev.y - l.y < min(prev.h, l.h) * 0.7:
            for s in l.segs:
                cell = next((p for p in prev.segs if s.x < p.x2 and s.x2 > p.x), None)
                if cell:
                    cell.str += " " + s.str
                    cell.x2 = max(cell.x2, s.x2)
                else:
                    prev.segs.append(s)
            prev.segs.sort(key=lambda s: s.x)
            prev.y = l.y
        else:
            rows.append(l)
    for r in rows:
        r.text = "  ".join(s.str for s in r.segs)
    return rows


def read_lines(data: bytes, password: str | None = None) -> list[list[Line]]:
    try:
        with pdfplumber.open(io.BytesIO(data), password=password or "") as pdf:
            return [_page_lines(p) for p in pdf.pages]
    except Exception as e:
        # pdfplumber wraps pdfminer's PDFPasswordIncorrect (as an argument or the exception context).
        if any(isinstance(x, PDFPasswordIncorrect) for x in (e, e.__context__, *e.args)):
            raise PasswordRequired() from e
        raise


# ---------------------------------------------------------------------------
# Dates
# ---------------------------------------------------------------------------
MON_RE = r"(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\.?"
MONTHS = {m: i for i, m in enumerate("jan feb mar apr may jun jul aug sep oct nov dec".split(), 1)}


def _mon(s: str) -> int:
    return MONTHS[s[:3].lower()]


def _yr(s: str) -> int:
    return 2000 + int(s) if len(s) == 2 else int(s)


DATE_PATTERNS = [
    (re.compile(r"^(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})(?!\d)"), lambda m: {"y": int(m[1]), "m": int(m[2]), "d": int(m[3])}),
    (re.compile(r"^(\d{1,2})[-/.](\d{1,2})[-/.](\d{4}|\d{2})(?!\d)"), lambda m: {"a": int(m[1]), "b": int(m[2]), "y": _yr(m[3])}),
    (re.compile(r"^(\d{1,2})(?:st|nd|rd|th)?[ \-]?" + MON_RE + r"(?:[ \-,]{1,2}(\d{4}|\d{2}))?(?![a-z\d.,])", re.I),
     lambda m: {"d": int(m[1]), "m": _mon(m[2]), "y": _yr(m[3]) if m[3] else None}),
    (re.compile(r"^" + MON_RE + r"[ \-](\d{1,2})(?:st|nd|rd|th)?(?:,?[ \-](\d{4}))?(?![a-z\d.,])", re.I),
     lambda m: {"d": int(m[2]), "m": _mon(m[1]), "y": int(m[3]) if m[3] else None}),
    (re.compile(r"^(\d{1,2})/(\d{1,2})(?![\d/.])"), lambda m: {"a": int(m[1]), "b": int(m[2]), "y": None}),
]


def parse_date_at(text: str):
    for rx, f in DATE_PATTERNS:
        m = rx.match(text)
        if not m:
            continue
        p = f(m)
        if "a" in p:
            if p["a"] < 1 or p["b"] < 1 or p["a"] > 31 or p["b"] > 31 or (p["a"] > 12 and p["b"] > 12):
                continue
        elif not (1 <= p["m"] <= 12 and 1 <= p["d"] <= 31):
            continue
        return len(m[0]), p
    return None


def resolve_date(p: dict, fmt: str, years: dict) -> str | None:
    y = p.get("y")
    if "a" in p:
        if p["a"] > 12:
            d, m = p["a"], p["b"]
        elif p["b"] > 12:
            m, d = p["a"], p["b"]
        elif fmt == "mdy":
            m, d = p["a"], p["b"]
        else:
            d, m = p["a"], p["b"]
    else:
        d, m = p["d"], p["m"]
    # Dates without a year: if the statement spans two years, late-year months belong to the earlier one.
    if not y:
        y = years["max"] if years["min"] == years["max"] else (years["min"] if m >= 7 else years["max"])
    if not (1 <= m <= 12 and 1 <= d <= 31):
        return None
    return f"{y}-{m:02d}-{d:02d}"


# ---------------------------------------------------------------------------
# Amounts
# ---------------------------------------------------------------------------
AMT_RE = re.compile(
    r"(?<![\w.,])(\(?)([-−–]?)\s?(?:[£$€₹¥]|Rs\.?\s?|PKR\s?|USD\s?|GBP\s?|EUR\s?|INR\s?|AED\s?)?([-−–]?)"
    r"(\d{1,3}(?:,\d{3})+|\d+)\.(\d{2})(?![.,]?\d)(\)?)(\s?(?:CR|DR|Cr|Dr|cr|dr)\b|-(?!\w))?"
)
CODE_RE = re.compile(r"\b(CR|DR|PKR|USD|GBP|EUR|INR|AED|Rs)\b\.?", re.I)


@dataclass
class Amount:
    index: int
    len: int
    value: float
    neg: bool
    cr: bool
    x: float = 0.0


def amounts_in(s: str) -> list[Amount]:
    out = []
    for m in AMT_RE.finditer(s):
        post = m[7] or ""
        out.append(Amount(
            index=m.start(), len=len(m[0]),
            value=float(m[4].replace(",", "") + "." + m[5]),
            neg=bool(m[2] or m[3] or (m[1] and m[6]) or re.search(r"dr|-", post, re.I)),
            cr=bool(re.search(r"cr", post, re.I)),
        ))
    return out


def analyze_line(line: Line) -> tuple[str, list[Amount]]:
    """Split a row into its description text and the amount columns."""
    parts: list[str] = []
    amts: list[Amount] = []
    for i, s in enumerate(line.segs):
        ms = amounts_in(s.str)
        if ms:
            leftover = re.sub(r"[£$€₹¥()\s]", "", CODE_RE.sub(" ", AMT_RE.sub(" ", s.str)))
            if not re.search(r"[a-z0-9]", leftover, re.I):
                w = s.x2 - s.x
                for m in ms:
                    m.x = s.x + w * ((m.index + m.len / 2) / len(s.str))
                    amts.append(m)
                continue
            # The last segment may have amounts glued to the description: peel trailing amounts off.
            if i == len(line.segs) - 1:
                k = len(ms)
                while k > 0:
                    tail = CODE_RE.sub(" ", AMT_RE.sub(" ", s.str[ms[k - 1].index:])).strip()
                    if tail:
                        break
                    k -= 1
                if k < len(ms):
                    head = s.str[:ms[k].index].strip()
                    if re.search(r"[a-z]", head, re.I):
                        parts.append(head)
                        for m in ms[k:]:
                            m.x = s.x2
                            amts.append(m)
                        continue
        parts.append(s.str)
    return re.sub(r"\s+", " ", " ".join(parts)).strip(), amts


# ---------------------------------------------------------------------------
# Statement -> transactions
# ---------------------------------------------------------------------------
BAL_LINE_RE = re.compile(r"balance (brought|carried) forward|\b(opening|closing|previous|new|start(ing)?|end(ing)?) balance|balance [bc]/?f\b|brought forward|carried forward", re.I)
OPEN_BAL_RE = re.compile(r"brought forward|opening balance|previous balance|start(ing)? balance|balance b/?f", re.I)
SUMMARY_RE = re.compile(r"\btotal|money (in|out)\b|payments? (in|out)\b|paid (in|out)\b|credit limit|minimum (payment|due)|payment due|available|overdraft|interest rate|\bapr\b|statement (date|period|number)|page \d|sort code|account (number|no)|iban|bic\b", re.I)
FOOTER_RE = re.compile(r"page \d|continued|statement|sort code|account (number|no)|www\.|\.com\b|registered|authori[sz]ed|regulated|balance|\b\d{1,2}:\d{2}\b", re.I)
INCOME_RE = re.compile(r"salary|payroll|\bwages?\b|deposit|refund|interest paid|credit interest|transfer from|\bfrom\b|received|dividend|reversal|cashback|payment received|thank you|\bbgc\b|\bcredit\b", re.I)


def detect_header(line: Line, amts: list[Amount]):
    if amts:
        return None
    t = line.text.lower()

    def find(rx):
        return next((s for s in line.segs if re.search(rx, s.str, re.I)), None)

    deb = find(r"debit|withdraw|paid out|money out|payments? out|\bout\b|\bdr\b")
    cred = find(r"credit|deposit|paid in|money in|payments? in|receipts?|\bin\b|\bcr\b")
    bal = find(r"balance")
    if not (deb or cred):
        return None
    if not re.search(r"date|description|details|particulars|transaction|narrative|balance", t):
        return None
    if deb and cred and deb is cred:
        return None
    c = lambda s: (s.x + s.x2) / 2 if s else None  # noqa: E731
    return {"debX": c(deb), "credX": c(cred), "balX": c(bal)}


def extract_transactions(pages: list[list[Line]], fmt_setting: str = "auto") -> tuple[list[dict], str]:
    lines = [l for p in pages for l in p]
    all_text = "\n".join(l.text for l in lines)
    now_y = date.today().year
    ys = [int(y) for y in re.findall(r"\b(20\d{2})\b", all_text) if 2000 <= int(y) <= now_y + 1]
    years = {"min": min(ys), "max": max(ys)} if ys else {"min": now_y, "max": now_y}
    if years["max"] - years["min"] > 1:
        years["min"] = years["max"] - 1
    dmy = mdy = 0
    for l in lines:
        d = parse_date_at(l.text)
        if d and "a" in d[1]:
            dmy += d[1]["a"] > 12
            mdy += d[1]["b"] > 12
    fmt = fmt_setting if fmt_setting != "auto" else ("mdy" if mdy > dmy else "dmy")
    # Year-less dates in a statement that crosses New Year (Dec + Jan) but only prints the later year.
    if years["min"] == years["max"]:
        ms = set()
        for l in lines:
            d = parse_date_at(l.text)
            if d and not d[1].get("y"):
                p = d[1]
                ms.add((p["a"] if fmt == "mdy" else p["b"]) if "a" in p else p["m"])
        if any(m >= 10 for m in ms) and any(m <= 3 for m in ms):
            years["min"] = years["max"] - 1

    out: list[dict] = []
    state = {"cols": None, "prev_bal": None}

    def emit(dt, desc, amts, line):
        cols = state["cols"]
        rest, bal = list(amts), None
        if cols and cols["balX"] is not None:
            last = rest[-1]
            d_b = abs(last.x - cols["balX"])
            d_o = min(abs(last.x - cols["debX"]) if cols["debX"] is not None else 1e9,
                      abs(last.x - cols["credX"]) if cols["credX"] is not None else 1e9)
            if d_b < d_o:
                bal = rest.pop()
        elif len(rest) >= 2:
            bal = rest.pop()
        if not rest:
            if bal:
                state["prev_bal"] = -bal.value if bal.neg else bal.value
            return None
        a = next((r for r in rest if r.value != 0), None)
        if not a:
            return None
        desc = re.sub(r"\s+", " ", desc).strip()
        bal_v = (-bal.value if bal.neg else bal.value) if bal else None
        sign = None
        if a.neg:
            sign = -1
        elif a.cr:
            sign = 1
        elif cols and cols["debX"] is not None and cols["credX"] is not None:
            sign = -1 if abs(a.x - cols["debX"]) <= abs(a.x - cols["credX"]) else 1
        prev = state["prev_bal"]
        if sign is None and bal_v is not None and prev is not None:
            if abs(prev - a.value - bal_v) < 0.015:
                sign = -1
            elif abs(prev + a.value - bal_v) < 0.015:
                sign = 1
        if sign is None:
            sign = 1 if INCOME_RE.search(desc) else -1
        if bal_v is not None:
            state["prev_bal"] = bal_v
        t = {"date": dt, "desc": desc or "(no description)", "amount": round(sign * a.value, 2), "balance": bal_v, "_cont": 0}
        out.append(t)
        return t

    last_date = None
    for page_lines in pages:
        pending = last_txn = None
        in_table = False
        analyzed = [analyze_line(l) for l in page_lines]
        for i, line in enumerate(page_lines):
            text, amts = analyzed[i]
            nxt = analyzed[i + 1] if i + 1 < len(analyzed) else None
            low = line.text.lower()
            head = detect_header(line, amts)
            if head:
                state["cols"], in_table, pending, last_txn = head, True, None, None
                continue
            if BAL_LINE_RE.search(low):
                if OPEN_BAL_RE.search(low) and amts:
                    b = amts[-1]
                    state["prev_bal"] = -b.value if b.neg else b.value
                pending = last_txn = None
                continue
            dm = parse_date_at(text)
            if dm:
                desc = text[dm[0]:].strip()
                dm2 = parse_date_at(desc)
                if dm2:
                    desc = desc[dm2[0]:].strip()
                iso = resolve_date(dm[1], fmt, years)
                if not iso:
                    continue
                last_date, in_table = iso, True
                if amts:
                    pending = None
                    last_txn = emit(iso, desc, amts, line)
                else:
                    pending, last_txn = {"date": iso, "desc": desc}, None
            elif amts:
                if SUMMARY_RE.search(low):
                    pending = last_txn = None
                    continue
                if pending:
                    last_txn = emit(pending["date"], pending["desc"] + " " + text, amts, line)
                    pending = None
                elif last_date and in_table and re.search(r"[a-z]{2,}", text, re.I):
                    last_txn = emit(last_date, text, amts, line)
            elif text:
                if pending and len(pending["desc"]) < 120 and not FOOTER_RE.search(low):
                    pending["desc"] += " " + text
                # An undated, amount-less line followed by an undated line WITH amounts starts a new
                # transaction (same day as the previous one) rather than continuing the last one.
                elif last_date and in_table and nxt and nxt[1] and not parse_date_at(nxt[0]) and not FOOTER_RE.search(low):
                    pending, last_txn = {"date": last_date, "desc": text}, None
                # Undated lines with no amount, between a transaction and the next one (or a header/footer),
                # are its description wrapping. No distance check: some PDFs report misleading text heights.
                elif last_txn and last_txn["_cont"] < 4 and len(text) < 90 and not FOOTER_RE.search(low):
                    last_txn["desc"] += " " + text
                    last_txn["_cont"] += 1
                else:
                    last_txn = None
    return [{k: v for k, v in t.items() if k != "_cont"} for t in out], fmt


# ---------------------------------------------------------------------------
# Entry point used by the server
# ---------------------------------------------------------------------------
def parse_pdf(data: bytes, fmt: str = "auto", password: str | None = None) -> dict:
    pages = read_lines(data, password)
    txns, used_fmt = extract_transactions(pages, fmt)
    return {
        "txns": txns,
        "fmt": used_fmt,
        "pages": len(pages),
        "textLength": sum(len(l.text) for p in pages for l in p),
        "text": "\n".join(f"--- page {i} ---\n" + "\n".join(l.text for l in p) for i, p in enumerate(pages, 1)),
        "layout": "\n".join(
            f"--- page {i} ---\n" + "\n".join(
                f"y={l.y:.1f} h={l.h:.1f}  " + "  ".join(f"[x={s.x:.1f}] {s.str}" for s in l.segs) for l in p)
            for i, p in enumerate(pages, 1)),
    }


if __name__ == "__main__":
    import json
    import sys

    for path in sys.argv[1:]:
        r = parse_pdf(open(path, "rb").read())
        print(json.dumps({"file": path, "fmt": r["fmt"], "txns": r["txns"]}, ensure_ascii=False))
