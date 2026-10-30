import csv
import unittest
from dataclasses import FrozenInstanceError
from datetime import datetime, timedelta, timezone
from pathlib import Path

from energyhome.domain import (
    DeviceError,
    EnergyError,
    LoadProfile,
    MeterDataError,
    MeterReading,
    ScheduleConflictError,
    SingleZoneTariff,
    Tariff,
    TariffError,
)


class TestDomain(unittest.TestCase):
    def setUp(self):
        path = Path(__file__).parent / 'fixtures' / 'w1_profile.csv'
        self.readings = []
        with path.open(encoding='utf-8', newline='') as file:
            for row in csv.DictReader(file):
                timestamp = datetime.fromisoformat(row['timestamp'].replace('Z', '+00:00'))
                self.readings.append(MeterReading(timestamp, float(row['power_kw'])))

        self.timestamps = [reading.timestamp for reading in self.readings]
        self.power_kw = [reading.power_kw for reading in self.readings]
        self.profile = LoadProfile(self.timestamps, self.power_kw, 15)
        self.tariff = SingleZoneTariff('single', 'Test tariff', 'Europe/Kyiv', 4.0)

    def test_error_hierarchy(self):
        for error in (DeviceError, TariffError, ScheduleConflictError, MeterDataError):
            with self.subTest(error=error):
                self.assertIsInstance(error('Invalid data'), EnergyError)

    def test_meter_reading_is_frozen(self):
        with self.assertRaises(FrozenInstanceError):
            self.readings[0].power_kw = 2.0

    def test_meter_reading_preserves_values_before_cleaning(self):
        reading = MeterReading(self.timestamps[0], -1.0)
        self.assertEqual(reading.power_kw, -1.0)
        with self.assertRaises(MeterDataError):
            LoadProfile([reading.timestamp], [reading.power_kw], 15)

    def test_shared_fixture_energy(self):
        self.assertEqual(self.profile.energy_kwh(), 1.0)

    def test_profile_methods(self):
        self.assertEqual(len(self.profile), 2)
        self.assertEqual(list(self.profile), list(zip(self.timestamps, self.power_kw)))
        self.assertEqual(self.profile.peak_kw(), 3.0)
        self.assertEqual(self.profile.start_time(), self.timestamps[0])
        self.assertEqual(self.profile.end_time(), self.timestamps[-1] + timedelta(minutes=15))
        self.assertEqual(repr(self.profile), 'LoadProfile(points=2, resolution_minutes=15)')

    def test_profile_copies_input_lists(self):
        self.timestamps[0] += timedelta(days=1)
        self.power_kw[0] = 100.0
        self.assertEqual(self.profile.start_time(), self.readings[0].timestamp)
        self.assertEqual(self.profile.energy_kwh(), 1.0)

    def test_profile_normalizes_timestamps_to_utc(self):
        local_timezone = timezone(timedelta(hours=2))
        timestamps = [timestamp.astimezone(local_timezone) for timestamp in self.timestamps]
        profile = LoadProfile(timestamps, self.power_kw, 15)
        self.assertEqual(profile.timestamps, self.timestamps)
        self.assertTrue(all(timestamp.tzinfo is timezone.utc for timestamp in profile.timestamps))

    def test_invalid_resolution(self):
        for resolution in (0, -15, 15.0, True):
            with self.subTest(resolution=resolution):
                with self.assertRaises(MeterDataError):
                    LoadProfile(self.timestamps, self.power_kw, resolution)

    def test_empty_or_mismatched_profile(self):
        for timestamps, powers in (([], []), (self.timestamps, [1.0]), ([], [1.0])):
            with self.subTest(timestamps=timestamps, powers=powers):
                with self.assertRaises(MeterDataError):
                    LoadProfile(timestamps, powers, 15)

    def test_invalid_power(self):
        for power in (-1.0, float('nan'), float('inf'), float('-inf'), '1.0', True):
            with self.subTest(power=power):
                with self.assertRaises(MeterDataError):
                    LoadProfile([self.timestamps[0]], [power], 15)

    def test_invalid_time_grid(self):
        start = self.timestamps[0]
        for timestamps in (
            [start, start],
            [start, start - timedelta(minutes=15)],
            [start, start + timedelta(minutes=30)],
            [start.replace(tzinfo=None)],
            ['2026-01-15T00:00:00Z'],
        ):
            with self.subTest(timestamps=timestamps):
                with self.assertRaises(MeterDataError):
                    LoadProfile(timestamps, [1.0] * len(timestamps), 15)

    def test_zero_power_profile(self):
        profile = LoadProfile(self.timestamps, [0.0, 0.0], 15)
        self.assertEqual(profile.energy_kwh(), 0.0)
        self.assertEqual(profile.peak_kw(), 0.0)
        self.assertEqual(self.tariff.cost(profile), 0.0)

    def test_single_zone_tariff(self):
        self.assertIsInstance(self.tariff, Tariff)
        self.assertEqual(self.tariff.tariff_id, 'single')
        self.assertEqual(self.tariff.name, 'Test tariff')
        self.assertEqual(self.tariff.timezone_name, 'Europe/Kyiv')
        for timestamp in self.timestamps:
            self.assertEqual(self.tariff.rate_at(timestamp), 4.0)
            self.assertEqual(self.tariff.zone_at(timestamp), 'single')
        self.assertEqual(self.tariff.cost(self.profile), 4.0)

    def test_zero_rate(self):
        tariff = SingleZoneTariff('free', 'Zero rate', 'UTC', 0.0)
        self.assertEqual(tariff.cost(self.profile), 0.0)

    def test_invalid_tariff_parameters(self):
        for rate in (-1.0, float('nan'), float('inf'), '4.0', True):
            with self.subTest(rate=rate):
                with self.assertRaises(TariffError):
                    SingleZoneTariff('single', 'Test tariff', 'UTC', rate)
        for tariff_id, name, timezone_name in (
            ('', 'Test tariff', 'UTC'),
            ('   ', 'Test tariff', 'UTC'),
            ('single', None, 'UTC'),
            ('single', 'Test tariff', 'Unknown/Zone'),
            ('single', 'Test tariff', ''),
        ):
            with self.subTest(tariff_id=tariff_id, name=name, timezone=timezone_name):
                with self.assertRaises(TariffError):
                    SingleZoneTariff(tariff_id, name, timezone_name, 4.0)

    def test_tariff_rejects_invalid_timestamp(self):
        for timestamp in (self.timestamps[0].replace(tzinfo=None), '2026-01-15'):
            with self.subTest(timestamp=timestamp):
                with self.assertRaises(TariffError):
                    self.tariff.rate_at(timestamp)
                with self.assertRaises(TariffError):
                    self.tariff.zone_at(timestamp)

    def test_tariff_is_abstract(self):
        with self.assertRaises(TypeError):
            Tariff('base', 'Base tariff', 'UTC')
