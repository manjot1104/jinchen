"""Stand-in rules decided on 18 November 2026 (US-079 to US-082).

US-079  good + rejected + rework + scrap is checked against the order of 100.
US-080  a confirmation on an order with no metal issued is refused.
US-081  the pieces must fit the steel still out, plus the loss allowance.
US-082  hold, resume and cancel; a closed order returns E-CLOSED.
"""
import inspect
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import server
from connector import inbox
from connector.inbox import load_config
from connector.poc import ConfigError, Connector, DEFAULT_WEIGHT

PROJECT = Path(__file__).resolve().parent.parent
SAMPLES = PROJECT / "inbox" / "samples"


def issue(ref="J-100", qty=120, work_order="WO-2401"):
    return {
        "kind": "issue", "id": ref, "work_order": work_order,
        "item": "ST-20", "qty": qty, "uom": "Kg", "batch": "H-441",
        "from_warehouse": "Raw Material Store", "to_warehouse": "Forging WIP",
    }


def give_back(ref, qty, against="J-100"):
    return {
        "kind": "return", "id": ref, "work_order": "WO-2401", "against": against,
        "item": "ST-20", "qty": qty, "uom": "Kg", "batch": "H-441",
    }


def confirm(ref="CF-10", operation=10, completed=100, rejected=0, rework=0, scrap=0):
    return {
        "kind": "confirm", "id": ref, "work_order": "WO-2401", "operation": operation,
        "completed": completed, "rejected": rejected, "rework": rework, "scrap": scrap,
    }


def order(kind, ref, work_order="WO-2401"):
    return {"kind": kind, "id": ref, "work_order": work_order}


def with_metal():
    connector = Connector()
    assert connector.process(issue()).status == "Posted"
    return connector


class FourQuantityRules(unittest.TestCase):
    """US-079."""

    def test_96_2_1_1_posts(self):
        connector = with_metal()
        result = connector.process(confirm(completed=96, rejected=2, rework=1, scrap=1))
        self.assertEqual(result.status, "Posted")
        self.assertEqual(connector.confirmations, [("WO-2401", 10, (96.0, 2.0, 1.0, 1.0))])

    def test_96_5_1_1_is_refused_and_the_stored_96_stays(self):
        connector = with_metal()
        connector.process(confirm(completed=96, rejected=2, rework=1, scrap=1))
        result = connector.process(confirm("CF-10B", completed=96, rejected=5, rework=1, scrap=1))
        self.assertEqual(result.code, "E-QTY")
        self.assertEqual(result.message, "Completed pieces are above the order.")
        self.assertEqual(connector.confirmations, [("WO-2401", 10, (96.0, 2.0, 1.0, 1.0))])

    def test_each_quantity_is_stored_separately_on_the_log_row(self):
        connector = with_metal()
        connector.process(confirm(completed=96, rejected=2, rework=1, scrap=1))
        row = connector.log[-1]
        self.assertEqual(row.kind, "confirm")
        self.assertEqual(row.quantities, {"completed": 96.0, "rejected": 2.0, "rework": 1.0, "scrap": 1.0})

    def test_kilograms_never_enter_the_piece_sum(self):
        connector = with_metal()
        before = connector.kg()
        connector.process(confirm(completed=96, rejected=2, rework=1, scrap=1))
        self.assertEqual(connector.kg(), before)
        self.assertNotIn("kg(", inspect.getsource(Connector._confirm))


class NoMetalRules(unittest.TestCase):
    """US-080 and BR-36."""

    def test_confirm_on_a_fresh_connector_is_refused(self):
        connector = Connector()
        result = connector.process(confirm())
        self.assertEqual(result.status, "Business Rejected")
        self.assertEqual(result.code, "E-QTY")
        self.assertEqual(result.message, "No metal was issued on this work order.")
        self.assertEqual(connector.confirmations, [])

    def test_the_same_confirmation_posts_after_j100(self):
        connector = Connector()
        connector.process(confirm())
        connector.process(issue())
        self.assertEqual(connector.process(confirm()).status, "Posted")
        self.assertEqual(len(connector.confirmations), 1)

    def test_metal_on_another_order_does_not_count(self):
        connector = Connector()
        connector.process(issue("J-200", 50, work_order="WO-2402"))
        self.assertEqual(connector.process(confirm()).message, "No metal was issued on this work order.")

    def test_all_metal_returned_counts_as_none(self):
        connector = with_metal()
        connector.process(give_back("R-ALL", 120))
        self.assertEqual(connector.process(confirm()).message, "No metal was issued on this work order.")

    def test_operation_30_is_still_ignored_without_metal(self):
        result = Connector().process(confirm("CF-30", operation=30))
        self.assertEqual(result.status, "Posted")
        self.assertIn("ignored", result.message)

    def test_the_nine_samples_still_reach_384_116_96_0(self):
        connector = Connector()
        for path in sorted(SAMPLES.glob("*.json")):
            result = connector.process(json.loads(path.read_text()), drop_log=path.name.startswith("01-"))
            self.assertEqual(result.status, "Posted", path.name)
        self.assertEqual(connector.kg(), {"raw": 384.0, "wip": 116.0})
        self.assertEqual(connector.pieces()["hold"], 96.0)
        self.assertEqual(connector.pieces()["finished"], 0.0)
        self.assertEqual(len(connector.confirmations), 2)


