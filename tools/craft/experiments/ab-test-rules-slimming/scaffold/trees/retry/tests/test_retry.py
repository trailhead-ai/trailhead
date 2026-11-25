import unittest

from courier.retry import deliver


class DeliverTest(unittest.TestCase):
    def test_a_first_try_success_is_returned(self):
        self.assertEqual(deliver(lambda m: m.upper(), "ok"), "OK")
