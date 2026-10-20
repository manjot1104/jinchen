from connector.poc import ConfigError, Connector, require_test_mode


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


def main():
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
        equal(c.kg(), {"raw": 384.0, "wip": 116.0}, "raw/WIP stock")
        equal(len(c.ledger.entries), 5, "entries after dispositions")

    check("07", "rejection, rework and scrap are separated without finished goods", tc07)

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
        equal(c.ledger.find("C-1", "WO-2401").name, "STE-0006", "C-1 document")
        equal(c.ledger.find("C-2", "WO-2401").name, "STE-0007", "C-2 document")

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

    check("11", "finished-goods receipt is blocked and batch trace is exact", tc11)

    if failed == 0 and passed == 10:
        print("ALL PASS")
    else:
        print(f"NOT PASS: {passed} passed, {failed} failed")
        raise SystemExit(1)


if __name__ == "__main__":
    main()
