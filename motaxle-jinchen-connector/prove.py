from contextlib import contextmanager
import json
from pathlib import Path
import shutil
import tempfile

from connector.inbox import load_config, read_all
from connector.poc import ConfigError, Connector, require_test_mode

PROJECT = Path(__file__).resolve().parent
REPO_INBOX = PROJECT / "inbox"


passed = 0
failed = 0


def check(number, description, test):
    global passed, failed
    try:
        test()
        passed += 1
        print(f"TC-{number} PASS: {description}")
    except Exception as exc:
        failed += 1
        print(f"TC-{number} FAIL: {description} ({exc})")


def equal(actual, expected, label):
    assert actual == expected, f"{label}: expected {expected!r}, got {actual!r}"


def rejected(result, code):
    equal(result.code, code, "rejection code")


def issue(ref="J-100", qty=120):
    return {
        "kind": "issue", "id": ref, "work_order": "WO-2401",
        "item": "ST-20", "qty": qty, "uom": "Kg", "batch": "H-441",
        "from_warehouse": "Raw Material Store",
        "to_warehouse": "Forging WIP",
    }


def return_message(ref="R-1", qty=4):
    return {
        "kind": "return", "id": ref, "work_order": "WO-2401",
        "against": "J-100", "item": "ST-20", "qty": qty,
        "uom": "Kg", "batch": "H-441",
    }


@contextmanager
def run_folder(src_dir, config=None):
    """Copy JSON files from src_dir into an isolated temp inbox and run read_all."""
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        inbox_dir = root / "in"
        done_dir = root / "done"
        results_dir = root / "results"
        inbox_dir.mkdir()
        for source in sorted(Path(src_dir).glob("*.json")):
            shutil.copy2(source, inbox_dir / source.name)

        connector = Connector()
        results = read_all(
            connector,
            switch_a=False,
            inbox_dir=inbox_dir,
            done_dir=done_dir,
            results_dir=results_dir,
            config=config,
        )
        yield connector, results, results_dir


