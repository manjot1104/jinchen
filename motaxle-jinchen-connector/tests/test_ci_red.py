import unittest


class CiRedTest(unittest.TestCase):
    def test_ci_can_fail(self):
        self.assertEqual(1, 2)


if __name__ == "__main__":
    unittest.main()
