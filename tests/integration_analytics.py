import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from importlib import import_module
from zoneinfo import ZoneInfo

from energyhome.analytics import build_demo1_summary, format_console_report


class TestAnalyticsIntegration(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            domain = import_module("energyhome.domain")
            import_module("energyhome.core")
        except ModuleNotFoundError as error:
            if error.name in {"energyhome.domain", "energyhome.core"}:
                message = "Integration requires real W1-A domain and W1-C core in the package"
                raise RuntimeError(message) from error
            raise
        cls.profile_type = domain.LoadProfile
        cls.tariff_type = domain.SingleZoneTariff

    def setUp(self):
        self.start = datetime(2026, 1, 15, tzinfo=timezone.utc)
        self.tariff = self.tariff_type("single", "Demo", "UTC", 2.5)

    def test_shared_fixture_with_real_core(self):
        timestamps = [self.start + timedelta(minutes=15 * i) for i in range(4)]
        profile = self.profile_type(timestamps, [1.0, 2.0, 0.0, 1.0], 15)

        summary = build_demo1_summary(profile, self.tariff)

        self.assertEqual(summary.period_start, self.start)
        self.assertEqual(summary.period_end_exclusive, self.start + timedelta(hours=1))
        self.assertEqual(summary.interval_count, 4)
        self.assertAlmostEqual(summary.energy_kwh, 1.0)
        self.assertEqual(summary.cost_uah, Decimal("2.50"))
        self.assertIn("Вартість: 2.50 UAH", format_console_report(summary))

    def test_empty_real_profile(self):
        profile = self.profile_type([], [], 15)

        summary = build_demo1_summary(profile, self.tariff)

        self.assertIsNone(summary.period_start)
        self.assertIsNone(summary.period_end_exclusive)
        self.assertEqual(summary.interval_count, 0)
        self.assertEqual(summary.energy_kwh, 0.0)
        self.assertEqual(summary.cost_uah, Decimal("0.00"))
        self.assertIn("немає даних", format_console_report(summary))

    def test_period_across_repeated_dst_hour(self):
        zone = ZoneInfo("Europe/Kyiv")
        timestamps = [
            datetime(2026, 10, 25, 3, 45, tzinfo=zone, fold=0),
            datetime(2026, 10, 25, 3, 0, tzinfo=zone, fold=1),
        ]
        profile = self.profile_type(timestamps, [1, 1], 15)

        summary = build_demo1_summary(profile, self.tariff)

        self.assertEqual(summary.period_start, datetime(2026, 10, 25, 0, 45, tzinfo=timezone.utc))
        self.assertEqual(
            summary.period_end_exclusive,
            datetime(2026, 10, 25, 1, 15, tzinfo=timezone.utc),
        )
        self.assertEqual(summary.interval_count, 2)
        self.assertEqual(summary.energy_kwh, 0.5)
        self.assertEqual(summary.cost_uah, Decimal("1.25"))
