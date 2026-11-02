"""Cross-module checks for tariff delegation, DST and input validation."""

import json
import os
import subprocess
import sys
import unittest
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory

from energyhome.analytics import (
    SubProfile, build_annual_report, daily_summaries, iter_daily_summaries, weekly_summaries,
)
from energyhome.cli import _parse_datetime
from energyhome.core import calculate_cost
from energyhome.dataio import (
    DataImporterError, SourceConfig, iter_meter_csv, load_demo_profile, parse_timestamp,
)
from energyhome.domain import LoadProfile, tariff_from_config

ROOT = Path(__file__).resolve().parents[1]
REGISTRY = json.loads((ROOT / 'config/tariffs.json').read_text())['tariffs']


class TestTariffCost(unittest.TestCase):
    def test_cost_api_uses_core_rounding_for_fractional_zero_and_empty_profiles(self):
        start = datetime(2026, 1, 15, 20, 45, tzinfo=UTC)
        for tariff_id in REGISTRY:
            tariff = tariff_from_config(tariff_id, REGISTRY)
            for powers in ([.008, .008], [0, 0], [1, 2], []):
                with self.subTest(tariff=tariff_id, powers=powers):
                    profile = LoadProfile([start + timedelta(minutes=15*i)
                                           for i in range(len(powers))], powers, 15)
                    expected = calculate_cost(profile, tariff).total_cost
                    self.assertEqual(tariff.cost(profile), expected)
            self.assertEqual(tariff.cost(None), 0)
        tiny = LoadProfile([start], [.008], 15)
        self.assertEqual(tariff_from_config('single', REGISTRY).cost(tiny), .01)

    def test_reports_agree_across_gaps_partial_days_and_dst_for_every_tariff(self):
        starts = [datetime(2026, 3, 28, 21, 45, tzinfo=UTC),
                  datetime(2026, 10, 24, 20, 45, tzinfo=UTC),
                  datetime(2026, 12, 31, 21, 45, tzinfo=UTC)]
        offsets = [0, 1, 4, 96, 97, 199]
        powers = [.008, .008, 0, 2, .013, 1]
        for start in starts:
            profile = SubProfile([start + timedelta(minutes=15*i) for i in offsets], powers, 15)
            for tariff_id in REGISTRY:
                with self.subTest(start=start, tariff=tariff_id):
                    tariff = tariff_from_config(tariff_id, REGISTRY)
                    days = daily_summaries(profile, tariff)
                    self.assertEqual(days, list(iter_daily_summaries(iter(profile), tariff)))
                    total = calculate_cost(profile, tariff)
                    self.assertEqual(sum(d.cost_uah for d in days), Decimal(str(total.total_cost)))
                    self.assertEqual(sum(w.cost_uah for w in weekly_summaries(days)),
                                     Decimal(str(total.total_cost)))
                    report = build_annual_report(iter(profile_to_readings(profile)), tariff)
                    for day in days:
                        row = (f'{day.day} | {day.interval_count} | {day.energy_kwh:.4f} | '
                               f'{day.cost_uah:.2f} | {day.coverage_pct:.2f}%')
                        self.assertIn(row, report)
                    self.assertIn(f'Вартість: {tariff.cost(profile):.2f} UAH', report)


def profile_to_readings(profile):
    from energyhome.domain import MeterReading
    return [MeterReading(t, p) for t, p in profile]


