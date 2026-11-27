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


class PocNotes(unittest.TestCase):
    """The POC drafts for US-010, US-011, US-012, US-014 and US-064 match the code."""

    def test_reason_list_matches_the_code(self):
        from connector.poc import DECISION_WAREHOUSE, REASONS
        rows = re.findall(r"^\| ([a-z-]+) \| ([a-z]+) \| ([A-Za-z ]+?) \|$", read(DOCS / "reason-list.md"), re.M)
        self.assertEqual({reason: decision for reason, decision, _ in rows}, REASONS)
        for _, decision, warehouse in rows:
            self.assertEqual(DECISION_WAREHOUSE[decision], warehouse)

    def test_master_data_lists_the_seven_warehouses(self):
        from connector import poc
        text = read(DOCS / "master-data.md")
        for name in (poc.RAW, poc.WIP, poc.HOLD, poc.FG, poc.REJECTION, poc.REWORK, poc.SCRAP):
            self.assertIn("| Warehouse | " + name + " |", text)
        order = ["Company", "UOM", "Warehouses", "Items", "BOM", "Batch H-441", "Opening stock"]
        positions = [text.index(word, text.index("## Load order")) for word in order]
        self.assertEqual(positions, sorted(positions))

    def test_interface_note_marks_each_message_once(self):
        text = read(DOCS / "interface-note.md")
        marks = dict(re.findall(r"^\| (M\d+) \|[^|]+\|[^|]+\| ([^|]+?) \|", text, re.M))
        self.assertEqual(sorted(marks, key=lambda m: int(m[1:])), ["M%d" % n for n in range(1, 11)])
        for mark in marks.values():
            self.assertIn(mark, ("In scope", "Out of scope", "By hand", "Link only"))

    def test_interface_note_answers_fifteen_questions(self):
        text = read(DOCS / "interface-note.md").split("## The fifteen workshop questions")[1].split("\n## ")[0]
        numbers = re.findall(r"^\| (\d+) \|", text, re.M)
        self.assertEqual(numbers, [str(n) for n in range(1, 16)])

    def test_acceptance_doc_points_at_the_demo_script(self):
        self.assertIn("demo/SCRIPT.txt", read(DOCS / "poc-acceptance.md"))
        self.assertTrue((PROJECT / "demo" / "SCRIPT.txt").is_file())


class PocFindings(unittest.TestCase):
    """US-065: the findings report counts every story once, and its sums add up."""

    def setUp(self):
        self.text = read(DOCS / "poc-findings.md")
        self.stories = re.findall(r"^\| (US-\d{3}) \| [^|]+ \| (Must|Should|Could) \| (Done|Open|On hold|Canceled) \| (PRI-\d+|-) \|", self.text, re.M)

    def test_every_story_appears_once(self):
        ids = [story for story, _, _, _ in self.stories]
        self.assertEqual(ids, ["US-%03d" % n for n in range(1, 92)])

    def test_the_summary_matches_the_story_table(self):
        summary = dict(re.findall(r"^\| (Done|Open|On hold|Canceled) \| (\d+) \|", self.text, re.M))
        counted = {}
        for _, _, state, _ in self.stories:
            counted[state] = counted.get(state, 0) + 1
        self.assertEqual({state: int(n) for state, n in summary.items()}, counted)
        self.assertIn("| **Total** | **%d** |" % len(self.stories), self.text)

    def test_stories_outside_linear_are_the_ones_on_hold(self):
        for story, _, state, linear in self.stories:
            self.assertEqual(linear == "-", state == "On hold", story)

    def test_the_code_count_matches_the_contract(self):
        codes = set(re.findall(r"E-[A-Z]+", read(DOCS / "message-contract.md")))
        self.assertIn("%d error codes." % len(codes), self.text)
        self.assertIn("%d codes in [message-contract.md]" % len(codes), self.text)

    def test_readme_links_the_report(self):
        self.assertIn("docs/poc-findings.md", read(PROJECT / "README.md"))


if __name__ == "__main__":
    unittest.main()
