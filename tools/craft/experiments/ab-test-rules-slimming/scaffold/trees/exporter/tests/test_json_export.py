import unittest

from reportbay.registry import export


class JsonExportTest(unittest.TestCase):
    def test_rows_come_out_as_sorted_key_json(self):
        self.assertEqual(export([{"b": 2, "a": 1}], "json"), '[{"a": 1, "b": 2}]')
