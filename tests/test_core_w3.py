import unittest
import weakref
from dataclasses import FrozenInstanceError
from datetime import datetime, timedelta, timezone
from fractions import Fraction
from unittest.mock import patch
from zoneinfo import ZoneInfo

from energyhome.core import (
    CostBreakdown, ProfileMetrics, calculate_cost, compare_tariffs,
    mean_price, merge_cost_breakdowns, peak_kw, profile_metrics,
)
from energyhome.domain import LoadProfile, SingleZoneTariff, Tariff, TariffError


class SyntheticTariff(Tariff):
    """Test-only schedule with synthetic rates, not an implementation of W3-A."""

    def __init__(self, tariff_id='two_zone', zones=None):
        super().__init__(tariff_id, 'Synthetic fixture', 'Europe/Kyiv')
        self.zones = zones or ((0, 'night', 1.25), (420, 'day', 2.5), (1380, 'night', 1.25))

    def _entry(self, timestamp):
        local = timestamp.astimezone(ZoneInfo(self.timezone_name))
        minute = local.hour * 60 + local.minute
        return next(entry for entry in reversed(self.zones) if minute >= entry[0])

    def zone_at(self, timestamp):
        return self._entry(timestamp)[1]

    def rate_at(self, timestamp):
        return self._entry(timestamp)[2]