class TestSourceOptions(unittest.TestCase):
    def test_invalid_source_options_fail_before_csv_parsing(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / 'empty.csv'
            path.write_text('timestamp,power_kw\n')
            options = [{'delimiter': ';;'}, {'delimiter': None}, {'timestamp_format': None},
                       {'timezone': []}, {'timezone': 'Unknown/Zone'}, {'power_unit': []},
                       {'step_minutes': 10**100}, {'step_minutes': True},
                       {'timestamp_columns': ()}, {'power_column': None},
                       {'missing_values': (None,)}]
            for kwargs in options:
                with self.subTest(kwargs=kwargs), self.assertRaises(DataImporterError):
                    list(iter_meter_csv(path, SourceConfig(**kwargs)))

    def test_offsets_preserve_fall_fold_and_spring_instants_across_modules(self):
        cases = [
            (['2026-10-25T03:15:00+03:00', '2026-10-25T03:15:00+02:00'], 60),
            (['2026-03-29T02:45:00+02:00', '2026-03-29T04:00:00+03:00'], 15),
        ]
        with TemporaryDirectory() as directory:
            path = Path(directory) / 'meter.csv'
            for labels, step in cases:
                path.write_text('timestamp,power_kw\n' + '\n'.join(f'{t},1' for t in labels))
                cfg = SourceConfig(step_minutes=step)
                profile = load_demo_profile(path, cfg)
                self.assertEqual(profile.timestamps, [_parse_datetime(t) for t in labels])
                self.assertEqual(profile.timestamps[1] - profile.timestamps[0],
                                 timedelta(minutes=step))
                for tariff_id in REGISTRY:
                    tariff = tariff_from_config(tariff_id, REGISTRY)
                    days = daily_summaries(profile, tariff)
                    self.assertEqual(len(days), 1)
                    self.assertEqual(sum(d.cost_uah for d in days),
                                     Decimal(str(tariff.cost(profile))))
            with self.assertRaisesRegex(ValueError, 'Nonexistent'):
                parse_timestamp('2026-03-29T03:30:00', cfg.timestamp_format, 'Europe/Kyiv')


class TestCliInputValidation(unittest.TestCase):
    def setUp(self):
        self.folder = TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.root = Path(self.folder.name)
        self.path = self.root / 'meter.csv'
        self.path.write_bytes((ROOT / 'data/demo/meter_15min.csv').read_bytes())
        self.config = self.root / 'tariffs.json'
        self.config.write_bytes((ROOT / 'config/tariffs.json').read_bytes())

    def command(self, *args):
        return subprocess.run(
            [sys.executable, '-m', 'energyhome', 'report', '--input', str(self.path),
             '--tariffs', str(self.config), *args], cwd=self.root,
            env=dict(os.environ, PYTHONPATH=str(ROOT / 'src')),
            text=True, capture_output=True, timeout=20,
        )

    def assert_readable_failure(self, result):
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertEqual(result.stdout, '')
        self.assertTrue(result.stderr)
        self.assertNotIn('Traceback', result.stderr)

    def test_invalid_metadata_types_and_out_of_range_steps_are_readable_errors(self):
        for step in [None, [], {}, '15', True, 0, -1, 1.5, 10**100]:
            with self.subTest(step=step):
                content = json.dumps({'step_minutes': step})
                self.path.with_suffix('.quality.json').write_text(content)
                self.assert_readable_failure(self.command())

    def test_units_and_schema_metadata_are_checked(self):
        original = json.loads(self.config.read_text())
        for key, value in [('schema_version', True), ('schema_version', 99),
                           ('currency', 'USD'), ('energy_unit', 'Wh'), ('synthetic', 'yes')]:
            with self.subTest(key=key, value=value):
                config = dict(original, **{key: value})
                self.config.write_text(json.dumps(config))
                self.assert_readable_failure(self.command())

    def test_fall_fold_bounds_use_instants_in_csv_core_and_cli(self):
        self.path.write_text('timestamp,power_kw\n2026-10-25T03:15:00+03:00,1\n'
                             '2026-10-25T03:15:00+02:00,1\n')
        self.path.with_suffix('.quality.json').write_text('{"step_minutes": 60}')
        result = self.command('--from', '2026-10-25T03:15:00+03:00',
                               '--to', '2026-10-25T03:15:00+02:00')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('Інтервалів: 1\nЕнергія: 1.0 kWh\nВартість: 2.50 UAH', result.stdout)

    def test_out_of_range_interval_is_a_readable_error(self):
        self.path.write_text('timestamp,power_kw\n9999-12-31T23:59:00Z,1\n')
        self.assert_readable_failure(self.command())


class TestCacheParameterTypes(unittest.TestCase):
    def setUp(self):
        self.folder = TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.path = Path(self.folder.name) / 'data.csv'
        self.path.write_text('timestamp,power_kw\n')

    def test_invalid_bool_step_cannot_reuse_integer_config_result(self):
        from energyhome.infra import cache_profile

        @cache_profile
        def load(path, source_config):
            source_config.validate()
            return source_config.step_minutes

        self.assertEqual(load(self.path, SourceConfig(step_minutes=1)), 1)
        with self.assertRaises(DataImporterError):
            load(self.path, SourceConfig(step_minutes=True))

    def test_dictionary_parameter_types_are_distinct(self):
        from energyhome.infra import cache_profile

        @cache_profile
        def load(path, config):
            return type(config['value']).__name__

        self.assertEqual(load(self.path, {'value': 1}), 'int')
        self.assertEqual(load(self.path, {'value': True}), 'bool')
        self.assertEqual(load(self.path, {'value': 1.0}), 'float')

    def test_repeated_local_hour_is_a_distinct_cache_parameter(self):
        from zoneinfo import ZoneInfo
        from energyhome.infra import cache_profile

        @cache_profile
        def load(path, start):
            return start.astimezone(UTC)

        first = datetime(2026, 10, 25, 3, 15, tzinfo=ZoneInfo('Europe/Kyiv'), fold=0)
        second = first.replace(fold=1)
        self.assertEqual(load(self.path, second) - load(self.path, first), timedelta(hours=1))
