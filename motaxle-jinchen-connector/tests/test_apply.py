"""Desk.apply must not change the shared ACTIONS catalog."""
import socket
import unittest

from server import ACTIONS, Desk


def port_open(port=8765):
    with socket.socket() as sock:
        sock.settimeout(0.2)
        return sock.connect_ex(("127.0.0.1", port)) == 0


class DeskApplyTests(unittest.TestCase):
    def test_importing_server_does_not_bind_the_port(self):
        self.assertFalse(port_open())

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
