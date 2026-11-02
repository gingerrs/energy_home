"""Regressions for cache invalidation, local-day totals and plot coordinates."""

import os
import unittest
from dataclasses import FrozenInstanceError
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
from zoneinfo import ZoneInfo

from energyhome.analytics import (
    daily_summaries, iter_daily_summaries, plot_daily_profile,
    plot_device_stack, weekly_summaries,
)
from energyhome.core import calculate_cost
from energyhome.dataio import SourceConfig
from energyhome.domain import (
    CyclicDevice, LoadProfile, MeterDataError, MeterReading, Schedule, ScheduledRun,
    SingleZoneTariff, ValidationError,
)
from energyhome.infra import cache_profile, log_call


class TestCache(unittest.TestCase):
    def setUp(self):
        self.folder = TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.path = Path(self.folder.name) / 'meter.csv'
        self.path.write_text('first')

    def test_bound_config_and_file_version_determine_cache(self):
        calls = []

        @cache_profile
        def load(path, source_config=SourceConfig()):
            calls.append(source_config)
            return Path(path).read_text(), source_config.timezone

        utc = SourceConfig(timezone='UTC')
        kyiv = SourceConfig(timezone='Europe/Kyiv')
        self.assertEqual(load(self.path, utc), ('first', 'UTC'))
        self.assertEqual(load(path=self.path, source_config=utc), ('first', 'UTC'))
        self.assertEqual(len(calls), 1)
        self.assertEqual(load(self.path, kyiv), ('first', 'Europe/Kyiv'))
        self.assertEqual(len(calls), 2)
        stamp = self.path.stat().st_mtime_ns
        self.path.write_text('second')
        os.utime(self.path, ns=(stamp + 1000000, stamp + 1000000))
        self.assertEqual(load(self.path, kyiv), ('second', 'Europe/Kyiv'))
        self.assertEqual(len(calls), 3)

    def test_dict_configuration_works_and_changes_invalidate(self):
        calls = []

        @cache_profile
        def load(path, config):
            calls.append(config.copy())
            return config['timezone']

        config = {'timezone': 'UTC', 'columns': ['power_kw']}
        self.assertEqual(load(self.path, config), 'UTC')
        self.assertEqual(load(self.path, dict(reversed(list(config.items())))), 'UTC')
        self.assertEqual(len(calls), 1)
        config['timezone'] = 'Europe/Kyiv'
        self.assertEqual(load(self.path, config), 'Europe/Kyiv')
        self.assertEqual(len(calls), 2)

    def test_iterator_is_not_consumed_or_cached(self):
        calls = []
        consumed = []

        @cache_profile
        def load(path):
            calls.append(path)

            def rows():
                for i in range(2):
                    consumed.append(i)
                    yield i
            return rows()

        first = load(self.path)
        self.assertEqual(consumed, [])
        self.assertEqual(list(first), [0, 1])
        self.assertEqual(list(load(self.path)), [0, 1])
        self.assertEqual(len(calls), 2)

    def test_exceptions_are_not_cached(self):
        calls = []

        @cache_profile
        def load(path):
            calls.append(path)
            raise ValueError('bad input')

        for _ in range(2):
            with self.assertRaisesRegex(ValueError, 'bad input'):
                load(self.path)
        self.assertEqual(len(calls), 2)

    def test_log_call_preserves_result_error_and_metadata(self):
        @log_call
        def divide(value, divisor):
            """Divide a value."""
            return value / divisor

        self.assertEqual(divide(8, 2), 4)
        self.assertEqual(divide.__name__, 'divide')
        self.assertEqual(divide.__doc__, 'Divide a value.')
        with self.assertLogs('energyhome.calls', level='DEBUG') as logs:
            with self.assertRaises(ZeroDivisionError):
                divide(8, 0)
        self.assertIn('ZeroDivisionError', '\n'.join(logs.output))


