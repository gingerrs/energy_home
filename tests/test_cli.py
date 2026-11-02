"""Process-level checks of configuration, filtering and report output."""

import json
import os
import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[1]


class TestCli(unittest.TestCase):
    def setUp(self):
        self.folder = TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.root = Path(self.folder.name)
        (self.root / 'config').mkdir()
        self.config = self.root / 'config/tariffs.json'
        self.config.write_bytes((ROOT / 'config/tariffs.json').read_bytes())
        self.path = self.root / 'meter.csv'
        self.path.write_bytes((ROOT / 'data/demo/meter_15min.csv').read_bytes())

    def command(self, *args):
        return subprocess.run(
            [sys.executable, '-m', 'energyhome', *args], cwd=self.root,
            env=dict(os.environ, PYTHONPATH=str(ROOT / 'src')),
            text=True, capture_output=True, timeout=20,
        )

    def report(self, *args):
        return self.command('report', '--input', str(self.path), *args)

    def assert_error(self, result, code=1):
        self.assertEqual(result.returncode, code, result.stdout + result.stderr)
        self.assertEqual(result.stdout, '')
        self.assertNotIn('Traceback', result.stderr)
        self.assertTrue(result.stderr)

    def test_shared_csv_matches_saved_control_report(self):
        result = self.report()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, '')
        self.assertEqual(result.stdout, (ROOT / 'docs/demo1/expected_small.txt').read_text())

    def test_three_tariffs_use_real_rates(self):
        for tariff, cost in [('single', '2.50'), ('two_zone', '1.25'), ('three_zone', '1.00')]:
            with self.subTest(tariff=tariff):
                result = self.report('--tariff', tariff)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn(f'Вартість: {cost} UAH', result.stdout)

    def test_period_includes_from_and_excludes_to(self):
        result = self.report('--from', '2026-01-15T00:15:00Z',
                             '--to', '2026-01-15T00:30:00Z')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('Інтервалів: 1\nЕнергія: 0.5 kWh\nВартість: 1.25 UAH', result.stdout)

    def test_zero_power_is_data_with_or_without_bounds(self):
        self.path.write_text('timestamp,power_kw\n2026-01-15T00:00:00Z,0\n')
        for bounds in [(), ('--from', '2026-01-15T00:00:00Z',
                            '--to', '2026-01-15T00:15:00Z')]:
            with self.subTest(bounds=bounds):
                result = self.report(*bounds)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn('Інтервалів: 1\nЕнергія: 0.0 kWh', result.stdout)
                self.assertIn('Нерівномірність: не визначено', result.stdout)

    def test_empty_csv_and_empty_selection_fail(self):
        self.assert_error(self.report('--from', '2030-01-01T00:00:00Z'))
        self.path.write_text('timestamp,power_kw\n')
        self.assert_error(self.report())

    def test_explicit_config_path_works_outside_project(self):
        custom = self.root / 'custom.json'
        custom.write_text(json.dumps({'single': {'rate': 1.0}}))
        self.config.unlink()
        result = self.report('--tariffs', str(custom))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('Вартість: 1.00 UAH', result.stdout)

    def test_legacy_config_requires_rate_and_handles_wrong_types(self):
        for entry in ({}, None, [], {'rate': None}, {'rate': True}, {'rate': '2.5'},
                      {'rate': -1}, {'rate': float('inf')}):
            with self.subTest(entry=entry):
                self.config.write_text(json.dumps({'single': entry}))
                self.assert_error(self.report())

    def test_corrupt_or_wrong_shape_config_fails(self):
        for value in ('{', '[]', '{}', '{"tariffs": []}'):
            with self.subTest(value=value):
                self.config.write_text(value)
                self.assert_error(self.report())

    def test_naive_invalid_equal_and_reversed_bounds_fail(self):
        bounds = [('--from', '2026-01-15T00:00:00'), ('--from', 'invalid'),
                  ('--from', '2026-01-15'),
                  ('--from', '2026-01-15T00:00:00Z', '--to', '2026-01-15T00:00:00Z'),
                  ('--from', '2026-01-16T00:00:00Z', '--to', '2026-01-15T00:00:00Z')]
        for args in bounds:
            with self.subTest(args=args):
                self.assert_error(self.report(*args))

    def test_offset_bounds_select_same_instants_as_utc(self):
        utc = self.report('--from', '2026-01-15T00:15:00Z', '--to', '2026-01-15T00:30:00Z')
        local = self.report('--from', '2026-01-15T02:15:00+02:00',
                            '--to', '2026-01-15T02:30:00+02:00')
        self.assertEqual(local.returncode, 0, local.stderr)
        self.assertEqual(local.stdout, utc.stdout)

    def test_missing_csv_config_and_bad_tariff_fail(self):
        self.assert_error(self.report('--tariffs', 'missing.json'))
        self.assert_error(self.report('--tariff', 'unknown'), code=2)
        self.path.unlink()
        result = self.report()
        self.assert_error(result)
        self.assertIn('Input CSV not found', result.stderr)

    def test_missing_input_is_parser_error(self):
        self.assert_error(self.command('report'), code=2)

    def test_help_documents_config_and_timezone(self):
        result = self.command('report', '--help')
        self.assertEqual(result.returncode, 0)
        self.assertIn('--tariffs', result.stdout)
        self.assertIn('UTC offset', result.stdout)

    def test_error_is_logged_once_and_debug_includes_timing(self):
        result = self.command('--log-level', 'DEBUG', 'report', '--input', str(self.path))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('_render_report took', result.stderr)
        self.path.unlink()
        result = self.report()
        self.assertEqual(result.stderr.count('ERROR'), 1)

    def test_text_analytics_import_does_not_load_plotting_packages(self):
        result = subprocess.run(
            [sys.executable, '-c', "import sys; import energyhome.analytics; "
             "assert 'matplotlib' not in sys.modules; assert 'numpy' not in sys.modules"],
            env=dict(os.environ, PYTHONPATH=str(ROOT / 'src')),
            text=True, capture_output=True, timeout=10,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
