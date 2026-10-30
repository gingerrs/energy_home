import unittest
from datetime import datetime, timedelta, timezone

from energyhome.domain import (
    CyclicDevice, DeviceError, EnergyMeter, FixedDevice, House,
    ThermostaticDevice, ValidationError,
)

START = datetime(2026, 1, 1, tzinfo=timezone.utc)


def hours(n: int) -> tuple[datetime, datetime]:
    return START, START + timedelta(hours=n)


def on_flags(profile) -> list[bool]:
    return [power > 0 for power in profile.power_kw]


class FixedDeviceTest(unittest.TestCase):
    def test_1kw_for_one_hour_is_1kwh(self) -> None:
        device = FixedDevice('fridge', 1.0)
        profile = device.profile_for(hours(1), 15)
        self.assertEqual(len(profile), 4)
        self.assertAlmostEqual(profile.energy_kwh(), 1.0)
        self.assertEqual(profile.start_time(), START)
        self.assertFalse(device.shiftable)


class CyclicDeviceTest(unittest.TestCase):
    def test_known_grid(self) -> None:
        washer = CyclicDevice('washer', 2.0, cycle_minutes=30, start_minute=15)
        profile = washer.profile_for(hours(1), 15)
        self.assertEqual(on_flags(profile), [False, True, True, False])
        self.assertAlmostEqual(profile.energy_kwh(), 1.0)
        self.assertTrue(washer.shiftable)


class ThermostaticDeviceTest(unittest.TestCase):
    def test_quarter_duty_cycle(self) -> None:
        boiler = ThermostaticDevice('boiler', 2.0, duty_cycle=0.25)
        profile = boiler.profile_for(hours(2), 15)
        self.assertEqual(on_flags(profile), [True, False, False, False] * 2)
        self.assertAlmostEqual(profile.energy_kwh(), 1.0)
        self.assertFalse(boiler.shiftable)

    def test_duty_cycle_edges(self) -> None:
        off = ThermostaticDevice('a', 1.0, duty_cycle=0)
        full = ThermostaticDevice('b', 1.0, duty_cycle=1)
        self.assertEqual(off.profile_for(hours(1), 15).energy_kwh(), 0.0)
        energy = full.profile_for(hours(1), 15).energy_kwh()
        self.assertAlmostEqual(energy, 1.0)


class ValidationTest(unittest.TestCase):
    def test_invalid_parameters(self) -> None:
        cases = [
            lambda: FixedDevice('  ', 1.0),
            lambda: FixedDevice('x', -1.0),
            lambda: CyclicDevice('x', 1.0, cycle_minutes=-30),
            lambda: ThermostaticDevice('x', 1.0, duty_cycle=1.5),
            lambda: ThermostaticDevice('x', 1.0, duty_cycle=-0.1),
            lambda: EnergyMeter('', 5.0),
            lambda: EnergyMeter('m1', 0),
            lambda: FixedDevice('x', 1.0).profile_for((START, START), 15),
            lambda: FixedDevice('x', 1.0).profile_for(hours(1), 0),
        ]
        for i, make in enumerate(cases):
            with self.subTest(case=i), self.assertRaises(ValidationError):
                make()

    def test_validation_error_is_device_error(self) -> None:
        self.assertTrue(issubclass(ValidationError, DeviceError))

    def test_house_rejects_duplicate_device_ids(self) -> None:
        meter = EnergyMeter('m1', 5.0)
        with self.assertRaises(ValidationError):
            House('h1', meter, [FixedDevice('d', 1), FixedDevice('d', 2)])
