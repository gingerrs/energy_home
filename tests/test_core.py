import unittest
from datetime import datetime, timedelta
from energyhome.core import calculate_cost, CostBreakdown

class DummyLoadProfile:
    def __init__(self, timestamps, power_kw, resolution_minutes=60):
        self.timestamps = timestamps
        self.power_kw = power_kw
        self.resolution_minutes = resolution_minutes

    def __len__(self):
        return len(self.timestamps)

    def __iter__(self):
        return iter(zip(self.timestamps, self.power_kw))

class DummySingleZoneTariff:
    def __init__(self, rate: float):
        self.rate = rate

    def rate_at(self, timestamp):
        return self.rate

class TestCoreCostCalculation(unittest.TestCase):
    def test_single_zone_constant_profile(self):
        start = datetime(2026, 1, 1, 10, 0)
        profile = DummyLoadProfile(
            timestamps=[start],
            power_kw=[1.0],
            resolution_minutes=60
        )
        tariff = DummySingleZoneTariff(rate=2.50)
        result = calculate_cost(profile, tariff)
        self.assertIsInstance(result, CostBreakdown)
        self.assertEqual(result.energy_kwh, 1.0)
        self.assertEqual(result.total_cost, 2.50)
        self.assertEqual(result.average_price, 2.50)

    def test_empty_profile_returns_zero(self):
        profile = DummyLoadProfile(
            timestamps=[],
            power_kw=[],
            resolution_minutes=60
        )
        tariff = DummySingleZoneTariff(rate=2.50)
        result = calculate_cost(profile, tariff)
        self.assertEqual(result.energy_kwh, 0.0)
        self.assertEqual(result.total_cost, 0.0)
        self.assertEqual(result.average_price, 0.0)

    def test_fractional_intervals_final_rounding(self):
        start = datetime(2026, 1, 1, 12, 0)
        timestamps = [start + timedelta(minutes=15 * i) for i in range(3)]
        powers = [1.333, 1.333, 1.333]
        profile = DummyLoadProfile(
            timestamps=timestamps,
            power_kw=powers,
            resolution_minutes=15
        )
        tariff = DummySingleZoneTariff(rate=4.321)
        result = calculate_cost(profile, tariff)
        self.assertEqual(result.total_cost, 4.32)

if __name__ == "__main__":
    unittest.main()