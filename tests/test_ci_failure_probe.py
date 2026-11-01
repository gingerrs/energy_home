import unittest


class TestCiFailureProbe(unittest.TestCase):
    def test_intentional_failure_is_reported_by_ci(self):
        self.fail("Intentional W2-E CI failure probe; never merge this test")
