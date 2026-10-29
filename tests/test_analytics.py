import io
import unittest
from contextlib import redirect_stdout
from dataclasses import FrozenInstanceError
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import ModuleType, SimpleNamespace
from unittest.mock import MagicMock, Mock, patch, sentinel

from energyhome.analytics import ReportSummary, build_demo1_summary, format_console_report


class TestAnalytics(unittest.TestCase):
    def setUp(self):
        self.start = datetime(2026, 1, 15, tzinfo=timezone.utc)
        self.timestamps = [self.start + timedelta(minutes=15 * i) for i in range(4)]
        self.profile = self.make_profile(self.timestamps, [1.0, 2.0, 0.0, 1.0], 15)
        self.tariff = sentinel.tariff
        self.calculate_cost = Mock(
            return_value=SimpleNamespace(total_energy_kwh=1.0, total_cost_uah=Decimal("2.50"))
        )
        core = ModuleType("energyhome.core")
        core.calculate_cost = self.calculate_cost
        patcher = patch.dict("sys.modules", {"energyhome.core": core})
        patcher.start()
        self.addCleanup(patcher.stop)

    def make_profile(self, timestamps, powers, step):
        profile = MagicMock(
            spec=["__len__", "start_time", "end_time", "timestamps", "power_kw"]
        )
        profile.__len__.return_value = len(timestamps)
        profile.start_time.return_value = timestamps[0] if timestamps else None
        profile.end_time.return_value = (
            timestamps[-1] + timedelta(minutes=step) if timestamps else None
        )
        profile.timestamps = list(timestamps)
        profile.power_kw = list(powers)
        return profile

    def test_shared_fixture_summary(self):
        summary = build_demo1_summary(self.profile, self.tariff)

        self.assertEqual(summary.period_start, self.start)
        self.assertEqual(summary.period_end_exclusive, self.start + timedelta(hours=1))
        self.assertEqual(summary.interval_count, 4)
        self.assertEqual(summary.energy_kwh, 1.0)
        self.assertEqual(summary.cost_uah, Decimal("2.50"))
        self.calculate_cost.assert_called_once_with(self.profile, self.tariff)

    def test_summary_uses_core_values_without_recalculating(self):
        self.calculate_cost.return_value = SimpleNamespace(
            total_energy_kwh=7.125,
            total_cost_uah=Decimal("19.37"),
        )

        summary = build_demo1_summary(self.profile, self.tariff)
        report = format_console_report(summary)

        self.assertEqual(summary.energy_kwh, 7.125)
        self.assertEqual(summary.cost_uah, Decimal("19.37"))
        self.assertIn("Енергія: 7.125 kWh", report)
        self.assertIn("Вартість: 19.37 UAH", report)
        self.calculate_cost.assert_called_once_with(self.profile, self.tariff)

    def test_empty_profile(self):
        profile = self.make_profile([], [], 15)

        summary = build_demo1_summary(profile, self.tariff)

        self.assertEqual(summary, ReportSummary(None, None, 0, 0.0, Decimal("0.00")))
        self.assertEqual(
            format_console_report(summary),
            "Період: немає даних\nІнтервалів: 0\n"
            "Енергія: 0.0 kWh\nВартість: 0.00 UAH",
        )
        profile.start_time.assert_not_called()
        profile.end_time.assert_not_called()
        self.calculate_cost.assert_not_called()

    def test_console_report(self):
        summary = build_demo1_summary(self.profile, self.tariff)

        self.assertEqual(
            format_console_report(summary),
            "Період: [2026-01-15T00:00:00+00:00, 2026-01-15T01:00:00+00:00)\n"
            "Інтервалів: 4\nЕнергія: 1.0 kWh\nВартість: 2.50 UAH",
        )

    def test_zero_consumption_keeps_the_observed_period(self):
        profile = self.make_profile(self.timestamps, [0.0] * 4, 15)
        self.calculate_cost.return_value = SimpleNamespace(
            total_energy_kwh=0.0,
            total_cost_uah=Decimal("0.00"),
        )

        summary = build_demo1_summary(profile, self.tariff)
        report = format_console_report(summary)

        self.assertEqual(summary.interval_count, 4)
        self.assertEqual(summary.period_start, self.start)
        self.assertEqual(summary.period_end_exclusive, self.start + timedelta(hours=1))
        self.assertNotIn("немає даних", report)
        self.assertIn("Вартість: 0.00 UAH", report)

    def test_money_has_two_decimal_places(self):
        for cost, expected in (("0", "0.00"), ("2.5", "2.50"), ("19.37", "19.37")):
            with self.subTest(cost=cost):
                summary = ReportSummary(self.start, self.start, 1, 0.0, Decimal(cost))
                self.assertIn(f"Вартість: {expected} UAH", format_console_report(summary))

    def test_single_interval_uses_profile_resolution(self):
        for resolution in (5, 15, 60):
            with self.subTest(resolution=resolution):
                profile = self.make_profile([self.start], [1.0], resolution)

                summary = build_demo1_summary(profile, self.tariff)

                self.assertEqual(summary.interval_count, 1)
                self.assertEqual(summary.period_start, self.start)
                self.assertEqual(
                    summary.period_end_exclusive,
                    self.start + timedelta(minutes=resolution),
                )

    def test_period_crosses_year_boundary(self):
        start = datetime(2026, 12, 31, 23, 45, tzinfo=timezone.utc)
        profile = self.make_profile([start], [1.0], 15)

        summary = build_demo1_summary(profile, self.tariff)

        self.assertEqual(summary.period_end_exclusive, datetime(2027, 1, 1, tzinfo=timezone.utc))

    def test_summary_does_not_change_profile(self):
        before = (list(self.profile.timestamps), list(self.profile.power_kw))

        build_demo1_summary(self.profile, self.tariff)

        self.assertEqual((self.profile.timestamps, self.profile.power_kw), before)

    def test_core_errors_are_preserved(self):
        error = RuntimeError("Invalid tariff")
        self.calculate_cost.side_effect = error

        with self.assertRaises(RuntimeError) as caught:
            build_demo1_summary(self.profile, self.tariff)

        self.assertIs(caught.exception, error)

    def test_report_summary_is_immutable(self):
        summary = build_demo1_summary(self.profile, self.tariff)

        with self.assertRaises(FrozenInstanceError):
            summary.cost_uah = Decimal("0.00")

    def test_non_empty_report_requires_period(self):
        for start, end in ((None, self.start), (self.start, None), (None, None)):
            with self.subTest(start=start, end=end):
                summary = ReportSummary(start, end, 1, 1.0, Decimal("2.50"))
                with self.assertRaisesRegex(ValueError, "period boundaries"):
                    format_console_report(summary)

    def test_analytics_does_not_print(self):
        output = io.StringIO()

        with redirect_stdout(output):
            summary = build_demo1_summary(self.profile, self.tariff)
            report = format_console_report(summary)

        self.assertEqual(output.getvalue(), "")
        self.assertIsInstance(report, str)

    def test_period_is_taken_from_domain_without_recalculation(self):
        expected_end = self.start + timedelta(days=2)
        self.profile.end_time.return_value = expected_end

        summary = build_demo1_summary(self.profile, self.tariff)

        self.assertEqual(summary.period_end_exclusive, expected_end)

    def test_empty_summary_does_not_require_core(self):
        profile = self.make_profile([], [], 15)
        with patch.dict("sys.modules", {"energyhome.core": None}):
            summary = build_demo1_summary(profile, self.tariff)

        self.assertEqual(summary, ReportSummary(None, None, 0, 0.0, Decimal("0.00")))

    def test_missing_core_is_not_replaced_by_fake_values(self):
        with patch.dict("sys.modules", {"energyhome.core": None}):
            with self.assertRaises(ModuleNotFoundError):
                build_demo1_summary(self.profile, self.tariff)

    def test_formatter_does_not_call_core(self):
        summary = ReportSummary(
            self.start, self.start + timedelta(hours=1), 4, 1.0, Decimal("2.50")
        )
        self.calculate_cost.side_effect = AssertionError("Formatter must not calculate costs")

        report = format_console_report(summary)

        self.assertIn("Вартість: 2.50 UAH", report)
        self.calculate_cost.assert_not_called()

    def test_none_from_empty_csv_is_an_empty_summary(self):
        summary = build_demo1_summary(None, self.tariff)

        self.assertEqual(summary, ReportSummary(None, None, 0, 0.0, Decimal("0.00")))
        self.assertIn("немає даних", format_console_report(summary))
        self.calculate_cost.assert_not_called()
