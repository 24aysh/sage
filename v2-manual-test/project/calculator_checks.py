"""Behavioral checks for the calculator fixture."""

import unittest

from calculator import add


class AddTests(unittest.TestCase):
    def test_adds_positive_integers(self) -> None:
        self.assertEqual(add(7, 5), 12)

    def test_adds_negative_and_positive_integers(self) -> None:
        self.assertEqual(add(-2, 5), 3)


if __name__ == "__main__":
    unittest.main()