def main():
    global passed, failed
    passed = 0
    failed = 0
    try:
        config = load_config()
    except ConfigError as exc:
        print(str(exc))
        print("prove.py did not run")
        raise SystemExit(1)

    def tc02():
        try:
            require_test_mode("test", "live-secret")
        except ConfigError:
            pass
        else:
            raise AssertionError("live password was not refused")
        c = Connector()
        equal(c.kg(), {"raw": 500.0, "wip": 0.0}, "initial stock")

    check("02", "live password refused; empty-password test mode starts", tc02)

    c = Connector()
    first_issue = {}

    def tc03():
        result = c.process(issue(), drop_log=True)
        first_issue["result"] = result
        equal(result.status, "Posted", "first issue status")
        equal(result.document, "STE-0001", "first stock entry")
        equal(c.kg(), {"raw": 380.0, "wip": 120.0}, "stock after issue")
        equal(len(c.ledger.entries), 1, "entry count")
        assert not any(row.ref == "J-100" for row in c.log), "J-100 unexpectedly logged"

    check("03", "J-100 posts once even when its log row is lost", tc03)

    def tc04():
        result = c.process(issue())
        rejected(result, "E-DUP")
        equal(result.document, "STE-0001", "duplicate document")
        equal(c.kg(), {"raw": 380.0, "wip": 120.0}, "stock after duplicate")
        equal(len(c.ledger.entries), 1, "entries after duplicate")

    check("04", "repeated J-100 does not deduct stock again", tc04)

    def tc05():
        first = c.process(return_message())
        second = c.process(return_message())
        equal(first.status, "Posted", "first return")
        rejected(second, "E-DUP")
        equal(c.kg(), {"raw": 384.0, "wip": 116.0}, "stock after repeated return")
        equal(len(c.ledger.entries), 2, "entries after return replay")

    check("05", "R-1 returns 4 kg only once", tc05)

    def tc06():
        for ref, operation in (("CF-10", 10), ("CF-20", 20)):
            result = c.process({
                "kind": "confirm", "id": ref, "work_order": "WO-2401",
                "operation": operation, "completed": 100,
                "rejected": 0, "rework": 0, "scrap": 0,
            })
            equal(result.status, "Posted", f"operation {operation}")
        equal(c.pieces()["finished"], 0.0, "finished goods")
        equal(c.pieces()["hold"], 0.0, "quality hold")
        equal(len(c.ledger.entries), 2, "entries after confirmations")

    check("06", "cutting and forging confirmations do not create receipts", tc06)

    def tc07():
        dispositions = [
            ("D-REJ", "rejection", "forge-crack", 2),
            ("D-RW", "rework", "rework-bend", 1),
            ("D-SC", "scrap", "scale", 1),
        ]
        for ref, decision, reason, qty in dispositions:
            result = c.process({
                "kind": "disposition", "id": ref, "work_order": "WO-2401",
                "item": "CR-100", "qty": qty,
                "decision": decision, "reason": reason,
            })
            equal(result.status, "Posted", ref)
        pieces = c.pieces()
        equal(pieces["rejection"], 2.0, "rejection")
        equal(pieces["rework"], 1.0, "rework")
        equal(pieces["scrap"], 1.0, "scrap")
        equal(pieces["finished"], 0.0, "finished goods")
        equal(c.ledger.capa, 0, "CAPA count")
        equal(c.ledger.capa_rows, [], "CAPA rows")
        equal(c.kg(), {"raw": 384.0, "wip": 116.0}, "raw/WIP stock")
        equal(len(c.ledger.entries), 5, "entries after dispositions")

        control = Connector()
        control.ledger.open_capa("D-CONTROL", "WO-2401", "manual-check")
        equal(control.ledger.capa, 1, "manual CAPA count")
        equal(
            control.ledger.capa_rows,
            [("D-CONTROL", "WO-2401", "manual-check")],
            "manual CAPA rows",
        )

    check("07", "dispositions keep CAPA rows empty; explicit CAPA control adds one row", tc07)

    def tc08():
        result = c.process({
            "kind": "disposition", "id": "D-REJ", "work_order": "WO-2401",
            "item": "CR-100", "qty": 2,
            "decision": "rejection", "reason": "forge-crack",
        })
        rejected(result, "E-DUP")
        equal(c.pieces()["rejection"], 2.0, "rejection after replay")
        equal(len(c.ledger.entries), 5, "entries after disposition replay")

    check("08", "repeated D-REJ does not add rejection twice", tc08)

    def tc09():
        for ref, qty, batch in (("C-1", 40, "B-1"), ("C-2", 56, "B-2")):
            result = c.process({
                "kind": "receipt", "id": ref, "work_order": "WO-2401",
                "item": "CR-100", "qty": qty, "uom": "Nos", "batch": batch,
            })
            equal(result.status, "Posted", ref)
        equal(c.pieces()["hold"], 96.0, "quality hold")
        equal(c.pieces()["finished"], 0.0, "finished goods")
        equal(len(c.ledger.entries), 7, "entries after receipts")
        equal(c.ledger.find("C-1", "WO-2401", "receipt").name, "STE-0006", "C-1 document")
        equal(c.ledger.find("C-2", "WO-2401", "receipt").name, "STE-0007", "C-2 document")

    check("09", "C-1 and C-2 total 96 pieces in Quality Hold", tc09)

    def tc10():
        result = c.process({
            "kind": "receipt", "id": "C-1", "work_order": "WO-2401",
            "item": "CR-100", "qty": 40, "uom": "Nos", "batch": "B-1",
        })
        rejected(result, "E-DUP")
        equal(result.document, "STE-0006", "replayed C-1 document")
        equal(c.pieces()["hold"], 96.0, "hold after replay")
        equal(c.pieces()["finished"], 0.0, "finished goods after replay")
        equal(len(c.ledger.entries), 7, "entries after receipt replay")

    check("10", "repeated C-1 does not add another 40 pieces", tc10)

    def tc11():
        result = c.process({
            "kind": "receipt", "id": "C-BAD", "work_order": "WO-2401",
            "item": "CR-100", "qty": 2, "uom": "Nos", "batch": "B-X",
            "to_warehouse": "Finished Goods Store",
        })
        rejected(result, "E-QC")
        equal(c.pieces()["finished"], 0.0, "finished goods after QC refusal")
        equal(len(c.ledger.entries), 7, "entries after QC refusal")
        found = c.lookup("B-2")
        equal(found["result"], "found", "B-2 lookup")
        equal(found["heat"], "H-441", "B-2 heat")
        equal(found["work_order"], "WO-2401", "B-2 work order")
        equal(c.lookup("H441")["result"], "missing", "near-match lookup")
        print("B-2 found with heat H-441")

    check("11", "finished-goods receipt is blocked and batch trace is exact", tc11)

    def tc12():
        with run_folder(REPO_INBOX / "samples", config=config) as (
            inbox_connector,
            results,
            results_dir,
        ):
            done_dir = results_dir.parent / "done"
            equal(len(results), 9, "sample result count")
            equal(inbox_connector.kg(), {"raw": 384.0, "wip": 116.0}, "inbox sample kg")
            equal(
                inbox_connector.pieces(),
                {"hold": 96.0, "finished": 0.0, "rejection": 2.0, "rework": 1.0, "scrap": 1.0},
                "inbox sample pieces",
            )
            equal(len(inbox_connector.ledger.entries), 7, "inbox sample entries")
            equal(inbox_connector.ledger.capa, 0, "inbox sample CAPA")
            equal(inbox_connector.ledger.capa_rows, [], "inbox sample CAPA rows")
            trace = inbox_connector.lookup("B-1")
            equal(trace["result"], "found", "B-1 trace result")
            equal(trace["heat"], "H-441", "B-1 trace heat")
            equal(trace["work_order"], "WO-2401", "B-1 trace work order")
            equal(trace["document"], "STE-0006", "B-1 trace document")
            equal(len(list(done_dir.glob("*.json"))), 9, "done file count")
            equal(len(list(results_dir.glob("*.json"))), 9, "results file count")

    check("12", "nine sample files reach raw 384, hold 96, finished 0 through read_all.", tc12)

    def tc13():
        expected_codes = {
            "ex-st20.json": "E-ITEM",
            "ex-h441.json": "E-BATCH",
            "ex-nos.json": "E-UOM",
            "ex-warehouse.json": "E-WH",
            "ex-999.json": "E-STOCK",
            "ex-finished.json": "E-QC",
            "ex-nobatch.json": "E-BATCH",
            "ex-scrap-return.json": "E-REASON",
        }
        with run_folder(REPO_INBOX / "examples", config=config) as (
            example_connector,
            results,
            results_dir,
        ):
            equal(len(results), 8, "example result count")
            for filename, expected_code in expected_codes.items():
                data = json.loads((results_dir / filename).read_text(encoding="utf-8"))
                equal(data["status"], "Business Rejected", f"{filename} status")
                equal(data["code"], expected_code, f"{filename} code")
            equal(example_connector.kg(), {"raw": 500.0, "wip": 0.0}, "example stock unchanged")
            equal(
                example_connector.pieces(),
                {"hold": 0.0, "finished": 0.0, "rejection": 0.0, "rework": 0.0, "scrap": 0.0},
                "example pieces unchanged",
            )
            equal(len(example_connector.ledger.entries), 0, "example entries unchanged")
            equal(example_connector.ledger.capa, 0, "example CAPA unchanged")
            equal(example_connector.ledger.capa_rows, [], "example CAPA rows unchanged")

    check("13", "eight bad example files are refused without moving stock", tc13)

    if failed == 0 and passed > 0:
        print("ALL PASS")
    else:
        print(f"NOT PASS: {passed} passed, {failed} failed")
        raise SystemExit(1)


if __name__ == "__main__":
    main()