class TestCoreW3(unittest.TestCase):
    def setUp(self):
        self.start = datetime(2026, 1, 15, tzinfo=timezone.utc)
        self.single = SingleZoneTariff('single', 'Synthetic single', 'Europe/Kyiv', 2.5)
        self.two = SyntheticTariff()

    def profile(self, powers, step=15, start=None):
        start = self.start if start is None else start
        return LoadProfile([start + timedelta(minutes=step * i) for i in range(len(powers))],
                           powers, step)

    def test_required_four_interval_metrics(self):
        profile = self.profile([1, 2, 0, 1])
        self.assertEqual(peak_kw(profile), 2)
        self.assertEqual(profile_metrics(profile, self.single), ProfileMetrics(2, 1, 2, 2.5))
        result = calculate_cost(profile, self.single)
        self.assertEqual(result.energy_kwh, 1)
        self.assertEqual(result.total_cost, 2.5)
        self.assertEqual(mean_price(result), 2.5)

    def test_empty_and_zero_profiles_have_explicit_undefined_ratios(self):
        for profile in (None, [], self.profile([0, 0, 0])):
            with self.subTest(profile=profile):
                self.assertEqual(profile_metrics(profile, self.single),
                                 ProfileMetrics(0, 0, None, None))
                self.assertEqual(peak_kw(profile), 0)
                result = calculate_cost(profile, self.single)
                self.assertEqual(result.total_cost, 0)
                self.assertIsNone(mean_price(result))
        self.assertEqual(merge_cost_breakdowns([]).total_cost, 0)

    def test_zero_rate_has_defined_zero_price_when_energy_is_positive(self):
        free = SingleZoneTariff('free', 'Synthetic free', 'UTC', 0)
        metrics = profile_metrics(self.profile([1, 1]), free)
        self.assertEqual(metrics, ProfileMetrics(1, 1, 1, 0))

    def test_mean_price_uses_unrounded_energy_and_cost(self):
        for power in (0.000001, 0.01):
            with self.subTest(power=power):
                result = calculate_cost(self.profile([power], step=1), self.single)
                self.assertEqual(result.total_cost, 0)
                self.assertEqual(mean_price(result), 2.5)

    def test_mean_price_supports_the_old_five_field_dto(self):
        legacy = CostBreakdown(2, 5, 2.5, {'single': 2}, {'single': 5})
        self.assertEqual(mean_price(legacy), 2.5)
        self.assertIsNone(mean_price(CostBreakdown(0, 0, 0, {}, {})))

    def test_compare_tariffs_returns_cost_dtos_and_calls_one_engine(self):
        start = datetime(2026, 1, 15, 22, 45, tzinfo=ZoneInfo('Europe/Kyiv'))
        profile = self.profile([1, 2, 0, 1], start=start)
        before = list(profile)
        with patch('energyhome.core.calculate_cost', wraps=calculate_cost) as engine:
            result = compare_tariffs(profile, (self.single, self.two))
        self.assertEqual(engine.call_count, 2)
        self.assertEqual(set(result), {'single', 'two_zone'})
        self.assertTrue(all(isinstance(cost, CostBreakdown) for cost in result.values()))
        self.assertEqual(result['single'].total_cost, 2.5)
        self.assertEqual(result['two_zone'].total_cost, 1.56)
        self.assertEqual(result['two_zone'].zone_energy, {'day': 0.25, 'night': 0.75})
        self.assertEqual(result['two_zone'].zone_cost, {'day': 0.63, 'night': 0.94})
        self.assertEqual(profile_metrics(profile, self.two).mean_price, 1.5625)
        self.assertEqual(list(profile), before)

    def test_comparison_rejects_duplicate_ids_and_handles_empty_inputs(self):
        with self.assertRaisesRegex(ValueError, 'unique'):
            compare_tariffs(self.profile([1]), [self.single, self.single])
        self.assertEqual(compare_tariffs(None, []), {})
        result = compare_tariffs(None, [self.single, self.two])
        self.assertTrue(all(part.total_cost == 0 for part in result.values()))

    def test_zone_boundaries_are_delegated_to_tariff_in_local_time(self):
        for hour, minute, zone, cost in ((22, 59, 'day', 0.04), (23, 0, 'night', 0.02),
                                         (6, 59, 'night', 0.02), (7, 0, 'day', 0.04)):
            with self.subTest(hour=hour, minute=minute):
                stamp = datetime(2026, 1, 15, hour, minute, tzinfo=ZoneInfo('Europe/Kyiv'))
                result = calculate_cost(self.profile([1], step=1, start=stamp), self.two)
                self.assertEqual(set(result.zone_energy), {zone})
                self.assertEqual(result.total_cost, cost)

    def test_three_zones_use_the_same_core_contract(self):
        tariff = SyntheticTariff('three_zone', ((0, 'night', 1), (420, 'day', 2),
                                               (480, 'peak', 3), (660, 'day', 2),
                                               (1200, 'peak', 3), (1320, 'day', 2),
                                               (1380, 'night', 1)))
        start = datetime(2026, 1, 15, tzinfo=ZoneInfo('Europe/Kyiv'))
        result = calculate_cost(self.profile([1] * 24, step=60, start=start), tariff)
        self.assertEqual(result.energy_kwh, 24)
        self.assertEqual(result.zone_energy, {'night': 8, 'day': 11, 'peak': 5})
        self.assertEqual(result.total_cost, 45)
        self.assertEqual(result.zone_cost, {'night': 8, 'day': 22, 'peak': 15})

    def test_merge_rounds_exact_totals_only_after_combining(self):
        tariff = SingleZoneTariff('tiny', 'Synthetic rounding', 'UTC', 1)
        profile = self.profile([0.01] * 4)
        parts = [calculate_cost(self.profile([power], start=stamp), tariff)
                 for stamp, power in profile]
        self.assertEqual(sum(part.total_cost for part in parts), 0)
        self.assertEqual(merge_cost_breakdowns(parts), calculate_cost(profile, tariff))
        self.assertEqual(merge_cost_breakdowns(parts).total_cost, 0.01)
        self.assertEqual(merge_cost_breakdowns(parts[:2]).total_cost, 0.01)
        self.assertEqual(parts[0]._raw_energy, {'single': Fraction(1, 400)})

    def test_all_chunk_boundaries_and_nested_merges_match_full_result(self):
        start = datetime(2026, 1, 15, 22, 58, tzinfo=ZoneInfo('Europe/Kyiv'))
        powers = [0.013, 1.333, 0, 2.111, 0.001, 1.234, 0.01]
        profile = self.profile(powers, step=1, start=start)
        expected = calculate_cost(profile, self.two)
        parts = [calculate_cost(self.profile([power], step=1, start=stamp), self.two)
                 for stamp, power in profile]
        before = [(dict(part._raw_energy), dict(part._raw_cost)) for part in parts]
        for boundary in range(len(parts) + 1):
            with self.subTest(boundary=boundary):
                merged = merge_cost_breakdowns((merge_cost_breakdowns(parts[:boundary]),
                                               merge_cost_breakdowns(parts[boundary:])))
                self.assertEqual(merged, expected)
        self.assertEqual(before, [(part._raw_energy, part._raw_cost) for part in parts])

    def test_merge_rejects_different_tariffs_and_rounded_only_legacy_values(self):
        profile = self.profile([1])
        with self.assertRaisesRegex(ValueError, 'different tariff'):
            merge_cost_breakdowns([calculate_cost(profile, self.single),
                                   calculate_cost(profile, self.two)])
        with self.assertRaisesRegex(ValueError, 'rounded-only'):
            merge_cost_breakdowns([CostBreakdown(1, 2.5, 2.5, {}, {})])

    def test_reducer_releases_processed_parts_instead_of_building_a_list(self):
        references = []
        def parts():
            for _ in range(100):
                self.assertLessEqual(sum(ref() is not None for ref in references), 1)
                part = calculate_cost(self.profile([1]), self.single)
                references.append(weakref.ref(part))
                yield part
        result = merge_cost_breakdowns(parts())
        self.assertEqual(result.energy_kwh, 25)
        self.assertEqual(result.total_cost, 62.5)
        self.assertTrue(all(ref() is None for ref in references))

    def test_metrics_are_immutable_and_tariff_errors_are_preserved(self):
        metrics = profile_metrics(self.profile([1]), self.single)
        with self.assertRaises(FrozenInstanceError):
            metrics.peak_kw = 999
        error = TariffError('Invalid tariff fixture')
        with patch.object(self.single, 'rate_at', side_effect=error):
            with self.assertRaises(TariffError) as caught:
                compare_tariffs(self.profile([1]), [self.single])
        self.assertIs(caught.exception, error)

    def test_empty_parts_are_neutral_for_every_tariff_and_nested_merge(self):
        profile = self.profile([1])
        for tariff in (self.single, self.two):
            with self.subTest(tariff=tariff.tariff_id):
                full = calculate_cost(profile, tariff)
                empty = calculate_cost(None, tariff)
                for parts in ((empty, full), (full, empty), (empty, full, empty)):
                    self.assertEqual(merge_cost_breakdowns(parts), full)
                nested = merge_cost_breakdowns([empty, merge_cost_breakdowns([empty, full])])
                self.assertEqual(nested, full)
                self.assertEqual(merge_cost_breakdowns([empty, empty]), empty)
        zero = calculate_cost(self.profile([0]), self.two)
        self.assertEqual(merge_cost_breakdowns([calculate_cost(None, self.two), zero]), zero)

    def test_same_id_with_different_rates_timezones_or_types_is_rejected(self):
        profile = self.profile([1])
        class NightTariff(SingleZoneTariff):
            def zone_at(self, timestamp):
                return 'night'
        incompatible = (
            SingleZoneTariff('single', 'Synthetic single', 'Europe/Kyiv', 10),
            SingleZoneTariff('single', 'Synthetic single', 'UTC', 2.5),
            NightTariff('single', 'Synthetic single', 'Europe/Kyiv', 2.5),
        )
        first = calculate_cost(profile, self.single)
        for tariff in incompatible:
            with self.subTest(tariff=tariff):
                with self.assertRaisesRegex(ValueError, 'tariff configurations'):
                    merge_cost_breakdowns([first, calculate_cost(profile, tariff)])

    def test_equivalent_tariff_instances_can_be_merged(self):
        same = SingleZoneTariff('single', 'Synthetic single', 'Europe/Kyiv', 2.5)
        first = calculate_cost(self.profile([1]), self.single)
        second = calculate_cost(self.profile([1], start=self.start + timedelta(minutes=15)), same)
        self.assertEqual(merge_cost_breakdowns([first, second]),
                         calculate_cost(self.profile([1, 1]), self.single))

    def test_tariff_configuration_is_a_deep_snapshot(self):
        zones = [list(zone) for zone in self.two.zones]
        tariff = SyntheticTariff(zones=zones)
        profile = self.profile([1])
        first = calculate_cost(profile, tariff)
        zones[0][2] = 10
        second = calculate_cost(profile, tariff)
        self.assertEqual(mean_price(first), 1.25)
        self.assertEqual(mean_price(second), 10)
        with self.assertRaisesRegex(ValueError, 'tariff configurations'):
            merge_cost_breakdowns([first, second])

    def test_legacy_average_price_keeps_its_display_value_formula(self):
        cases = (([0.01], 15, 4.0), ([0.000001], 1, 0.0), ([1.333] * 3, 15, 2.5005))
        for powers, step, expected in cases:
            with self.subTest(powers=powers):
                result = calculate_cost(self.profile(powers, step), self.single)
                self.assertEqual(result.average_price, expected)
                self.assertEqual(mean_price(result), 2.5)
