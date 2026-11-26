import csv
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from dataclasses import FrozenInstanceError
import json

from energyhome.domain import (
    CyclicDevice,
    FixedDevice,
    Device,
    House,
    DeviceError,
    EnergyError,
    LoadProfile,
    MeterDataError,
    MeterReading,
    ScheduleConflictError,
    SingleZoneTariff,
    Tariff,
    TariffError,
    Schedule,
    ScheduledRun,
    SavingsResult,
    OptimizationRequest,
    ValidationError,
    tariffs_from_dict,
    tariffs_to_dict,
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
        self.assertEqual(len(self.profile), 4)
        self.assertEqual(list(self.profile), list(zip(self.timestamps, self.power_kw)))
        self.assertEqual(self.profile.peak_kw(), 2.0)
        self.assertEqual(self.profile.start_time(), self.timestamps[0])
        self.assertEqual(self.profile.end_time(), self.timestamps[-1] + timedelta(minutes=15))
        self.assertEqual(repr(self.profile), 'LoadProfile(points=4, resolution_minutes=15)')

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
        self.assertTrue(all(timestamp.tzinfo == timezone.utc for timestamp in profile.timestamps))

    def test_invalid_resolution(self):
        for resolution in (0, -15, 15.0, True):
            with self.subTest(resolution=resolution):
                with self.assertRaises(MeterDataError):
                    LoadProfile(self.timestamps, self.power_kw, resolution)

    def test_empty_or_mismatched_profile(self):
        for timestamps, powers in ((self.timestamps, [1.0]), ([], [1.0])):
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
        profile = LoadProfile(self.timestamps, [0.0] * len(self.timestamps), 15)
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
            (' ', 'Test tariff', 'UTC'),
            ('single', None, 'UTC'),
            ('single', 'Test tariff', 'Unknown/Zone'),
            ('single', 'Test tariff', ''),
        ):
            with self.subTest(tariff_id=tariff_id, name=name, timezone=timezone):
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

    def test_schedule_creation_and_iteration(self):
        start = datetime(2026, 11, 1, 10, 0, tzinfo=timezone.utc)
        run = ScheduledRun(device_id='heater-1', start=start, duration=30)
        schedule = Schedule([run], device_ids=['heater-1'])

        self.assertEqual(len(schedule), 1)
        runs = list(schedule)
        self.assertEqual(runs[0].device_id, 'heater-1')
        self.assertEqual(runs[0].duration, 30)

    def test_scheduled_run_validation_error(self):
        start = datetime(2026, 11, 1, 10, 0)
        with self.assertRaises(ValidationError):
            ScheduledRun(device_id='heater-1', start=start, duration=30)

    def test_savings_and_optimization_contracts(self):
        savings = SavingsResult(
            baseline_cost_uah=100.0,
            optimized_cost_uah=80.0,
            savings_uah=20.0,
            savings_percent=20.0
        )
        self.assertEqual(savings.savings_uah, 20.0)
        self.assertEqual(savings.savings_percent, 20.0)

        req = OptimizationRequest(
            house=None,
            tariff=self.tariff,
            period=(self.timestamps[0], self.timestamps[-1]),
            step_minutes=15
        )
        self.assertEqual(req.step_minutes, 15)

    def test_load_profile_addition_and_energy(self) -> None:
        t0 = datetime(2026, 10, 5, 0, 0, tzinfo=timezone.utc)
        grid = [t0, t0 + timedelta(minutes=30)]
        p1 = LoadProfile(grid, [1.0, 2.0], 30)
        p2 = LoadProfile(grid, [0.5, 1.5], 30)
        combined = p1 + p2
        self.assertEqual(combined.power_kw, [1.5, 3.5])
        self.assertAlmostEqual(combined.energy_kwh(), 2.5)

    def test_incompatible_profiles_and_device_comparison(self) -> None:
        t0 = datetime(2026, 10, 5, 0, 0, tzinfo=timezone.utc)
        p1 = LoadProfile([t0, t0 + timedelta(minutes=15)], [1.0, 1.0], 15)
        p_diff_step = LoadProfile([t0, t0 + timedelta(minutes=30)], [1.0, 1.0], 30)
        with self.assertRaises(ValidationError):
            _ = p1 + p_diff_step

        d1 = CyclicDevice('washer', 1.8, 60, start_minute=10)
        d2 = CyclicDevice('washer', 1.8, 60, start_minute=10)
        d_heavy = FixedDevice('heater', 2.5)
        self.assertEqual(d1, d2)
        self.assertLess(d1, d_heavy)

    def test_json_roundtrip_and_strict_schema_validation(self) -> None:
        house_raw = json.loads(Path('config/house.json').read_text(encoding='utf-8'))
        house = House.from_dict(house_raw)
        restored_house = House.from_dict(house.to_dict())
        self.assertEqual(restored_house.house_id, 'demo-house-01')
        self.assertEqual(restored_house.devices, house.devices)

        tariffs_raw = json.loads(Path('config/tariffs.json').read_text(encoding='utf-8'))
        tariffs = tariffs_from_dict(tariffs_raw)
        restored_tariffs = tariffs_from_dict(tariffs_to_dict(tariffs))
        self.assertEqual(set(restored_tariffs.keys()), {'single', 'two_zone', 'three_zone'})

        t0 = datetime(2026, 10, 5, 1, 0, tzinfo=timezone.utc)
        sched = Schedule([ScheduledRun('washer', t0, 60)], device_ids=['washer', 'router'])
        restored_sched = Schedule.from_dict(sched.to_dict(device_ids=['washer', 'router']))
        self.assertEqual(list(restored_sched), list(sched))

        bad_version = house.to_dict()
        bad_version['schema_version'] = 99
        with self.assertRaises(ValidationError):
            House.from_dict(bad_version)

        bad_type = house.devices[0].to_dict()
        bad_type['type'] = 'unknown_device'
        with self.assertRaises(ValidationError):
            Device.from_dict(bad_type)