class TestLocalDays(unittest.TestCase):
    def setUp(self):
        self.tariff = SingleZoneTariff('single', 'Test', 'Europe/Kyiv', 2.5)

    def test_half_cent_rounding_matches_core_across_local_midnight(self):
        start = datetime(2026, 1, 15, 21, 45, tzinfo=UTC)
        profile = LoadProfile([start, start + timedelta(minutes=15)], [.008, .008], 15)
        daily = daily_summaries(profile, self.tariff)
        streamed = list(iter_daily_summaries(iter(profile), self.tariff))
        self.assertEqual(streamed, daily)
        self.assertEqual([str(d.day) for d in daily], ['2026-01-15', '2026-01-16'])
        expected = Decimal(str(calculate_cost(profile, self.tariff).total_cost))
        self.assertEqual(expected, Decimal('0.01'))
        self.assertEqual(sum(d.cost_uah for d in daily), expected)
        self.assertEqual(sum(w.cost_uah for w in weekly_summaries(daily)), expected)

    def test_streaming_dst_days_match_batch_with_full_coverage(self):
        zone = ZoneInfo('Europe/Kyiv')
        for month, day, hours in [(3, 29, 23), (10, 25, 25)]:
            with self.subTest(month=month):
                start = datetime(2026, month, day, tzinfo=zone).astimezone(UTC)
                profile = LoadProfile([start + timedelta(minutes=15*i) for i in range(hours*4)],
                                      [1] * (hours*4), 15)
                streamed = list(iter_daily_summaries(
                    (MeterReading(t, p) for t, p in profile), self.tariff))
                self.assertEqual(streamed, daily_summaries(profile, self.tariff))
                self.assertEqual(len(streamed), 1)
                self.assertEqual(streamed[0].coverage_pct, 100)
                self.assertEqual(streamed[0].interval_count, hours*4)

    def test_empty_profile_has_no_dates_and_invalid_data_is_validation_error(self):
        empty = LoadProfile([], [], 15)
        self.assertEqual((len(empty), empty.energy_kwh(), empty.peak_kw()), (0, 0, 0))
        self.assertIsNone(empty.start_time())
        self.assertIsNone(empty.end_time())
        self.assertTrue(issubclass(MeterDataError, ValidationError))


class TestScheduleRegistry(unittest.TestCase):
    def test_known_devices_and_stable_order(self):
        start = datetime(2026, 1, 15, tzinfo=UTC)
        first, second = ScheduledRun('washer', start, 15), ScheduledRun('heater', start, 30)
        schedule = Schedule([first, second], device_ids=['washer', 'heater'])
        self.assertEqual([r.device_id for r in schedule], ['heater', 'washer'])
        with self.assertRaises(FrozenInstanceError):
            schedule._runs = ()

    def test_unknown_device_and_invalid_run_are_rejected(self):
        start = datetime(2026, 1, 15, tzinfo=UTC)
        run = ScheduledRun('unknown', start, 15)
        with self.assertRaisesRegex(ValidationError, 'Unknown device'):
            Schedule([run], device_ids=['known'])
        with self.assertRaisesRegex(ValidationError, 'Unknown device'):
            Schedule([run])
        for duration in (0, -15, True, 1.5):
            with self.subTest(duration=duration), self.assertRaises(ValidationError):
                ScheduledRun('known', start, duration)


class TestPlotCoordinates(unittest.TestCase):
    def test_repeated_hour_has_distinct_positions(self):
        start = datetime(2026, 10, 25, tzinfo=ZoneInfo('Europe/Kyiv')).astimezone(UTC)
        stamps = [(start + timedelta(minutes=15*i)).astimezone(ZoneInfo('Europe/Kyiv'))
                  for i in range(100)]
        fig = plot_daily_profile(stamps, [1]*100)
        x = fig.axes[0].lines[0].get_xdata(orig=False)
        self.assertEqual(len(set(x)), 100)
        self.assertTrue(all(a < b for a, b in zip(x, x[1:])))

    def test_tariff_transition_is_at_interval_start(self):
        from energyhome.domain import TariffZone, TwoZoneTariff
        tariff = TwoZoneTariff('two', 'Test', 'Europe/Kyiv', [
            TariffZone('night', 1380, 420, 1.25), TariffZone('day', 420, 1380, 2.5)])
        stamps = [datetime(2026, 1, 15, 20, 45, tzinfo=UTC),
                  datetime(2026, 1, 15, 21, tzinfo=UTC)]
        fig = plot_daily_profile(stamps, [1, 1], tariff)
        line = fig.axes[1].lines[0]
        vertices = line.get_path().vertices
        change = [vertices[i][0] for i in range(1, len(vertices))
                  if vertices[i][1] != vertices[i-1][1]]
        self.assertEqual(change, [line.get_xdata(orig=False)[1]])

    def test_device_models_sum_to_stack_and_wrong_totals_fail(self):
        start = datetime(2026, 1, 15, tzinfo=UTC)
        period = start, start + timedelta(hours=1)
        devices = [CyclicDevice('a', 1, 30), CyclicDevice('b', 1, 15, 15),
                   CyclicDevice('c', 1, 15, 45)]
        profiles = {d.device_id: d.profile_for(period, 15) for d in devices}
        stamps = profiles['a'].timestamps
        series = {name: p.power_kw for name, p in profiles.items()}
        total = [1, 2, 0, 1]
        fig = plot_device_stack(stamps, series, total)
        for i, power in enumerate(total):
            self.assertEqual(sum(layer[i].get_height() for layer in fig.axes[0].containers), power)
        for kwargs in ({}, {'device_series': series, 'total_power_kw': [9]*4},
                       {'total_power_kw': [1]}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                plot_device_stack(stamps, **kwargs)
