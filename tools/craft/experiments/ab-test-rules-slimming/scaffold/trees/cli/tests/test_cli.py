import io
import unittest

from addup.cli import main


class AddupTest(unittest.TestCase):
    def test_prints_the_sum(self):
        out = io.StringIO()
        self.assertEqual(main(["1", "2.5"], out=out), 0)
        self.assertEqual(out.getvalue(), "3.5\n")
