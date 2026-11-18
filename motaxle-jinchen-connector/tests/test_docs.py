"""The docs say what the code does (US-013, US-066, US-086, US-088)."""
import json
import re
import unittest
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
DOCS = PROJECT / "docs"
SAMPLES = PROJECT / "inbox" / "samples"


def read(path):
    return path.read_text(encoding="utf-8")


class MessageContractDoc(unittest.TestCase):
    def setUp(self):
        self.text = read(DOCS / "message-contract.md")

    def test_page_one_says_it_is_not_a_signed_api(self):
        self.assertIn("POC contract, not a signed Jinchen API", self.text.split("## ")[0])

    def test_every_field_of_the_nine_samples_is_documented(self):
        for path in sorted(SAMPLES.glob("*.json")):
            message = json.loads(read(path))
            section = self.text.split("## " + message["kind"])[1].split("\n## ")[0]
            for field in message:
                documented = "| " + field + " |"
                self.assertTrue(documented in section or documented in self.text, (path.name, field))

    def test_every_sentence_in_the_contract_is_one_the_code_returns(self):
        code = read(PROJECT / "connector" / "poc.py") + read(PROJECT / "connector" / "inbox.py")
        sentences = re.findall(r"(?<!\*)\*([A-Z][^*]+?[.])\*(?!\*)", self.text)
        self.assertGreater(len(sentences), 25)
        for sentence in sentences:
            self.assertIn(sentence, code)

    def test_the_eleven_codes_are_listed(self):
        codes = re.findall(r"^\| (E-[A-Z]+) \|", self.text, re.M)
        self.assertEqual(codes, ["E-DUP", "E-ITEM", "E-UOM", "E-WH", "E-BATCH", "E-STOCK",
                                 "E-QTY", "E-OP", "E-REASON", "E-QC", "E-CLOSED"])
        code = read(PROJECT / "connector" / "poc.py")
        used = set(re.findall(r'"(E-[A-Z]+)"', code))
        self.assertEqual(used, set(codes))


class ReadmeAndRunBook(unittest.TestCase):
    def test_readme_has_how_we_work_and_the_prove_line(self):
        readme = read(PROJECT / "README.md")
        self.assertIn("## How we work", readme)
        self.assertIn("## Prove\n\n`python3 prove.py` prints `ALL PASS`.", readme)

    def test_no_jinchen_web_address_in_the_docs(self):
        for path in [PROJECT / "README.md"] + sorted(DOCS.glob("*.md")):
            # The repository's own address (github.com/manjot1104/jinchen) is not Jinchen's.
            text = read(path).lower().replace("https://github.com/manjot1104/jinchen", "")
            self.assertNotRegex(text, r"https?://[^ )]*jinchen", path.name)

    def test_run_book_names_the_password_owner_and_the_replay_steps(self):
        book = read(DOCS / "run-book.md")
        self.assertIn("Test passwords", book)
        self.assertIn("Manjot (Tech Lead)", book)
        self.assertIn("## Replay a message", book)
        self.assertIn("## Restart the desk", book)
        self.assertIn("## The two switches", book)


class DecisionsDoc(unittest.TestCase):
    def test_each_decision_has_a_date_an_owner_and_stories(self):
        rows = [line for line in read(DOCS / "decisions.md").splitlines() if line.startswith("| D-")]
        self.assertGreaterEqual(len(rows), 6)
        for row in rows:
            cells = [cell.strip() for cell in row.strip("|").split("|")]
            self.assertTrue(re.match(r"\d+ \w{3} 2026|open", cells[2]), row)
            self.assertEqual(cells[3], "Manjot")
            self.assertIn("US-", cells[4])


if __name__ == "__main__":
    unittest.main()
