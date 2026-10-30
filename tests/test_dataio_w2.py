import csv
import random
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from energyhome.dataio import (
    DataImporterError,
    QualityReport,
    RejectRecord,
    clean_readings,
    generate_demo_consumption,
    iter_meter_csv,
    load_demo_profile,
    validate_readings,
)
from energyhome.domain import LoadProfile, MeterReading


class TestDataIOW2(unittest.TestCase):
    def setUp(self):
        self.start = datetime(2026, 1, 15, tzinfo=timezone.utc)
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.directory = Path(directory.name)

    def reading(self, minute, power=1.0):
        return MeterReading(self.start + timedelta(minutes=minute), power)

    def csv_file(self, content):
        path = self.directory / 'meter.csv'
        path.write_text(content, encoding='utf-8')
        return path

    def test_reject_preserves_first_duplicate_and_reports_gap_without_fill(self):
        readings = [self.reading(0), self.reading(15, 2.0), self.reading(15, 99.0),
                    self.reading(30, -1.0), self.reading(45, 3.0)]
        report = QualityReport()

        cleaned = list(clean_readings(readings, mode='reject', report=report))

        self.assertEqual(cleaned, [readings[0], readings[1], readings[4]])
        self.assertEqual([record.line for record in report.rejects], [4, 5])
        self.assertEqual(report.rejects[0].value, readings[2])
        self.assertIn('Duplicate', report.rejects[0].reason)
        self.assertEqual(report.gaps, [RejectRecord(
            6, 'Missing intervals', (self.start + timedelta(minutes=30),
                                     self.start + timedelta(minutes=45)))])
        self.assertEqual(sum(r.power_kw * 0.25 for r in cleaned), 1.5)

    def test_strict_records_error_and_stops_at_first_bad_reading(self):
        report = QualityReport()
        source = clean_readings([self.reading(0), self.reading(0), self.reading(15)],
                                report=report)
        self.assertEqual(next(source), self.reading(0))
        with self.assertRaisesRegex(DataImporterError, 'Рядок 3'):
            next(source)
        self.assertEqual(len(report.rejects), 1)
        self.assertEqual(report.last_timestamp, self.start)

    def test_strict_records_gap_without_replacing_it_with_zero(self):
        report = QualityReport()
        source = clean_readings([self.reading(0), self.reading(30)], report=report)
        self.assertEqual(next(source), self.reading(0))
        with self.assertRaisesRegex(DataImporterError, 'Missing intervals'):
            next(source)
        self.assertEqual(len(report.gaps), 1)
        self.assertEqual(report.rejects, [])
        self.assertEqual(report.last_timestamp, self.start)

    def test_all_chunk_boundaries_have_identical_decisions_and_line_numbers(self):
        readings = [self.reading(0), self.reading(15), self.reading(15),
                    self.reading(30, float('nan')), self.reading(45),
                    self.reading(40), self.reading(60)]
        whole_report = QualityReport()
        whole = list(clean_readings(readings, mode='reject', report=whole_report))
        for split in range(len(readings) + 1):
            with self.subTest(split=split):
                report = QualityReport()
                parts = list(clean_readings(readings[:split], mode='reject', report=report))
                parts += list(clean_readings(readings[split:], mode='reject', report=report))
                self.assertEqual(parts, whole)
                self.assertEqual(report, whole_report)

    def test_duplicate_at_start_of_second_strict_chunk_is_rejected(self):
        report = QualityReport()
        list(clean_readings([self.reading(0), self.reading(15)], report=report))
        with self.assertRaisesRegex(DataImporterError, 'Рядок 4'):
            list(clean_readings([self.reading(15)], report=report))

    def test_strict_stops_at_same_record_for_every_chunk_boundary(self):
        def consume(chunks):
            report = QualityReport()
            accepted = []
            try:
                for chunk in chunks:
                    accepted.extend(clean_readings(chunk, report=report))
            except DataImporterError as error:
                return accepted, report, str(error)
            self.fail('Strict mode must reject the invalid series')

        for readings in ([self.reading(0), self.reading(15), self.reading(15)],
                         [self.reading(0), self.reading(30)],
                         [self.reading(0), self.reading(15, -1), self.reading(30)]):
            whole = consume([readings])
            for split in range(len(readings) + 1):
                with self.subTest(readings=readings, split=split):
                    self.assertEqual(consume([readings[:split], readings[split:]]), whole)

    def test_validation_returns_quality_report_for_entire_series(self):
        report = validate_readings([self.reading(0), self.reading(15, -1),
                                    self.reading(30), self.reading(30)])
        self.assertEqual(len(report.rejects), 2)
        self.assertEqual(len(report.gaps), 1)
        self.assertEqual(report.last_timestamp, self.start + timedelta(minutes=30))

    def test_invalid_numbers_and_naive_dates_are_rejected(self):
        bad = [self.reading(0, power) for power in
               (-1.0, float('nan'), float('inf'), float('-inf'), True, '1.0')]
        bad.append(MeterReading(self.start.replace(tzinfo=None), 1.0))
        report = QualityReport()
        self.assertEqual(list(clean_readings(bad, mode='reject', report=report)), [])
        self.assertEqual(len(report.rejects), len(bad))
        self.assertIsNone(report.last_timestamp)

    def test_timezone_normalization_detects_same_instant_as_duplicate(self):
        local = self.start.astimezone(timezone(timedelta(hours=2)))
        report = QualityReport()
        cleaned = list(clean_readings([MeterReading(local, 1), self.reading(0)],
                                     mode='reject', report=report))
        self.assertEqual(cleaned, [self.reading(0)])
        self.assertIs(cleaned[0].timestamp.tzinfo, timezone.utc)
        self.assertEqual(len(report.rejects), 1)

    def test_out_of_order_and_off_grid_rows_do_not_move_validation_state(self):
        readings = [self.reading(0), self.reading(5), self.reading(-15), self.reading(15)]
        report = QualityReport()
        self.assertEqual(list(clean_readings(readings, mode='reject', report=report)),
                         [readings[0], readings[3]])
        self.assertEqual(len(report.rejects), 2)
        self.assertEqual(report.gaps, [])

    def test_csv_reject_logs_raw_malformed_row_and_physical_line(self):
        path = self.csv_file('timestamp,power_kw\n\n2026-01-15T00:00:00Z,1\n'
                             '2026-01-15T00:15:00Z,bad\n'
                             '2026-01-15T00:30:00Z,2\n')
        report = QualityReport()
        readings = list(iter_meter_csv(path, mode='reject', report=report))
        self.assertEqual(readings, [self.reading(0), self.reading(30, 2)])
        self.assertEqual(report.rejects[0].line, 4)
        self.assertEqual(report.rejects[0].value['power_kw'], 'bad')
        self.assertTrue(report.rejects[0].reason)
        self.assertEqual(report.gaps[0].line, 5)
        self.assertEqual(report.gaps[0].value, (self.reading(15).timestamp,
                                              self.reading(30).timestamp))

    def test_csv_reject_handles_bad_date_missing_extra_and_nonfinite_values(self):
        for row in ('not-a-date,1', '2026-01-15T00:00:00Z',
                    '2026-01-15T00:00:00Z,1,extra', '2026-01-15T00:00:00Z,nan'):
            with self.subTest(row=row):
                path = self.csv_file('timestamp,power_kw\n' + row + '\n')
                report = QualityReport()
                self.assertEqual(list(iter_meter_csv(path, mode='reject', report=report)), [])
                self.assertEqual(len(report.rejects), 1)
                self.assertEqual(report.rejects[0].line, 2)
                with self.assertRaisesRegex(DataImporterError, 'Рядок 2'):
                    list(iter_meter_csv(path))

    def test_invalid_header_is_fatal_in_both_modes(self):
        path = self.csv_file('date,value\n2026-01-15T00:00:00Z,1\n')
        for mode in ('strict', 'reject'):
            with self.subTest(mode=mode):
                with self.assertRaisesRegex(DataImporterError, 'Рядок 1'):
                    list(iter_meter_csv(path, mode=mode, report=QualityReport()))

    def test_duplicate_header_cannot_silently_replace_a_power_value(self):
        path = self.csv_file('timestamp,power_kw,power_kw\n'
                             '2026-01-15T00:00:00Z,1,99\n')
        for mode in ('strict', 'reject'):
            with self.subTest(mode=mode):
                with self.assertRaisesRegex(DataImporterError, 'повторювані назви колонок'):
                    list(iter_meter_csv(path, mode=mode, report=QualityReport()))

    def test_unclosed_csv_quote_is_fatal_instead_of_becoming_valid_power(self):
        path = self.csv_file('timestamp,power_kw\n2026-01-15T00:00:00Z,"1\n')
        for mode in ('strict', 'reject'):
            with self.subTest(mode=mode):
                with self.assertRaisesRegex(DataImporterError, 'Рядок 2: Invalid CSV'):
                    list(iter_meter_csv(path, mode=mode, report=QualityReport()))

    def test_reject_requires_an_accessible_quality_report(self):
        with self.assertRaisesRegex(DataImporterError, 'requires a QualityReport'):
            list(clean_readings([self.reading(0), self.reading(0)], mode='reject'))
        path = self.csv_file('timestamp,power_kw\nnot-a-date,1\n')
        with self.assertRaisesRegex(DataImporterError, 'requires a QualityReport'):
            list(iter_meter_csv(path, mode='reject'))

    def test_invalid_options_are_rejected_even_for_empty_input(self):
        for step in (0, -15, 15.0, True):
            with self.subTest(step=step):
                with self.assertRaises(DataImporterError):
                    list(clean_readings([], step_minutes=step))
        with self.assertRaises(DataImporterError):
            list(clean_readings([], mode='fill'))
        self.assertEqual(validate_readings([]), QualityReport())

    def test_cleaning_is_lazy_and_does_not_change_source_readings(self):
        visited = []
        original = [self.reading(0), self.reading(15)]

        def source():
            for reading in original:
                visited.append(reading)
                yield reading

        cleaned = clean_readings(source())
        self.assertEqual(visited, [])
        self.assertEqual(next(cleaned), original[0])
        self.assertEqual(visited, original[:1])
        self.assertEqual(list(cleaned), original[1:])
        self.assertEqual(original, [self.reading(0), self.reading(15)])

    def test_seed_reproducibility_labels_and_known_signal_levels(self):
        profile, labels = generate_demo_consumption(42)
        repeated, repeated_labels = generate_demo_consumption(42)
        different, _ = generate_demo_consumption(43)
        self.assertIsInstance(profile, LoadProfile)
        self.assertEqual(list(profile), list(repeated))
        self.assertEqual(labels, repeated_labels)
        self.assertNotEqual(profile.power_kw, different.power_kw)
        self.assertEqual(labels, ((self.start + timedelta(hours=1), 'heater', 'on'),
                                  (self.start + timedelta(hours=2), 'heater', 'off')))
        self.assertEqual(len(profile), 16)
        for i, power in enumerate(profile.power_kw):
            expected = 1.75 if 4 <= i < 8 else 0.25
            self.assertLessEqual(abs(power - expected), 0.020001)

    def test_generator_does_not_change_global_random_state(self):
        state = random.getstate()
        generate_demo_consumption(42)
        self.assertEqual(random.getstate(), state)
        for seed in (True, 1.5, '42'):
            with self.subTest(seed=seed):
                with self.assertRaises(DataImporterError):
                    generate_demo_consumption(seed)

    def test_saved_fixtures_match_generated_profile_and_ground_truth(self):
        profile, labels = generate_demo_consumption(42)
        fixture = Path(__file__).parent / 'fixtures'
        loaded = load_demo_profile(fixture / 'demo_consumption.csv')
        self.assertEqual(list(loaded), list(profile))
        with (fixture / 'events.csv').open(encoding='utf-8', newline='') as file:
            rows = list(csv.DictReader(file))
        saved_labels = tuple((datetime.fromisoformat(row['timestamp'].replace('Z', '+00:00')),
                              row['device_id'], row['event_type']) for row in rows)
        self.assertEqual(saved_labels, labels)
