import csv
import unittest
from dataclasses import FrozenInstanceError
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

from energyhome.analytics import (
    DailySummary, ReportSummary, WeeklySummary, build_demo1_summary,
    daily_summaries, format_console_report, weekly_summaries,
)
from energyhome.core import detect_events
from energyhome.dataio import generate_demo_consumption, load_demo_profile
from energyhome.domain import (
    CyclicDevice, FixedDevice, LoadProfile, SingleZoneTariff,
    ThermostaticDevice, ValidationError,
)


class TestAnalyticsRegressions(unittest.TestCase):
    def setUp(self):
        self.start = datetime(2026, 1, 15, tzinfo=timezone.utc)
        self.tariff = SingleZoneTariff('single', 'Demo', 'Europe/Kyiv', 2.5)

    def test_local_midnight_splits_days_with_real_core(self):
        start = self.start + timedelta(hours=21, minutes=45)
        profile = LoadProfile([start, start + timedelta(minutes=15)], [1, 2], 15)
        summaries = daily_summaries(profile, self.tariff)
        self.assertEqual([s.day.isoformat() for s in summaries], ['2026-01-15', '2026-01-16'])
        self.assertEqual([s.interval_count for s in summaries], [1, 1])
        self.assertEqual([s.energy_kwh for s in summaries], [0.25, 0.5])
        self.assertEqual([s.cost_uah for s in summaries], [Decimal('0.63'), Decimal('1.25')])
        self.assertEqual([s.coverage_pct for s in summaries], [1.04, 1.04])

    def test_complete_dst_days_have_full_coverage(self):
        zone = ZoneInfo('Europe/Kyiv')
        for month, day, hours in ((3, 29, 23), (10, 25, 25)):
            with self.subTest(month=month):
                local_start = datetime(2026, month, day, tzinfo=zone)
                start = local_start.astimezone(timezone.utc)
                profile = LoadProfile(
                    [start + timedelta(minutes=15 * i) for i in range(hours * 4)],
                    [1] * (hours * 4), 15,
                )
                summaries = daily_summaries(profile, self.tariff)
                self.assertEqual(len(summaries), 1)
                self.assertEqual(summaries[0].day, local_start.date())
                self.assertEqual(summaries[0].interval_count, hours * 4)
                self.assertEqual(summaries[0].energy_kwh, hours)
                self.assertEqual(summaries[0].cost_uah, Decimal(hours) * Decimal('2.50'))
                self.assertEqual(summaries[0].coverage_pct, 100.0)

    def test_coverage_uses_observed_duration_without_flooring_day_length(self):
        profile = LoadProfile([self.start], [1], 59)
        summary = daily_summaries(profile, self.tariff)[0]
        self.assertEqual(summary.coverage_pct, round(59 / 1440 * 100, 2))

    def test_two_weeks_are_separate_and_sorted(self):
        monday = datetime(2026, 1, 12).date()
        days = [DailySummary(monday + timedelta(days=i), 96, 24, Decimal('60.00'))
                for i in range(14)]
        weeks = weekly_summaries(list(reversed(days)))
        self.assertEqual(len(weeks), 2)
        for week in weeks:
            self.assertEqual(week, WeeklySummary(7, 672, 168, Decimal('420.00')))
        self.assertEqual(weekly_summaries([]), [])

    def test_calendar_week_crosses_new_year_and_preserves_missing_days(self):
        dates = ((2027, 1, 4), (2026, 12, 28), (2027, 1, 3))
        days = [DailySummary(datetime(*value).date(), 1, i + 1, Decimal(i + 1))
                for i, value in enumerate(dates)]
        self.assertEqual(weekly_summaries(days), [
            WeeklySummary(2, 2, 5, Decimal('5.00')),
            WeeklySummary(1, 1, 1, Decimal('1.00')),
        ])

    def test_all_summary_types_prevent_assignment_and_deletion(self):
        summaries = (
            ReportSummary(self.start, self.start + timedelta(minutes=15), 1, 1, Decimal('2.50')),
            DailySummary(self.start.date(), 1, 1, Decimal('2.50')),
            WeeklySummary(1, 1, 1, Decimal('2.50')),
        )
        for summary in summaries:
            with self.subTest(summary=type(summary).__name__):
                with self.assertRaises(FrozenInstanceError):
                    summary.energy_kwh = 999
                with self.assertRaises(FrozenInstanceError):
                    del summary.energy_kwh

    def test_report_displays_exclusive_end_boundary(self):
        profile = LoadProfile([self.start], [1], 15)
        report = format_console_report(build_demo1_summary(profile, self.tariff))
        self.assertEqual(report.splitlines()[0],
                         'Період: [2026-01-15T00:00:00+00:00, 2026-01-15T00:15:00+00:00)')


