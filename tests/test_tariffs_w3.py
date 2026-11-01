import json
import unittest
from collections import Counter
from copy import deepcopy
from dataclasses import FrozenInstanceError
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

from energyhome.analytics import build_demo1_summary, daily_summaries, plot_daily_profile
from energyhome.core import calculate_cost, compare_tariffs, merge_cost_breakdowns
from energyhome.domain import (
    LoadProfile, SingleZoneTariff, Tariff, TariffError, TariffValidationError, TariffZone,
    ThreeZoneTariff, TwoZoneTariff, ValidationError, tariff_from_config,
)


class TestTariffsW3(unittest.TestCase):
    def setUp(self):
        path = Path(__file__).resolve().parents[1] / 'config' / 'tariffs.json'
        self.config = json.loads(path.read_text(encoding='utf-8'))
        self.registry = self.config['tariffs']
        self.two = tariff_from_config('two_zone', self.registry)
        self.three = tariff_from_config('three_zone', self.registry)
        self.zone = ZoneInfo('Europe/Kyiv')

    def local(self, hour, minute=0, month=1, day=15):
        return datetime(2026, month, day, hour, minute, tzinfo=self.zone)

    def profile(self, start, count, step=60, power=1):
        utc_start = start.astimezone(timezone.utc)
        timestamps = [utc_start + timedelta(minutes=i * step) for i in range(count)]
        return LoadProfile(timestamps, [power] * count, step)

    def test_registry_builds_all_three_real_tariff_classes(self):
        for tariff_id, kind in (('single', SingleZoneTariff), ('two_zone', TwoZoneTariff),
                                ('three_zone', ThreeZoneTariff)):
            with self.subTest(tariff_id=tariff_id):
                tariff = tariff_from_config(tariff_id, self.registry)
                self.assertIsInstance(tariff, kind)
                self.assertIsInstance(tariff, Tariff)
                self.assertEqual(tariff.tariff_id, tariff_id)
                self.assertEqual(tariff.timezone_name, 'Europe/Kyiv')
        self.assertIs(self.config['synthetic'], True)
        self.assertEqual((self.config['currency'], self.config['energy_unit']), ('UAH', 'kWh'))

    def test_two_zone_required_boundaries(self):
        cases = ((22, 59, 'day', 2.5), (23, 0, 'night', 1.25),
                 (6, 59, 'night', 1.25), (7, 0, 'day', 2.5), (0, 0, 'night', 1.25))
        for hour, minute, expected_zone, expected_rate in cases:
            with self.subTest(hour=hour, minute=minute):
                stamp = self.local(hour, minute)
                self.assertEqual(self.two.zone_at(stamp), expected_zone)
                self.assertEqual(self.two.rate_at(stamp), expected_rate)

    def test_every_three_zone_boundary_is_half_open_to_microseconds(self):
        cases = ((7, 'night', 'day'), (8, 'day', 'peak'), (11, 'peak', 'day'),
                 (20, 'day', 'peak'), (22, 'peak', 'day'), (23, 'day', 'night'))
        rates = {'night': 1.0, 'day': 2.0, 'peak': 3.0}
        for hour, before, after in cases:
            with self.subTest(hour=hour):
                boundary = self.local(hour)
                previous = boundary - timedelta(microseconds=1)
                self.assertEqual(self.three.zone_at(previous), before)
                self.assertEqual(self.three.rate_at(previous), rates[before])
                self.assertEqual(self.three.zone_at(boundary), after)
                self.assertEqual(self.three.rate_at(boundary), rates[after])

    def test_every_minute_of_winter_and_summer_has_the_expected_zone_and_rate(self):
        for month in (1, 7):
            counts = Counter()
            for minute in range(1440):
                stamp = self.local(minute // 60, minute % 60, month=month)
                two_zone = 'night' if minute < 420 or minute >= 1380 else 'day'
                three_zone = (two_zone if two_zone == 'night' else
                              'peak' if 480 <= minute < 660 or 1200 <= minute < 1320 else 'day')
                self.assertEqual(self.two.zone_at(stamp), two_zone)
                self.assertEqual(self.two.rate_at(stamp), 1.25 if two_zone == 'night' else 2.5)
                self.assertEqual(self.three.zone_at(stamp), three_zone)
                self.assertEqual(self.three.rate_at(stamp),
                                 {'night': 1, 'day': 2, 'peak': 3}[three_zone])
                counts[three_zone] += 1
            self.assertEqual(counts, {'night': 480, 'day': 660, 'peak': 300})

    def test_equal_instants_in_different_offsets_have_equal_rates(self):
        for tariff in (self.two, self.three):
            for local in (self.local(7), self.local(23, month=7), self.local(0, month=7)):
                for zone in (timezone.utc, timezone(timedelta(hours=-7)), ZoneInfo('Asia/Tokyo')):
                    with self.subTest(tariff=tariff.tariff_id, local=local, zone=zone):
                        equivalent = local.astimezone(zone)
                        self.assertEqual(tariff.zone_at(equivalent), tariff.zone_at(local))
                        self.assertEqual(tariff.rate_at(equivalent), tariff.rate_at(local))

    def test_timezone_belongs_to_tariff_not_input_timestamp(self):
        entry = deepcopy(self.registry['two_zone'])
        entry['timezone_name'] = 'America/New_York'
        tariff = tariff_from_config('other', {'other': entry})
        stamp = datetime(2026, 1, 15, 6, tzinfo=timezone.utc)
        self.assertEqual(tariff.zone_at(stamp), 'night')
        self.assertEqual(self.two.zone_at(stamp), 'day')

    def test_repeated_dst_hour_has_two_instants_in_the_same_local_zone(self):
        first = datetime(2026, 10, 25, 3, 30, tzinfo=self.zone, fold=0)
        second = first.replace(fold=1)
        self.assertNotEqual(first.astimezone(timezone.utc), second.astimezone(timezone.utc))
        for tariff in (self.two, self.three):
            self.assertEqual(tariff.zone_at(first), 'night')
            self.assertEqual(tariff.zone_at(second), 'night')
            self.assertEqual(tariff.rate_at(first), tariff.rate_at(second))

    def test_invalid_timestamps_raise_both_public_error_types(self):
        for tariff in (self.two, self.three):
            for timestamp in (datetime(2026, 1, 15), '2026-01-15', None, 0):
                for method in (tariff.zone_at, tariff.rate_at):
                    with self.subTest(tariff=tariff.tariff_id, timestamp=timestamp):
                        with self.assertRaises(TariffValidationError) as caught:
                            method(timestamp)
                        self.assertIsInstance(caught.exception, ValidationError)
                        self.assertIsInstance(caught.exception, TariffError)

    def test_zero_rates_are_valid(self):
        zones = [TariffZone('night', 1380, 420, 0), TariffZone('day', 420, 1380, 0)]
        tariff = TwoZoneTariff('free', 'Synthetic free', 'UTC', zones)
        self.assertEqual(calculate_cost(self.profile(self.local(0), 24), tariff).total_cost, 0)

    def test_unrepresentable_timezone_conversion_is_a_validation_error(self):
        for stamp in (
            datetime.min.replace(tzinfo=timezone(timedelta(hours=14))),
            datetime.max.replace(tzinfo=timezone(timedelta(hours=-14))),
        ):
            with self.subTest(stamp=stamp):
                with self.assertRaises(TariffValidationError):
                    self.two.rate_at(stamp)

    def test_zone_rejects_invalid_rates(self):
        for rate in (-1, float('nan'), float('inf'), -float('inf'), True, False,
                     '1.25', None, Decimal('1.25'), 10 ** 400):
            with self.subTest(rate=rate):
                with self.assertRaises(TariffValidationError):
                    TariffZone('night', 1380, 420, rate)

    def test_zone_rejects_invalid_boundaries_and_empty_interval(self):
        for start, end in ((0, 0), (420, 420), (-1, 420), (1440, 420), (1380, 1441),
                           (1380, -1), (1380.0, 420), (1380, 420.5), (True, 420),
                           (1380, False), ('23:00', 420), (1380, None)):
            with self.subTest(start=start, end=end):
                with self.assertRaises(TariffValidationError):
                    TariffZone('night', start, end, 1)

    def test_zone_rejects_invalid_id_and_is_immutable(self):
        for zone_id in ('', ' ', None, 1):
            with self.subTest(zone_id=zone_id):
                with self.assertRaises(TariffValidationError):
                    TariffZone(zone_id, 0, 420, 1)
        zone = TariffZone('night', 0, 420, 1)
        with self.assertRaises(FrozenInstanceError):
            zone.rate = 100

    def test_gaps_overlaps_and_missing_midnight_coverage_are_rejected(self):
        for start, end in ((1381, 420), (1379, 420), (1380, 419), (1380, 421), (1380, 0)):
            with self.subTest(start=start, end=end):
                zones = [TariffZone('night', start, end, 1), TariffZone('day', 420, 1380, 2)]
                with self.assertRaises(TariffValidationError):
                    TwoZoneTariff('two', 'Synthetic two', 'UTC', zones)

    def test_zero_endpoint_and_split_midnight_are_supported(self):
        zones = [TariffZone('night', 1380, 0, 1.25), TariffZone('night', 0, 420, 1.25),
                 TariffZone('day', 420, 1380, 2.5)]
        tariff = TwoZoneTariff('split', 'Synthetic split', 'Europe/Kyiv', zones)
        for hour in range(24):
            self.assertEqual(tariff.rate_at(self.local(hour)), self.two.rate_at(self.local(hour)))

    def test_1440_is_an_exclusive_end_not_an_allowed_start(self):
        zones = [TariffZone('night', 0, 420, 1), TariffZone('day', 420, 1440, 2)]
        tariff = TwoZoneTariff('two', 'Synthetic two', 'UTC', zones)
        self.assertEqual(tariff.zone_at(datetime(2026, 1, 15, 23, 59, tzinfo=timezone.utc)), 'day')
        self.assertEqual(tariff.zone_at(datetime(2026, 1, 16, tzinfo=timezone.utc)), 'night')

    def test_unsupported_or_missing_zone_ids_are_rejected(self):
        for kind, zones in (
            (TwoZoneTariff, [TariffZone('off_peak', 1380, 420, 1),
                            TariffZone('day', 420, 1380, 2)]),
            (TwoZoneTariff, [TariffZone('night', 0, 1440, 1)]),
            (ThreeZoneTariff, self.two.zones),
            (TwoZoneTariff, self.three.zones),
        ):
            with self.subTest(kind=kind):
                with self.assertRaises(TariffValidationError):
                    kind('invalid', 'Synthetic invalid', 'UTC', zones)

    def test_repeated_zone_ids_cannot_have_different_rates(self):
        zones = [TariffZone('night', 0, 420, 1), TariffZone('day', 420, 1380, 2),
                 TariffZone('night', 1380, 1440, 3)]
        with self.assertRaises(TariffValidationError):
            TwoZoneTariff('two', 'Synthetic two', 'UTC', zones)

    def test_invalid_metadata_and_zone_containers_are_validation_errors(self):
        for tariff_id, name, tz in (('', 'Name', 'UTC'), (' ', 'Name', 'UTC'),
                                    ('two', None, 'UTC'), ('two', 'Name', ''),
                                    ('two', 'Name', 'Unknown/Zone'), ('two', 'Name', None)):
            with self.subTest(tariff_id=tariff_id, name=name, tz=tz):
                with self.assertRaises(TariffValidationError):
                    TwoZoneTariff(tariff_id, name, tz, self.two.zones)
        for zones in (None, 1, [], ['night', 'day'], [object()]):
            with self.subTest(zones=zones):
                with self.assertRaises(TariffValidationError):
                    TwoZoneTariff('two', 'Name', 'UTC', zones)

    def test_constructor_and_factory_copy_input_data(self):
        zones = list(self.two.zones)
        tariff = TwoZoneTariff('copy', 'Synthetic copy', 'Europe/Kyiv', iter(zones))
        zones.clear()
        self.assertEqual(tariff.rate_at(self.local(0)), 1.25)
        self.registry['two_zone']['zones'][0]['rate'] = 100
        self.assertEqual(self.two.rate_at(self.local(0)), 1.25)
        self.assertEqual(len(tariff.zones), 2)

    def test_lookups_do_not_mutate_configuration_or_break_chunk_merging(self):
        before = deepcopy(vars(self.three))
        for hour in range(24):
            self.three.zone_at(self.local(hour))
            self.three.rate_at(self.local(hour))
        self.assertEqual(vars(self.three), before)
        profile = self.profile(self.local(0), 24)
        full = calculate_cost(profile, self.three)
        for split in range(1, 24):
            first = calculate_cost(self.profile(self.local(0), split), self.three)
            second = calculate_cost(self.profile(self.local(split), 24 - split), self.three)
            self.assertEqual(merge_cost_breakdowns([first, second]), full)

    def test_input_zone_order_is_canonical_for_equivalent_core_snapshots(self):
        tariff = ThreeZoneTariff(self.three.tariff_id, self.three.name,
                                 self.three.timezone_name, reversed(self.three.zones))
        self.assertEqual(vars(tariff), vars(self.three))
        first = calculate_cost(self.profile(self.local(0), 1), self.three)
        second = calculate_cost(self.profile(self.local(1), 1), tariff)
        self.assertEqual(merge_cost_breakdowns([first, second]),
                         calculate_cost(self.profile(self.local(0), 2), self.three))

    def test_unknown_config_ids_types_and_malformed_entries_are_rejected(self):
        for entry in (None, [], 'single', {}, {'type': 'unknown'},
                      dict(self.registry['single'], extra=True),
                      dict(self.registry['two_zone'], zones=[{'zone_id': 'night'}]),
                      dict(self.registry['two_zone'], zones=None)):
            with self.subTest(entry=entry):
                with self.assertRaises(ValidationError):
                    tariff_from_config('invalid', {'invalid': entry})
        with self.assertRaises(ValidationError):
            tariff_from_config('unknown_id', self.registry)

    def test_existing_single_constructor_and_config_preserve_legacy_api(self):
        old = SingleZoneTariff('single', 'Synthetic single', 'Europe/Kyiv', 2.5)
        configured = tariff_from_config('single', self.registry)
        profile = self.profile(self.local(0), 4, step=15)
        self.assertEqual(calculate_cost(profile, old), calculate_cost(profile, configured))
        self.assertEqual(old.zone_at(self.local(23)), 'single')
        self.assertEqual(SingleZoneTariff('free', 'Free', 'UTC', 0).rate_at(self.local(0)), 0)

    def test_real_core_compares_three_tariffs_without_a_separate_zone_algorithm(self):
        profile = self.profile(self.local(0), 24)
        tariffs = [tariff_from_config(key, self.registry) for key in self.registry]
        costs = compare_tariffs(profile, tariffs)
        self.assertEqual({key: value.total_cost for key, value in costs.items()},
                         {'single': 60, 'two_zone': 50, 'three_zone': 45})
        self.assertEqual(costs['three_zone'].zone_energy, {'night': 8, 'day': 11, 'peak': 5})
        self.assertEqual(costs['three_zone'].zone_cost, {'night': 8, 'day': 22, 'peak': 15})
        self.assertEqual(costs['two_zone'].zone_energy, {'night': 8, 'day': 16})
        self.assertEqual(self.three.cost(profile), 45)
        self.assertEqual(build_demo1_summary(profile, self.three).cost_uah, Decimal('45.0'))

    def test_real_core_counts_actual_dst_intervals(self):
        for month, day, hours, night in ((3, 29, 23, 7), (10, 25, 25, 9)):
            with self.subTest(month=month):
                profile = self.profile(self.local(0, month=month, day=day), hours)
                result = calculate_cost(profile, self.three)
                self.assertEqual(result.energy_kwh, hours)
                self.assertEqual(result.zone_energy, {'night': night, 'day': 11, 'peak': 5})
                self.assertEqual(result.total_cost, night + 37)
                daily = daily_summaries(profile, self.three)
                self.assertEqual(len(daily), 1)
                self.assertEqual(daily[0].coverage_pct, 100)
                self.assertEqual(daily[0].cost_uah, Decimal(str(night + 37)))

    def test_plot_uses_the_same_real_tariff_rates(self):
        import matplotlib.pyplot as plt

        timestamps = [self.local(hour) for hour in (6, 7, 8, 11, 20, 22, 23)]
        figure = plot_daily_profile(timestamps, [1] * len(timestamps), self.three)
        try:
            self.assertEqual(list(figure.axes[1].lines[0].get_ydata()), [1, 2, 3, 2, 3, 2, 1])
        finally:
            plt.close(figure)
