import json
import os
import subprocess
import sys
import unittest
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from zoneinfo import ZoneInfo

import matplotlib.pyplot as plt

from energyhome.analytics import (
    build_annual_report, daily_summaries, plot_daily_profile, plot_device_stack,
)
from energyhome.cli import _parse_datetime
from energyhome.core import calculate_cost, profile_metrics
from energyhome.dataio import DataImporterError, iter_meter_csv
from energyhome.domain import LoadProfile, MeterReading, tariff_from_config


ROOT = Path(__file__).resolve().parents[1]
KYIV = ZoneInfo('Europe/Kyiv')


class TestAnnualReport(unittest.TestCase):
    def setUp(self):
        registry = json.loads((ROOT / 'config/tariffs.json').read_text())['tariffs']
        self.single = tariff_from_config('single', registry)
        self.two = tariff_from_config('two_zone', registry)
        self.start = datetime(2026, 1, 15, tzinfo=UTC)

    def readings(self, powers, start=None, step=15):
        start = self.start if start is None else start
        return (MeterReading(start + timedelta(minutes=i * step), power)
                for i, power in enumerate(powers))

    def tables(self, report):
        lines = report.splitlines()
        daily = lines.index('Добові підсумки: дата | інтервали | kWh | UAH | покриття')
        weekly = lines.index('Тижневі підсумки: дні | інтервали | kWh | UAH')
        return ([line.split(' | ') for line in lines[daily + 1:weekly]],
                [line.split(' | ') for line in lines[weekly + 1:]])

    def test_manual_example_matches_core_and_expected_text(self):
        powers = [1, 2, 0, 1]
        report = build_annual_report(self.readings(powers), self.single, self.two)
        expected = (ROOT / 'docs/demo1/expected_small.txt').read_text(encoding='utf-8')
        self.assertEqual(report + '\n', expected)
        profile = LoadProfile([r.timestamp for r in self.readings(powers)], powers, 15)
        metrics = profile_metrics(profile, self.single)
        self.assertEqual((metrics.peak_kw, metrics.unevenness, metrics.mean_price), (2, 2, 2.5))
        for tariff in (self.single, self.two):
            cost = calculate_cost(profile, tariff)
            self.assertIn(f'{tariff.tariff_id}: {cost.total_cost:.2f}', report)

    def test_year_is_one_pass_and_daily_weekly_totals_match_core(self):
        start = datetime(2026, 1, 1, tzinfo=KYIV).astimezone(UTC)
        powers = [0.013 + (i % 4) / 1000 for i in range(365 * 24)]
        timestamps = [start + timedelta(hours=i) for i in range(len(powers))]
        consumed = []

        def source():
            for stamp, power in zip(timestamps, powers):
                consumed.append(stamp)
                yield MeterReading(stamp, power)

        def check_day(*args):
            if not observed:
                self.assertLessEqual(len(consumed), 25)
            observed.append(len(consumed))
            return daily_summaries(*args)

        observed = []
        with patch('energyhome.analytics.daily_summaries', side_effect=check_day):
            report = build_annual_report(source(), self.single, self.two, step_minutes=60)
        profile = LoadProfile(timestamps, powers, 60)
        cost = calculate_cost(profile, self.single)
        daily, weekly = self.tables(report)
        self.assertEqual(len(daily), 365)
        self.assertEqual(len(consumed), len(powers))
        self.assertTrue(all(row[-1] == '100.00%' for row in daily))
        self.assertEqual(sum(int(row[1]) for row in daily), len(powers))
        self.assertEqual(sum(int(row[1]) for row in weekly), len(powers))
        for rows, energy_column, cost_column in ((daily, 2, 3), (weekly, 2, 3)):
            self.assertEqual(sum(Decimal(row[energy_column]) for row in rows),
                             Decimal(str(cost.energy_kwh)))
            self.assertEqual(sum(Decimal(row[cost_column]) for row in rows),
                             Decimal(str(cost.total_cost)))
        for tariff in (self.single, self.two):
            total = calculate_cost(profile, tariff).total_cost
            self.assertIn(f'{tariff.tariff_id}: {total:.2f}', report)
        metrics = profile_metrics(profile, self.single)
        self.assertIn(f'Середня ціна: {metrics.mean_price}', report)
        self.assertIn(f'Пік: {metrics.peak_kw:g} kW', report)
        unevenness = next(line for line in report.splitlines()
                          if line.startswith('Нерівномірність:'))
        self.assertAlmostEqual(float(unevenness.split(': ')[1]), metrics.unevenness)

    def test_rounding_is_carried_between_days_instead_of_accumulated(self):
        start = datetime(2026, 1, 1, tzinfo=KYIV).astimezone(UTC)
        points = (MeterReading(start + timedelta(days=i), 0.013) for i in range(60))
        report = build_annual_report(points, self.single, self.two, step_minutes=60)
        daily, weekly = self.tables(report)
        self.assertIn('single: 1.95', report)
        self.assertEqual(sum(Decimal(row[3]) for row in daily), Decimal('1.95'))
        self.assertEqual(sum(Decimal(row[3]) for row in weekly), Decimal('1.95'))
        self.assertEqual({row[3] for row in daily}, {'0.03', '0.04'})
        self.assertNotEqual(Decimal('0.03') * len(daily), Decimal('1.95'))

    def test_parsed_bounds_select_the_same_values_for_report_and_both_plots(self):
        powers = [99, 1, 2, 0, 1, 99]
        begin = _parse_datetime('2026-01-15T00:15:00Z')
        end = _parse_datetime('2026-01-15T01:15:00Z')
        report = build_annual_report(self.readings(powers), self.single, self.two,
                                     date_from=begin, date_to=end)
        selected = [r for r in self.readings(powers) if begin <= r.timestamp < end]
        timestamps = [r.timestamp for r in selected]
        values = [r.power_kw for r in selected]
        self.assertIn(f'Період: [{begin.isoformat()}, {end.isoformat()})', report)
        self.assertIn('Інтервалів: 4\nЕнергія: 1.0 kWh\nВартість: 2.50 UAH', report)
        self.assertIn('Пік: 2 kW\nНерівномірність: 2.0', report)
        with TemporaryDirectory() as directory:
            line_path = Path(directory) / 'profile.png'
            stack_path = Path(directory) / 'devices.png'
            line = plot_daily_profile(timestamps, values, self.two, line_path)
            stack = plot_device_stack(timestamps, total_power_kw=values, output_path=stack_path)
            self.addCleanup(plt.close, line)
            self.addCleanup(plt.close, stack)
            self.assertEqual(list(line.axes[0].lines[0].get_ydata()), values)
            self.assertEqual(list(line.axes[1].lines[0].get_ydata()),
                             [self.two.rate_at(t) for t in timestamps])
            self.assertEqual(list(line.axes[0].lines[0].get_xdata()),
                             [t.strftime('%H:%M') for t in timestamps])
            layers = stack.axes[0].containers
            for i, power in enumerate(values):
                self.assertAlmostEqual(sum(layer[i].get_height() for layer in layers), power)
            labels = [text.get_text() for text in stack.axes[0].get_legend().get_texts()]
            self.assertTrue(all('Synthetic' in label for label in labels))
            self.assertGreater(line_path.stat().st_size, 0)
            self.assertGreater(stack_path.stat().st_size, 0)

    def test_local_days_and_dst_have_actual_coverage(self):
        cases = ((datetime(2026, 3, 29, tzinfo=KYIV), 23),
                 (datetime(2026, 10, 25, tzinfo=KYIV), 25))
        for local_start, hours in cases:
            with self.subTest(day=local_start.date()):
                start = local_start.astimezone(UTC)
                report = build_annual_report(self.readings([1] * hours, start, 60),
                                             self.single, self.two, step_minutes=60)
                daily, _ = self.tables(report)
                self.assertEqual(len(daily), 1)
                self.assertEqual(daily[0][0], str(local_start.date()))
                self.assertEqual(daily[0][1], str(hours))
                self.assertEqual(daily[0][-1], '100.00%')

    def test_partial_tail_and_gaps_are_observed_instead_of_filled(self):
        start = datetime(2026, 1, 15, 21, 30, tzinfo=UTC)
        readings = [MeterReading(start + timedelta(minutes=15 * i), 1)
                    for i in (0, 1, 3)]
        report = build_annual_report(iter(readings), self.single, self.two)
        daily, _ = self.tables(report)
        self.assertEqual([row[1] for row in daily], ['2', '1'])
        self.assertEqual([row[-1] for row in daily], ['2.08%', '1.04%'])
        self.assertIn('Інтервалів: 3\nЕнергія: 0.75 kWh', report)
        self.assertIn('2026-01-15T22:30:00+00:00)', report)

    def test_zero_power_keeps_real_period_and_undefined_ratios(self):
        report = build_annual_report(self.readings([0] * 4), self.single, self.two)
        self.assertIn('Інтервалів: 4\nЕнергія: 0.0 kWh\nВартість: 0.00 UAH', report)
        self.assertIn('Пік: 0 kW\nНерівномірність: не визначено', report)
        self.assertIn('Середня ціна: не визначено UAH/kWh', report)
        self.assertNotIn('немає даних', report)

    def test_empty_invalid_and_out_of_order_input_cannot_be_a_successful_year(self):
        with self.assertRaisesRegex(ValueError, 'No data'):
            build_annual_report(iter(()), self.single, self.two)
        cases = [list(self.readings([-1])), list(self.readings([float('nan')])),
                 list(self.readings([float('inf')])), list(self.readings([True])),
                 [MeterReading(self.start, 1)] * 2,
                 list(reversed(list(self.readings([1, 2]))))]
        for readings in cases:
            with self.subTest(readings=readings):
                with self.assertRaises(DataImporterError):
                    build_annual_report(iter(readings), self.single, self.two)
        with self.assertRaises(DataImporterError):
            build_annual_report(self.readings([1]), self.single, self.two, step_minutes=0)
        with self.assertRaisesRegex(ValueError, 'No data'):
            build_annual_report(self.readings([1]), self.single, self.two,
                                date_from=self.start + timedelta(days=1))

    def test_invalid_bounds_and_repeated_dst_hour(self):
        invalid = ((datetime(2026, 1, 15), None),
                   (self.start, self.start), (self.start + timedelta(days=1), self.start))
        for begin, end in invalid:
            with self.subTest(begin=begin, end=end):
                with self.assertRaisesRegex(ValueError, '[Pp]eriod'):
                    build_annual_report(self.readings([1]), self.single, self.two,
                                        date_from=begin, date_to=end)
        begin = datetime(2026, 10, 25, 3, 30, tzinfo=KYIV, fold=0)
        end = datetime(2026, 10, 25, 3, 15, tzinfo=KYIV, fold=1)
        start = begin.astimezone(UTC)
        report = build_annual_report(self.readings([1] * 4, start), self.single, self.two,
                                     date_from=begin, date_to=end)
        self.assertIn('Інтервалів: 3', report)

    def test_malformed_csv_and_tariff_errors_propagate(self):
        with self.assertRaises(DataImporterError):
            build_annual_report(iter_meter_csv(ROOT / 'tests/fixtures/malformed_meter.csv'),
                                self.single, self.two)
        with self.assertRaisesRegex(ValueError, 'unique'):
            build_annual_report(self.readings([1]), self.single, self.single)

    def test_invalid_stream_stops_at_the_first_bad_reading(self):
        consumed = []

        def source():
            for i in range(10000):
                consumed.append(i)
                yield MeterReading(self.start + timedelta(minutes=15 * i), -1)

        with self.assertRaises(DataImporterError):
            build_annual_report(source(), self.single, self.two)
        self.assertEqual(consumed, [0])

    def test_whole_missing_day_is_explicit_without_zero_fill(self):
        start = datetime(2026, 1, 1, tzinfo=KYIV)
        readings = (MeterReading(start + timedelta(hours=i), 1)
                    for i in range(72) if not 24 <= i < 48)
        report = build_annual_report(readings, self.single, self.two, step_minutes=60)
        daily, _ = self.tables(report)
        self.assertEqual([row[0] for row in daily], ['2026-01-01', '2026-01-03'])
        self.assertIn('Пропущено інтервалів у спостереженому періоді: 24', report)
        self.assertIn('Добові суми: округлення переноситься між днями.', report)
        self.assertIn('Інтервалів: 48\nЕнергія: 48.0 kWh', report)

    def test_allowing_gaps_keeps_strict_validation_and_does_not_collect_gaps(self):
        from energyhome.dataio import QualityReport, clean_readings

        rows = list(self.readings([1, 2, 3]))[::2]
        quality = QualityReport()
        self.assertEqual(list(clean_readings(rows, report=quality, allow_gaps=True)), rows)
        self.assertEqual(quality.gaps, [])
        with self.assertRaisesRegex(DataImporterError, 'Missing intervals'):
            list(clean_readings(rows))
        with self.assertRaises(DataImporterError):
            list(clean_readings(rows + [rows[-1]], allow_gaps=True))