class TestDeviceRegressions(unittest.TestCase):
    def setUp(self):
        self.start = datetime(2026, 1, 15, tzinfo=timezone.utc)

    def test_profiles_cross_both_dst_transitions_on_utc_grid(self):
        zone = ZoneInfo('Europe/Kyiv')
        for month, day, hours in ((3, 29, 5), (10, 25, 7)):
            with self.subTest(month=month):
                start = datetime(2026, month, day, 0, tzinfo=zone)
                end = datetime(2026, month, day, 6, tzinfo=zone)
                for device in (FixedDevice('f', 1), CyclicDevice('c', 1, hours * 60),
                               ThermostaticDevice('t', 1, 1)):
                    profile = device.profile_for((start, end), 15)
                    self.assertEqual(len(profile), hours * 4)
                    self.assertEqual(profile.energy_kwh(), hours)
                    self.assertEqual(profile.start_time(), start.astimezone(timezone.utc))
                    self.assertEqual(profile.end_time(), end.astimezone(timezone.utc))

    def test_repeated_hour_uses_instants_instead_of_wall_clock_order(self):
        zone = ZoneInfo('Europe/Kyiv')
        start = datetime(2026, 10, 25, 3, 45, tzinfo=zone, fold=0)
        end = datetime(2026, 10, 25, 3, 15, tzinfo=zone, fold=1)
        profile = FixedDevice('f', 1).profile_for((start, end), 15)
        self.assertEqual(len(profile), 2)
        self.assertEqual(profile.energy_kwh(), 0.5)

    def test_cycles_and_start_minutes_require_integers(self):
        for value in (0.5, 1.9, 1.0, True, '15', float('nan'), float('inf')):
            with self.subTest(value=value):
                with self.assertRaises(ValidationError):
                    CyclicDevice('c', 1, value)
                with self.assertRaises(ValidationError):
                    ThermostaticDevice('t', 1, 0.5, value)
                with self.assertRaises(ValidationError):
                    CyclicDevice('c', 1, 15, value)

    def test_invalid_periods_raise_device_validation_errors(self):
        end = self.start + timedelta(hours=1)
        periods = (
            (self.start, self.start), (end, self.start),
            (self.start.replace(tzinfo=None), end), (self.start, end.replace(tzinfo=None)),
            ('2026-01-15', end), (self.start, self.start + timedelta(minutes=16)),
        )
        for period in periods:
            with self.subTest(period=period), self.assertRaises(ValidationError):
                FixedDevice('f', 1).profile_for(period, 15)


class TestEventRegressions(unittest.TestCase):
    def test_nonfinite_and_nonnumeric_thresholds_are_rejected(self):
        for value in (float('nan'), float('inf'), -float('inf'), True, None, '1'):
            for on, off in ((value, 0.2), (0.8, value)):
                with self.subTest(on=on, off=off), self.assertRaises(ValueError):
                    detect_events([], on, off)

    def test_generated_and_saved_profiles_match_real_detector_and_labels(self):
        generated, labels = generate_demo_consumption(42)
        fixture_dir = Path(__file__).parent / 'fixtures'
        with (fixture_dir / 'events.csv').open(encoding='utf-8', newline='') as source:
            expected = tuple((datetime.fromisoformat(row['timestamp']),
                              row['device_id'], row['event_type'])
                             for row in csv.DictReader(source))
        self.assertEqual(labels, expected)
        for profile in (generated, load_demo_profile(fixture_dir / 'demo_consumption.csv')):
            with self.subTest(profile=profile):
                events = detect_events(profile, 1.0, 0.5, 'heater')
                actual = tuple((e.timestamp, e.device_id, e.event_type) for e in events)
                self.assertEqual(actual, expected)
