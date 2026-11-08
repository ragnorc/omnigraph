"""A short stress run, so that `python3 -m pytest` covers the generator;
`run.py` runs the full 50,000 terms."""
import unittest

from tests.util import C   # noqa: F401  (puts the spike on sys.path)
from gq3 import stress


class Stress(unittest.TestCase):
    def test_two_thousand_terms(self):
        self.assertEqual(stress.main(["--terms", "2000", "--seed", "7", "--quiet"]), 0)


if __name__ == "__main__":
    unittest.main()
