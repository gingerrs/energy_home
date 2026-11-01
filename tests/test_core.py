import json
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from energyhome.core import (
    CostBreakdown, ProfileMetrics, calculate_cost, detect_events,
    merge_cost_breakdowns, profile_metrics,
)
from energyhome.domain import LoadProfile, tariff_from_config

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

KYIV = ZoneInfo('Europe/Kyiv')
REGISTRY = json.loads((Path(__file__).parents[1] / 'config' / 'tariffs.json')
                      .read_text(encoding='utf-8'))['tariffs']

def kyiv_profile(start: datetime, powers: list[float]) -> LoadProfile:
    return LoadProfile([start + timedelta(minutes=15 * i)
                        for i in range(len(powers))], powers, 15)

class TestDemo1Regressions(unittest.TestCase):
    def setUp(self) -> None:
        self.tariff = {name: tariff_from_config(name, REGISTRY)
                       for name in ('single', 'two_zone', 'three_zone')}

    def test_hand_computed_slices_at_23_00_and_07_00(self) -> None:
        for month in (1, 7):  
            start = datetime(2026, month, 15, 22, 30, tzinfo=KYIV)
            cost = calculate_cost(kyiv_profile(start, [2, 2, 4, 4]),
                                  self.tariff['two_zone'])
            self.assertEqual(cost.zone_energy, {'day': 1.0, 'night': 2.0})
            self.assertEqual(cost.zone_cost, {'day': 2.5, 'night': 2.5})
            self.assertEqual(cost.total_cost, 5.0)

        start = datetime(2026, 3, 2, 6, 30, tzinfo=KYIV)
        cost = calculate_cost(kyiv_profile(start, [2.0] * 7),
                              self.tariff['three_zone'])
        self.assertEqual(cost.zone_energy,
                         {'night': 1.0, 'day': 2.0, 'peak': 0.5})
        self.assertEqual(cost.total_cost, 1.0 * 1 + 2.0 * 2 + 0.5 * 3)

    def test_full_and_chunked_rounded_totals_are_equal(self) -> None:
        start = datetime(2026, 1, 15, tzinfo=KYIV)
        profile = kyiv_profile(start, [0.013] * 96)  
        for name, tariff in self.tariff.items():
            full = calculate_cost(profile, tariff)
            for size in (1, 17, 1000):
                with self.subTest(tariff=name, chunk_size=size):
                    parts = [calculate_cost(LoadProfile(
                        profile.timestamps[i:i + size],
                        profile.power_kw[i:i + size], 15), tariff)
                        for i in range(0, len(profile), size)]
                    merged = merge_cost_breakdowns(parts)
                    self.assertEqual(merged.total_cost, full.total_cost)
                    self.assertEqual(merged.zone_cost, full.zone_cost)
                    self.assertEqual(merged.zone_energy, full.zone_energy)
        single = [calculate_cost(LoadProfile([t], [0.013], 15),
                                 self.tariff['single']) for t in
                  profile.timestamps]
        self.assertAlmostEqual(sum(p.total_cost for p in single), 0.96)
        self.assertEqual(merge_cost_breakdowns(single).total_cost, 0.78)

    def test_agreed_unevenness_and_mean_price_hand_example(self) -> None:
        start = datetime(2026, 1, 15, 22, 30, tzinfo=KYIV)
        profile = kyiv_profile(start, [1, 2, 0, 1])
        metrics = profile_metrics(profile, self.tariff['two_zone'])
        self.assertEqual(metrics, ProfileMetrics(2.0, 1.0, 2.0, 2.1875))
        cost = calculate_cost(profile, self.tariff['two_zone'])
        self.assertEqual((cost.total_cost, cost.average_price), (2.19, 2.19))

    def test_zero_profile_and_mixed_tariffs(self) -> None:
        start = datetime(2026, 1, 15, 22, 30, tzinfo=KYIV)
        zero = kyiv_profile(start, [0.0] * 4)
        cost = calculate_cost(zero, self.tariff['two_zone'])
        self.assertEqual(cost.zone_energy, {'day': 0.0, 'night': 0.0})
        self.assertEqual((cost.total_cost, cost.average_price), (0.0, 0.0))
        self.assertEqual(profile_metrics(zero, self.tariff['two_zone']),
                         ProfileMetrics(0.0, 0.0, None, None))
        with self.assertRaises(ValueError):
            merge_cost_breakdowns([
                calculate_cost(kyiv_profile(start, [1.0]), self.tariff[name])
                for name in ('two_zone', 'three_zone')])