class TestAnnualCli(unittest.TestCase):
    def setUp(self):
        self.folder = TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.root = Path(self.folder.name)
        (self.root / 'config').mkdir()
        (self.root / 'config/tariffs.json').write_bytes((ROOT / 'config/tariffs.json').read_bytes())
        self.path = self.root / 'meter.csv'
        self.write_readings([1, 2, 0, 1])

    def write_readings(self, powers, step=15):
        start = datetime(2026, 1, 15, tzinfo=UTC)
        rows = ['timestamp,power_kw']
        for i, power in enumerate(powers):
            stamp = (start + timedelta(minutes=step * i)).isoformat().replace('+00:00', 'Z')
            rows.append(f'{stamp},{power}')
        self.path.write_text('\n'.join(rows) + '\n', encoding='utf-8')

    def command(self, *args):
        return subprocess.run(
            [sys.executable, '-m', 'energyhome', 'report', '--input', str(self.path), *args],
            cwd=self.root, env=dict(os.environ, PYTHONPATH=str(ROOT / 'src')),
            capture_output=True, text=True, timeout=20,
        )

    def test_command_prints_real_streaming_report_and_real_tariffs(self):
        for tariff, cost in (('single', '2.50'), ('two_zone', '1.25'), ('three_zone', '1.00')):
            with self.subTest(tariff=tariff):
                result = self.command('--tariff', tariff)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn(f'Вартість: {cost} UAH', result.stdout)
                self.assertIn(f'Тарифи (UAH), базовий: {tariff}:', result.stdout)
                self.assertIn('Добові підсумки:', result.stdout)
                self.assertIn('Тижневі підсумки:', result.stdout)

    def test_actual_cli_bounds_match_filtered_report(self):
        self.write_readings([99, 1, 2, 0, 1, 99])
        result = self.command('--from', '2026-01-15T00:15:00Z',
                              '--to', '2026-01-15T01:15:00Z')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('Інтервалів: 4\nЕнергія: 1.0 kWh\nВартість: 2.50 UAH', result.stdout)
        self.assertIn('Пік: 2 kW', result.stdout)

    def test_empty_bad_period_and_bad_csv_fail_without_fake_report_or_traceback(self):
        periods = [('--from', '2030-01-01T00:00:00Z'),
                   ('--from', '2026-01-15T00:00:00Z', '--to', '2026-01-15T00:00:00Z'),
                   ('--from', '2026-01-16T00:00:00Z', '--to', '2026-01-15T00:00:00Z'),
                   ('--from', '2026-01-15')]
        for args in periods:
            with self.subTest(args=args):
                result = self.command(*args)
                self.assertEqual(result.returncode, 1)
                self.assertEqual(result.stdout, '')
                self.assertNotIn('Traceback', result.stderr)
        for powers in ([], [-1], [float('nan')]):
            self.write_readings(powers)
            result = self.command()
            self.assertEqual(result.returncode, 1)
            self.assertEqual(result.stdout, '')
            self.assertNotIn('Traceback', result.stderr)

    def test_uci_step_from_quality_metadata_is_used(self):
        self.write_readings([1, 2, 0, 1], step=1)
        self.path.with_suffix('.quality.json').write_text('{"step_minutes": 1}')
        result = self.command()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('Інтервалів: 4\nЕнергія: 0.0667 kWh\nВартість: 0.17 UAH', result.stdout)

    def test_corrupt_quality_metadata_is_a_readable_error(self):
        for content in ('[]', '{', '{"step_minutes": 0}'):
            self.path.with_suffix('.quality.json').write_text(content)
            result = self.command()
            self.assertEqual(result.returncode, 1)
            self.assertEqual(result.stdout, '')
            self.assertNotIn('Traceback', result.stderr)

    def test_actual_cli_preserves_gaps_and_reports_missing_intervals(self):
        self.write_readings([1] * 72, step=60)
        rows = self.path.read_text().splitlines()
        self.path.write_text('\n'.join(rows[:25] + rows[49:]) + '\n')
        self.path.with_suffix('.quality.json').write_text('{"step_minutes": 60}')
        result = self.command()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('Пропущено інтервалів у спостереженому періоді: 24', result.stdout)
        self.assertIn('Інтервалів: 48\nЕнергія: 48.0 kWh', result.stdout)


if __name__ == '__main__':
    unittest.main()
