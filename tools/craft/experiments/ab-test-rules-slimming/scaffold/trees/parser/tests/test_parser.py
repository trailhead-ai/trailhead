import unittest

from kvparse.parser import parse_pairs


class ParsePairsTest(unittest.TestCase):
    def test_splits_pairs_and_trims_space(self):
        self.assertEqual(parse_pairs("a=1; b = 2"), {"a": "1", "b": "2"})

    def test_skips_empty_segments(self):
        self.assertEqual(parse_pairs("a=1;;"), {"a": "1"})