class WeightRules(unittest.TestCase):
    """US-081 and BR-08."""

    def test_100_pieces_from_116_kg_posts_within_the_allowance(self):
        connector = with_metal()
        connector.process(give_back("R-1", 4))
        # 100 x 1.20 = 120 kg needed; 116 x 1.05 = 121.8 kg allowed.
        self.assertEqual(connector.process(confirm(completed=96, rejected=2, rework=1, scrap=1)).status, "Posted")

    def test_100_rods_cannot_be_confirmed_from_60_kg(self):
        connector = Connector()
        connector.process(issue(qty=60))
        result = connector.process(confirm())
        self.assertEqual(result.code, "E-QTY")
        self.assertEqual(result.message, "Pieces need more steel than was issued.")
        self.assertEqual(connector.confirmations, [])

    def test_the_edge_is_the_allowance_not_a_literal(self):
        # 100 kg out allows 105 kg = 87.5 pieces: 87 posts, 88 is refused.
        connector = Connector()
        connector.process(issue(qty=100))
        self.assertEqual(connector.process(confirm("CF-87", completed=87)).status, "Posted")
        self.assertEqual(connector.process(confirm("CF-88", completed=88)).code, "E-QTY")

    def test_a_tighter_allowance_refuses_what_5_percent_allows(self):
        connector = Connector(weight={"loss_allowance": 0.02})
        connector.process(issue())
        connector.process(give_back("R-1", 4))
        # 116 x 1.02 = 118.32 kg < 120 kg.
        self.assertEqual(connector.process(confirm()).code, "E-QTY")

    def test_the_rule_reads_the_weight_not_numbers(self):
        source = inspect.getsource(Connector._confirm)
        for literal in ("1.2", "1.20", "0.05", "1.05"):
            self.assertNotIn(literal, source)

    def test_committed_config_holds_the_weight(self):
        committed = json.loads((PROJECT / "config.json").read_text())
        self.assertEqual(committed["weight"], {"kg_per_piece": 1.2, "loss_allowance": 0.05})
        self.assertEqual(load_config()["weight"], {"kg_per_piece": 1.2, "loss_allowance": 0.05})
        self.assertEqual(DEFAULT_WEIGHT, {"kg_per_piece": 1.20, "loss_allowance": 0.05})

    def test_config_without_weight_takes_the_default(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "config.json"
            path.write_text(json.dumps({"mode": "test", "live_password": ""}))
            self.assertEqual(load_config(path)["weight"], DEFAULT_WEIGHT)

    def test_bad_weight_values_are_refused(self):
        for weight in ({"kg_per_piece": 0}, {"kg_per_piece": "1.2"}, {"loss_allowance": 2},
                       {"loss_allowance": -0.1}, {"loss_allowance": True}, [1.2, 0.05]):
            with tempfile.TemporaryDirectory() as temp:
                path = Path(temp) / "config.json"
                path.write_text(json.dumps({"mode": "test", "live_password": "", "weight": weight}))
                with self.assertRaises(ConfigError, msg=repr(weight)) as caught:
                    load_config(path)
                self.assertEqual(str(caught.exception), inbox.WEIGHT_SENTENCE)

    def test_the_desk_uses_the_weight_it_was_given(self):
        desk = server.Desk(weight={"kg_per_piece": 1.2, "loss_allowance": 0.0})
        desk.run("issue")
        desk.run("return")
        self.assertEqual(desk.run("confirm-10")["code"], "E-QTY")
        desk.reset()
        self.assertEqual(desk.connector.weight["loss_allowance"], 0.0)


class HoldAndCancelRules(unittest.TestCase):
    """US-082 and BR-03."""

    def test_hold_then_issue_is_closed_and_raw_stays_500(self):
        connector = Connector()
        self.assertEqual(connector.process(order("hold", "H-1")).status, "Posted")
        result = connector.process(issue())
        self.assertEqual(result.code, "E-CLOSED")
        self.assertEqual(result.message, "Work order is on hold.")
        self.assertEqual(connector.kg()["raw"], 500)
        self.assertEqual(connector.ledger.entries, [])

    def test_every_message_kind_is_closed_while_held(self):
        connector = with_metal()
        connector.process(order("hold", "H-1"))
        messages = [
            give_back("R-1", 4),
            confirm(),
            {"kind": "disposition", "id": "D-REJ", "work_order": "WO-2401", "item": "CR-100",
             "qty": 2, "decision": "rejection", "reason": "forge-crack"},
            {"kind": "receipt", "id": "C-1", "work_order": "WO-2401", "item": "CR-100", "qty": 40, "batch": "B-1"},
        ]
        for message in messages:
            self.assertEqual(connector.process(message).code, "E-CLOSED", message["kind"])
        self.assertEqual(len(connector.ledger.entries), 1)
        self.assertEqual(connector.confirmations, [])

    def test_a_held_order_does_not_close_another_order(self):
        connector = Connector()
        connector.process(order("hold", "H-2", work_order="WO-2402"))
        self.assertEqual(connector.process(issue()).status, "Posted")

    def test_resume_opens_the_order_again(self):
        connector = Connector()
        connector.process(order("hold", "H-1"))
        self.assertEqual(connector.process(order("resume", "RS-1")).message, "Work order resumed.")
        self.assertEqual(connector.process(issue()).status, "Posted")

    def test_resume_on_an_open_order_is_refused(self):
        self.assertEqual(Connector().process(order("resume", "RS-1")).code, "E-OP")

    def test_cancel_after_j100_is_refused_until_the_120_kg_is_back(self):
        connector = with_metal()
        refused = connector.process(order("cancel", "X-1"))
        self.assertEqual(refused.code, "E-QTY")
        self.assertEqual(refused.message, "Steel is still out. Return or scrap it before cancel.")
        self.assertEqual(connector.order_state, {})
        connector.process(give_back("R-120", 120))
        self.assertEqual(connector.process(order("cancel", "X-1")).status, "Posted")
        self.assertEqual(connector.order_state, {"WO-2401": "cancelled"})
        self.assertEqual(connector.kg(), {"raw": 500.0, "wip": 0.0})

    def test_cancel_is_final(self):
        connector = Connector()
        connector.process(order("cancel", "X-1"))
        for kind, ref in (("resume", "RS-1"), ("hold", "H-1")):
            result = connector.process(order(kind, ref))
            self.assertEqual(result.code, "E-CLOSED")
            self.assertEqual(result.message, "Work order is cancelled.")
        self.assertEqual(connector.process(issue()).message, "Work order is cancelled.")

    def test_resending_the_release_after_cancel_leaves_it_cancelled(self):
        desk = server.Desk()
        desk.run("cancel")
        row = desk.release()
        self.assertEqual(row["code"], "E-CLOSED")
        self.assertFalse(desk.released)
        desk.release()
        self.assertFalse(desk.released)
        self.assertEqual(desk.payload()["order_state"], "cancelled")

    def test_hold_cancel_and_release_use_three_different_keys(self):
        connector = Connector()
        self.assertEqual(connector.process(order("hold", "WO-KEY")).status, "Posted")
        self.assertEqual(connector.process(order("resume", "WO-KEY")).code, "")
        self.assertEqual(connector.process(order("cancel", "WO-KEY")).code, "")
        repeat = connector.process(order("hold", "WO-KEY"))
        self.assertEqual(repeat.code, "E-DUP")
        self.assertEqual(connector.order_events, [
            ("WO-KEY", "WO-2401", "hold"), ("WO-KEY", "WO-2401", "resume"), ("WO-KEY", "WO-2401", "cancel"),
        ])
        # The release is the desk's own button, not a message with an id.
        desk = server.Desk()
        desk.run("hold")
        self.assertEqual(desk.release()["id"], "release")

    def test_a_repeated_hold_is_not_applied_twice(self):
        connector = Connector()
        connector.process(order("hold", "H-1"))
        connector.process(order("resume", "RS-1"))
        repeat = connector.process(order("hold", "H-1"))
        self.assertEqual(repeat.code, "E-DUP")
        self.assertEqual(connector.order_state, {})

    def test_hold_moves_no_stock(self):
        connector = with_metal()
        before = (connector.kg(), connector.pieces(), len(connector.ledger.entries))
        connector.process(order("hold", "H-1"))
        self.assertEqual((connector.kg(), connector.pieces(), len(connector.ledger.entries)), before)

    def test_the_desk_catalog_has_hold_resume_and_cancel_on_the_order_screen(self):
        ids = {item["id"]: item["screen"] for item in server.CATALOG}
        for action in ("hold", "resume", "cancel"):
            self.assertEqual(ids[action], "order")
            self.assertEqual(server.ACTIONS[action]["message"]["kind"], action)


if __name__ == "__main__":
    unittest.main()
