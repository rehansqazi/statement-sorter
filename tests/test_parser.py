"""Parser regression tests on made-up statements (tests/fixtures/*.pdf).

    .venv/bin/python -m unittest discover tests          # run
    .venv/bin/python tests/test_parser.py --update       # accept new output as expected

Every fixture is synthetic: names, account numbers and references are invented.
"""

import json
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from statement_parser import parse_pdf  # noqa: E402

FIXTURES = HERE / "fixtures"
EXPECTED = HERE / "expected"


def result(pdf: Path) -> dict:
    r = parse_pdf(pdf.read_bytes())
    return {"fmt": r["fmt"], "txns": r["txns"]}


class ParserTests(unittest.TestCase):
    def test_fixtures(self):
        pdfs = sorted(FIXTURES.glob("*.pdf"))
        self.assertTrue(pdfs, "no fixtures found")
        for pdf in pdfs:
            with self.subTest(statement=pdf.name):
                expected = json.loads((EXPECTED / f"{pdf.stem}.json").read_text())
                got = result(pdf)
                self.assertEqual(got["fmt"], expected["fmt"])
                self.assertEqual(len(got["txns"]), len(expected["txns"]))
                for i, (g, e) in enumerate(zip(got["txns"], expected["txns"])):
                    self.assertEqual(g, e, f"transaction {i}")


if __name__ == "__main__":
    if "--update" in sys.argv:
        EXPECTED.mkdir(exist_ok=True)
        for pdf in sorted(FIXTURES.glob("*.pdf")):
            (EXPECTED / f"{pdf.stem}.json").write_text(json.dumps(result(pdf), indent=1, ensure_ascii=False) + "\n")
            print("updated", pdf.stem)
    else:
        unittest.main()
