import unittest
from energyhome.cli import main

class TestCli(unittest.TestCase):
    def test_report_success(self):
        result = main(['report','--input','test.csv'])
        self.assertEqual(result,0)
    def test_missing_input(self):
        result = main(['report'])
        self.assertEqual(result,2)
    def test_invalid_tariff(self):
        result = main(['report','--input','test.csv','--tariff','wrong tariff'])
        self.assertEqual(result,2)
