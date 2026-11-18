"""Desk.apply must not change the shared ACTIONS catalog."""
import importlib
import unittest
from unittest import mock

import server
from server import ACTIONS, Desk


class DeskApplyTests(unittest.TestCase):
    def test_importing_server_does_not_bind_the_port(self):
        with mock.patch("http.server.ThreadingHTTPServer") as built:
            importlib.reload(server)
            built.assert_not_called()
        importlib.reload(server)

    def test_catalog_qty_is_still_the_integer_120_after_a_click(self):
        before = ACTIONS["issue"]["message"]["qty"]
        self.assertIsInstance(before, int)
        self.assertEqual(before, 120)

        desk = Desk()
        row = desk.run("issue")

        after = ACTIONS["issue"]["message"]["qty"]
        self.assertIsInstance(after, int)
        self.assertEqual(after, 120)
        self.assertEqual(row["document"], "STE-0001")
        self.assertEqual(desk.connector.kg()["raw"], 380)

    def test_a_typed_bad_qty_is_refused_and_the_catalog_is_untouched(self):
        desk = Desk()
        typed = {
            "kind": "issue", "id": "J-TXT", "work_order": "WO-2401",
            "item": "ST-20", "qty": "abc", "uom": "Kg", "batch": "H-441",
            "from_warehouse": "Raw Material Store", "to_warehouse": "Forging WIP",
        }

        row = desk.apply("", typed)

        self.assertEqual(row["code"], "E-QTY")
        self.assertEqual(desk.connector.kg()["raw"], 500)
        self.assertEqual(typed["qty"], "abc")
        self.assertEqual(ACTIONS["issue"]["message"]["qty"], 120)

    def test_every_catalog_message_survives_one_click(self):
        snapshot = {key: dict(spec["message"]) for key, spec in ACTIONS.items()}
        desk = Desk()
        for key in ACTIONS:
            desk.run(key)
        for key, spec in ACTIONS.items():
            self.assertEqual(spec["message"], snapshot[key], key)

    def test_two_desks_do_not_share_a_connector(self):
        a, b = Desk(), Desk()
        a.run("issue")
        self.assertEqual(a.connector.kg()["raw"], 380)
        self.assertEqual(b.connector.kg()["raw"], 500)
        self.assertIsNot(a.connector, b.connector)


if __name__ == "__main__":
    unittest.main()
