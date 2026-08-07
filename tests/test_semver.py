import unittest

from dev_forge.semver import satisfies


class SemverTests(unittest.TestCase):
    def test_caret(self):
        self.assertTrue(satisfies("1.95.3", "^1.80.0"))
        self.assertFalse(satisfies("1.79.0", "^1.80.0"))

    def test_comparator_and_or(self):
        self.assertTrue(satisfies("1.95.3", ">=1.90.0 <2.0.0"))
        self.assertTrue(satisfies("1.95.3", "^1.99.0 || ^1.90.0"))

    def test_tilde_x_and_hyphen(self):
        self.assertTrue(satisfies("1.95.3", "~1.95.0"))
        self.assertTrue(satisfies("1.95.3", "1.95.x"))
        self.assertTrue(satisfies("1.95.3", "1.90.0 - 1.96.0"))


if __name__ == "__main__":
    unittest.main()
