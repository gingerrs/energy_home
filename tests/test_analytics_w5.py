"""W5-D: DataFrame contract, energy-preserving resample and rolling stats."""
import json
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

from energyhome.analytics import (
    FRAME_COLUMNS, daily_summaries, dataframe_summary, profile_to_dataframe,
    rolling_stats,
)
from energyhome.dataio import load_demo_profile
from energyhome.domain import LoadProfile, tariff_from_config

ROOT = Path(__file__).resolve().parents[1]


def utc_profile(start: datetime, powers: list[float], step: int = 15) -> LoadProfile:
    """Regular UTC profile starting at `start`."""
    return LoadProfile([start + timedelta(minutes=step * i)
                        for i in range(len(powers))], powers, step)


class TestDataFrameW5(unittest.TestCase):
    def test_fixture_frame_index_rows_and_energy(self) -> None:
        frame = profile_to_dataframe(
            load_demo_profile(ROOT / 'data' / 'demo' / 'meter_15min.csv'))
        self.assertEqual(tuple(frame.columns), FRAME_COLUMNS)
        self.assertEqual(str(frame.index.dtype), 'datetime64[ns, UTC]')
        self.assertEqual(frame.index.name, 'timestamp')
        self.assertEqual([t.isoformat() for t in frame.index],
                         [f'2026-01-15T00:{m:02d}:00+00:00' for m in (0, 15, 30, 45)])
        self.assertEqual(frame['energy_kwh'].tolist(), [0.25, 0.5, 0.0, 0.25])
        # 00:00 UTC on Thursday is 02:00 in Kyiv (UTC+2).
        self.assertEqual(set(frame['hour']), {2})
        self.assertEqual(set(frame['day_of_week']), {3})

    def test_resample_keeps_energy_and_matches_core_daily_totals(self) -> None:
        # 2026-10-25 is the Kyiv DST fall-back day: 25 local hours.
        start = datetime(2026, 10, 23, 21, tzinfo=UTC)  # 2026-10-24 00:00 Kyiv
        powers = [round(0.1 + (i % 7) * 0.35, 2) for i in range(4 * 73)]
        profile = utc_profile(start, powers)
        frame = profile_to_dataframe(profile)
        daily = dataframe_summary(frame, 'D')
        self.assertAlmostEqual(daily['energy_kwh'].sum(), profile.energy_kwh())
        self.assertEqual(daily['intervals'].tolist(), [96, 100, 96])
        registry = json.loads((ROOT / 'config' / 'tariffs.json')
                              .read_text(encoding='utf-8'))['tariffs']
        reference = daily_summaries(profile, tariff_from_config('single', registry))
        self.assertEqual([d.day for d in reference], list(daily.index.date))
        for day, expected in zip(daily['energy_kwh'], reference):
            self.assertAlmostEqual(round(day, 4), expected.energy_kwh)
        hourly = dataframe_summary(frame, 'h')
        self.assertAlmostEqual(hourly['energy_kwh'].sum(), profile.energy_kwh())

    def test_rolling_window_two_and_min_periods(self) -> None:
        frame = profile_to_dataframe(
            utc_profile(datetime(2026, 1, 15, tzinfo=UTC), [1.0, 2.0, 3.0]))
        strict = rolling_stats(frame, 2)
        self.assertTrue(strict.iloc[0].isna().all())
        self.assertEqual(strict['rolling_mean_kw'].tolist()[1:], [1.5, 2.5])
        self.assertEqual(strict['rolling_peak_kw'].tolist()[1:], [2.0, 3.0])
        loose = rolling_stats(frame, 2, min_periods=1)
        self.assertEqual(loose['rolling_mean_kw'].tolist(), [1.0, 1.5, 2.5])
        with self.assertRaises(ValueError):
            rolling_stats(frame, 0)

    def test_empty_and_timezone_boundaries(self) -> None:
        empty = profile_to_dataframe([])
        self.assertEqual((len(empty), tuple(empty.columns)), (0, FRAME_COLUMNS))
        self.assertTrue(dataframe_summary(empty).empty)
        self.assertTrue(rolling_stats(empty, 2).empty)
        # Fall-back day: local 03:00 occurs twice, the UTC index stays ordered.
        start = datetime(2026, 10, 24, 21, tzinfo=UTC)  # 2026-10-25 00:00 Kyiv
        frame = profile_to_dataframe(utc_profile(start, [1.0] * 100))
        self.assertTrue(frame.index.is_monotonic_increasing)
        self.assertEqual(set(frame['date'].astype(str)), {'2026-10-25'})
        self.assertEqual(int((frame['hour'] == 3).sum()), 8)
        # Only the selected half-open period [00:30, 01:00) is converted.
        selected = profile_to_dataframe(utc_profile(start, [1.0] * 8),
                                        start=start + timedelta(minutes=30),
                                        end=start + timedelta(minutes=60))
        self.assertEqual(len(selected), 2)
        with self.assertRaises(ValueError):
            profile_to_dataframe([(datetime(2026, 1, 15), 1.0)])


if __name__ == '__main__':
    unittest.main()
