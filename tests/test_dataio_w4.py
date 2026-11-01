import json
import subprocess
import sys
import unittest
from datetime import UTC, datetime, timedelta
from fractions import Fraction
from pathlib import Path
from tempfile import TemporaryDirectory

from energyhome.dataio import (
    UCI_HOUSEHOLD, DataImporterError, QualityReport, SourceConfig, parse_timestamp, read_chunks,
)
from scripts.prepare_data import synthetic_year, uci_year, write_csv


ROOT = Path(__file__).resolve().parents[1]
SLICE = ROOT / 'tests/fixtures/w4_uci_slice.csv'


class TestDataIOW4(unittest.TestCase):
    def setUp(self):
        self.folder = TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.root = Path(self.folder.name)

    def source(self, rows):
        path = self.root / 'raw.txt'
        text = 'Date;Time;Global_active_power\n' + '\n'.join(rows) + '\n'
        path.write_text(text, encoding='utf-8')
        return path

    def run_script(self, *args):
        return subprocess.run(
            [sys.executable, str(ROOT / 'scripts/prepare_data.py'), *args],
            cwd=ROOT, capture_output=True, text=True, timeout=30,
        )

    def test_source_calendar_boundaries_do_not_use_utc_year_or_other_year_quality(self):
        path = self.source([
            '31/12/2007;23:59:00;?', '1/1/2008;00:00:00;1',
            '31/12/2008;23:59:00;2', '1/1/2009;00:00:00;?',
        ])
        for size in (1, 2, 17, 1000):
            with self.subTest(size=size):
                report = QualityReport()
                readings = list(uci_year(path, 2008, report, size))
                self.assertEqual([r.timestamp for r in readings], [
                    datetime(2007, 12, 31, 23, tzinfo=UTC),
                    datetime(2008, 12, 31, 22, 59, tzinfo=UTC),
                ])
                self.assertEqual(report.rejects, [])
                self.assertEqual(report.line, 4)
                self.assertEqual(len(report.gaps), 1)

    def test_future_year_is_not_parsed_ahead_even_in_a_large_chunk(self):
        path = self.source([
            '1/1/2008;00:00:00;1', '1/1/2009;00:00:00;1',
            '1/1/2009;00:01:00;"unclosed',
        ])
        for size in (1, 17, 1000):
            self.assertEqual(len(list(uci_year(path, 2008, QualityReport(), size))), 1)
        with self.assertRaises(DataImporterError):
            list(read_chunks(path, UCI_HOUSEHOLD))

    def test_year_selection_keeps_physical_lines_and_incomplete_final_chunk(self):
        path = self.source([
            '31/12/2007;23:59:00;1',
            *(f'1/1/2008;00:{minute:02}:00;1' for minute in range(12)),
            '1/1/2009;00:00:00;1',
        ])
        chunks = list(read_chunks(path, UCI_HOUSEHOLD, 5, year=2008))
        self.assertEqual([len(chunk) for chunk in chunks], [5, 5, 2])
        self.assertEqual([line for chunk in chunks for line, _ in chunk], list(range(3, 15)))

    def test_chunk_size_does_not_change_values_rejects_gaps_or_line_numbers(self):
        path = self.source([
            '31/12/2007;23:59:00;?', '1/1/2008;00:00:00;1',
            '1/1/2008;00:01:00;?', '1/1/2008;00:02:00;3',
            '1/1/2008;00:02:00;9', '1/1/2008;00:03:00;4',
            '1/1/2009;00:00:00;?',
        ])
        expected = None
        for size in (1, 2, 3, 5, 17, 1000):
            report = QualityReport()
            readings = list(uci_year(path, 2008, report, size))
            result = (readings, report)
            expected = result if expected is None else expected
            self.assertEqual(result, expected)
            self.assertEqual([r.power_kw for r in readings], [1, 3, 4])
            self.assertEqual([r.line for r in report.rejects], [4, 6])
            self.assertEqual(len(report.gaps), 1)

    def test_nonexistent_spring_time_cannot_replace_the_valid_following_hour(self):
        path = self.source([
            '30/3/2008;01:59:00;1', '30/3/2008;02:00:00;100',
            '30/3/2008;02:59:00;100', '30/3/2008;03:00:00;2',
        ])
        report = QualityReport()
        readings = list(uci_year(path, 2008, report, 3))
        self.assertEqual([r.power_kw for r in readings], [1, 2])
        self.assertEqual(readings[1].timestamp - readings[0].timestamp, timedelta(minutes=1))
        self.assertEqual([r.reason for r in report.rejects],
                         ['Nonexistent local timestamp'] * 2)
        self.assertEqual(report.gaps, [])

    def test_fall_missing_fold_is_reported_as_a_gap_without_invented_rows(self):
        path = self.source(['26/10/2008;02:59:00;1', '26/10/2008;03:00:00;2'])
        report = QualityReport()
        readings = list(uci_year(path, 2008, report))
        self.assertEqual(len(readings), 2)
        self.assertEqual(report.rejects, [])
        self.assertEqual(len(report.gaps), 1)
        start, end = report.gaps[0].value
        self.assertEqual(end - start, timedelta(hours=1))

    def test_explicit_offset_is_an_instant_and_not_an_invalid_naive_clock_label(self):
        stamp = parse_timestamp('2008-03-30T02:30:00+01:00', '%Y-%m-%dT%H:%M:%S%z',
                                'Europe/Paris')
        self.assertEqual(stamp, datetime(2008, 3, 30, 1, 30, tzinfo=UTC))

    def test_rejected_iso_row_uses_the_source_timezone_to_choose_its_year(self):
        path = self.root / 'offset.csv'
        path.write_text('timestamp,power_kw\n2007-12-31T23:00:00Z,1\n'
                        '2007-12-31T23:01:00Z,bad\n2008-12-31T23:00:00Z,1\n',
                        encoding='utf-8')
        cfg = SourceConfig(timezone='Europe/Paris', step_minutes=1)
        chunks = list(read_chunks(path, cfg, 1000, year=2008))
        self.assertEqual([line for chunk in chunks for line, _ in chunk], [2, 3])

    def test_unknown_source_year_is_a_readable_error_instead_of_silent_data_loss(self):
        path = self.source(['bad_date;00:00:00;1'])
        message = 'Рядок 2: cannot determine source year'
        with self.assertRaisesRegex(DataImporterError, message):
            list(uci_year(path, 2008, QualityReport()))

    def test_invalid_year_and_chunk_size_are_rejected_before_reading(self):
        for year in (0, 9999, -1, True, 2008.0):
            with self.subTest(year=year):
                with self.assertRaises(DataImporterError):
                    next(read_chunks(SLICE, UCI_HOUSEHOLD, year=year))
        for size in (0, -1, True, 1.5):
            with self.subTest(size=size):
                with self.assertRaises(DataImporterError):
                    next(uci_year(SLICE, 2008, QualityReport(), size))

    def test_real_uci_slice_has_independent_exact_control_total(self):
        for size in (1, 5, 17, 1000):
            report = QualityReport()
            readings = list(uci_year(SLICE, 2008, report, size))
            energy = sum(Fraction(str(r.power_kw)) for r in readings) / 60
            self.assertEqual(len(readings), 12)
            self.assertEqual(energy, Fraction(9461, 30000))
            self.assertEqual(readings[0].timestamp, datetime(2007, 12, 31, 23, tzinfo=UTC))
            self.assertEqual(readings[-1].timestamp, datetime(2007, 12, 31, 23, 11, tzinfo=UTC))
            self.assertEqual((report.rejects, report.gaps), ([], []))

    def test_synthetic_leap_year_is_lazy_and_contains_february_29(self):
        readings = synthetic_year(2008, 42)
        self.assertIs(iter(readings), readings)
        count, leap_rows, last = 0, 0, None
        for reading in readings:
            count += 1
            leap_rows += (reading.timestamp.month, reading.timestamp.day) == (2, 29)
            last = reading.timestamp
        self.assertEqual(count, 35136)
        self.assertEqual(leap_rows, 96)
        self.assertEqual(last, datetime(2008, 12, 31, 23, 45, tzinfo=UTC))

    def test_writer_keeps_legacy_count_and_collects_exact_observed_bounds(self):
        summary = {}
        path = self.root / 'slice.csv'
        rows = write_csv(uci_year(SLICE, 2008, QualityReport()), path, summary)
        self.assertEqual(rows, 12)
        self.assertEqual(summary, {'first_timestamp': '2007-12-31T23:00:00Z',
                                   'last_timestamp': '2007-12-31T23:11:00Z',
                                   'power_sum_kw': '18.922'})
        self.assertEqual(write_csv(iter(()), self.root / 'empty.csv'), 0)

    def test_script_help_and_missing_input_are_real_entrypoint_checks(self):
        result = self.run_script('--help')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('--chunk-size', result.stdout)
        result = self.run_script('uci', '--year', '2008')
        self.assertEqual(result.returncode, 2)
        self.assertIn('--input', result.stderr)
        self.assertNotIn('Traceback', result.stderr)

    def test_script_creates_year_quality_json_and_rejects_invalid_arguments(self):
        out = self.root / 'prepared.csv'
        result = self.run_script('uci', '--year', '2008', '--input', str(SLICE),
                                 '--out', str(out), '--chunk-size', '5')
        self.assertEqual(result.returncode, 0, result.stderr)
        summary = json.loads(out.with_suffix('.quality.json').read_text(encoding='utf-8'))
        self.assertEqual(summary['rows'], 12)
        self.assertEqual(summary['source_rows'], 12)
        self.assertEqual(summary['expected_intervals'], 527040)
        self.assertEqual(summary['missing_intervals'], 527028)
        self.assertEqual(summary['period_start'], '2007-12-31T23:00:00+00:00')
        self.assertEqual(summary['period_end_exclusive'], '2008-12-31T23:00:00+00:00')
        for args in (('--year', '0'), ('--year', '9999'),
                     ('--year', '2008', '--chunk-size', '0')):
            self.assertEqual(self.run_script('synthetic', *args).returncode, 2)

    def test_script_cannot_overwrite_its_input_or_quality_report_input(self):
        path = self.source(['1/1/2008;00:00:00;1'])
        before = path.read_bytes()
        result = self.run_script('uci', '--year', '2008', '--input', str(path), '--out', str(path))
        self.assertEqual(result.returncode, 2)
        self.assertEqual(path.read_bytes(), before)
        quality_input = self.root / 'other.quality.json'
        quality_input.write_bytes(before)
        result = self.run_script('uci', '--year', '2008', '--input', str(quality_input),
                                 '--out', str(self.root / 'other.csv'))
        self.assertEqual(result.returncode, 2)
        self.assertEqual(quality_input.read_bytes(), before)

    def test_missing_file_and_empty_selected_year_fail_without_traceback(self):
        for path in (self.root / 'missing.txt', self.source(['1/1/2007;00:00:00;1'])):
            result = self.run_script('uci', '--year', '2008', '--input', str(path),
                                     '--out', str(self.root / 'out.csv'))
            self.assertEqual(result.returncode, 1)
            self.assertNotIn('Traceback', result.stderr)

    def test_offline_backup_is_byte_reproducible_and_explicitly_synthetic(self):
        paths = [self.root / 'backup1.csv', self.root / 'backup2.csv']
        for path in paths:
            result = self.run_script('synthetic', '--year', '2008', '--seed', '42',
                                     '--out', str(path))
            self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(paths[0].read_bytes(), paths[1].read_bytes())
        report = json.loads(paths[0].with_suffix('.quality.json').read_text(encoding='utf-8'))
        self.assertEqual(report['source'], 'synthetic')
        self.assertEqual(report['seed'], 42)
        self.assertEqual(report['rows'], 35136)
        self.assertEqual(report['expected_intervals'], 35136)
        observed = report['rejects'], report['gaps'], report['missing_intervals']
        self.assertEqual(observed, (0, 0, 0))
