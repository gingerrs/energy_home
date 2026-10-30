import unittest
from datetime import datetime, timedelta
from energyhome.core import calculate_cost, CostBreakdown, detect_events

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

class TestDetectEventsW2(unittest.TestCase):
    def setUp(self) -> None:
        self.on_thresh = 0.8
        self.off_thresh = 0.2
        self.device_label = "kettle"
        self.base_time = datetime(2026, 10, 5, 0, 0)

    def _make_profile(self, values: list[float]) -> list[tuple[datetime, float]]:
        return [
            (self.base_time + timedelta(minutes=15 * i), val)
            for i, val in enumerate(values)
        ]

    def test_profile_00110_single_on_and_off(self) -> None:
        profile = self._make_profile([0.0, 0.0, 1.0, 1.0, 0.0])
        events = detect_events(
            profile,
            on_threshold=self.on_thresh,
            off_threshold=self.off_thresh,
            label=self.device_label,
        )

        self.assertEqual(len(events), 2)
        self.assertEqual(events[0].event_type, "on")
        self.assertEqual(events[0].timestamp, profile[2][0])
        self.assertEqual(events[0].device_id, self.device_label)

        self.assertEqual(events[1].event_type, "off")
        self.assertEqual(events[1].timestamp, profile[4][0])

    def test_steady_zero_and_empty_profile(self) -> None:
        self.assertEqual(detect_events([], self.on_thresh, self.off_thresh), ())

        zeros = self._make_profile([0.0, 0.0, 0.0])
        self.assertEqual(detect_events(zeros, self.on_thresh, self.off_thresh), ())

        high = self._make_profile([1.5, 1.5, 1.5])
        events_high = detect_events(high, self.on_thresh, self.off_thresh)
        self.assertEqual(len(events_high), 1)
        self.assertEqual(events_high[0].event_type, "on")
        self.assertEqual(events_high[0].timestamp, high[0][0])

    def test_noise_hysteresis_and_invalid_thresholds(self) -> None:
        noisy = self._make_profile([0.0, 1.0, 0.5, 0.7, 0.3, 0.6, 0.1])
        events = detect_events(noisy, self.on_thresh, self.off_thresh)
        self.assertEqual(len(events), 2)
        self.assertEqual(events[0].event_type, "on")
        self.assertEqual(events[0].timestamp, noisy[1][0])
        self.assertEqual(events[1].event_type, "off")
        self.assertEqual(events[1].timestamp, noisy[6][0])

        with self.assertRaises(ValueError):
            detect_events(noisy, on_threshold=0.5, off_threshold=0.5)

        with self.assertRaises(ValueError):
            detect_events(noisy, on_threshold=0.2, off_threshold=0.8)

if __name__ == "__main__":
    unittest.main()