from __future__ import annotations

import io
from datetime import datetime, timedelta
from decimal import Decimal
from dataclasses import FrozenInstanceError
from energyhome.domain import LoadProfile, Tariff

class ReportSummary:
    """Immutable summary of an energy consumption report."""
    __slots__ = ("_period_start", "_period_end_exclusive", "_interval_count", "_energy_kwh", "_cost_uah")

    def __init__(
        self,
        period_start: datetime | None,
        period_end_exclusive: datetime | None,
        interval_count: int,
        energy_kwh: float,
        cost_uah: Decimal,
    ):
        object.__setattr__(self, "_period_start", period_start)
        object.__setattr__(self, "_period_end_exclusive", period_end_exclusive)
        object.__setattr__(self, "_interval_count", interval_count)
        object.__setattr__(self, "_energy_kwh", energy_kwh)
        object.__setattr__(self, "_cost_uah", cost_uah)

    @property
    def period_start(self) -> datetime | None:
        return self._period_start

    @property
    def period_end_exclusive(self) -> datetime | None:
        return self._period_end_exclusive

    @property
    def interval_count(self) -> int:
        return self._interval_count

    @property
    def energy_kwh(self) -> float:
        return self._energy_kwh

    @property
    def cost_uah(self) -> Decimal:
        return self._cost_uah

    def __setattr__(self, key, value):
        raise FrozenInstanceError("ReportSummary is immutable")

    def __eq__(self, other):
        if not isinstance(other, ReportSummary):
            return False
        return (
            self.period_start == other.period_start
            and self.period_end_exclusive == other.period_end_exclusive
            and self.interval_count == other.interval_count
            and self.energy_kwh == other.energy_kwh
            and self.cost_uah == other.cost_uah
        )

class DailySummary:
    """Summary of energy consumption for a single calendar day."""
    def __init__(self, day, interval_count: int, energy_kwh: float, cost_uah: Decimal, coverage_pct: float = 100.0):
        self.day = day
        self.interval_count = interval_count
        self.energy_kwh = energy_kwh
        self.cost_uah = cost_uah
        self.coverage_pct = coverage_pct

    def __eq__(self, other):
        if not isinstance(other, DailySummary):
            return False
        return (
            self.day == other.day
            and self.interval_count == other.interval_count
            and self.energy_kwh == other.energy_kwh
            and self.cost_uah == other.cost_uah
            and self.coverage_pct == other.coverage_pct
        )

class WeeklySummary:
    """Summary of energy consumption for a week."""
    def __init__(self, days_count: int, interval_count: int, energy_kwh: float, cost_uah: Decimal):
        self.days_count = days_count
        self.interval_count = interval_count
        self.energy_kwh = energy_kwh
        self.cost_uah = cost_uah

    def __eq__(self, other):
        if not isinstance(other, WeeklySummary):
            return False
        return (
            self.days_count == other.days_count
            and self.interval_count == other.interval_count
            and self.energy_kwh == other.energy_kwh
            and self.cost_uah == other.cost_uah
        )

def build_demo1_summary(profile: LoadProfile | None, tariff: Tariff) -> ReportSummary:
    """Build a report summary from a load profile and a tariff."""
    if profile is None or len(profile) == 0:
        return ReportSummary(None, None, 0, 0.0, Decimal("0.00"))

    from energyhome.core import calculate_cost

    cost_result = calculate_cost(profile, tariff)
    start = profile.start_time()
    end = profile.end_time()
    return ReportSummary(
        period_start=start,
        period_end_exclusive=end,
        interval_count=len(profile),
        energy_kwh=float(cost_result.total_energy_kwh),
        cost_uah=Decimal(str(cost_result.total_cost_uah)),
    )

def format_console_report(summary: ReportSummary) -> str:
    """Format the report summary as a human-readable console string."""
    if summary.interval_count > 0 and (summary.period_start is None or summary.period_end_exclusive is None):
        raise ValueError("Invalid period boundaries")
    if (summary.period_start is None) != (summary.period_end_exclusive is None):
        raise ValueError("Invalid period boundaries")
    if summary.period_start is None or summary.period_end_exclusive is None:
        period_str = "немає даних"
    else:
        period_str = f"[{summary.period_start.isoformat()}, {summary.period_end_exclusive.isoformat()}]"
    cost_str = f"{summary.cost_uah:.2f}"
    return (
        f"Період: {period_str}\n"
        f"Інтервалів: {summary.interval_count}\n"
        f"Енергія: {summary.energy_kwh} kWh\n"
        f"Вартість: {cost_str} UAH"
    )

class SubProfile:
    """Helper container representing a sub-profile for a specific day."""
    def __init__(self, timestamps, powers, resolution):
        self.timestamps = timestamps
        self.power_kw = powers
        self.resolution_minutes = resolution

    def start_time(self):
        return self.timestamps[0] if self.timestamps else None

    def end_time(self):
        return self.timestamps[-1] + timedelta(minutes=self.resolution_minutes) if self.timestamps else None

    def __iter__(self):
        return iter(zip(self.timestamps, self.power_kw))

    def __len__(self):
        return len(self.timestamps)

def daily_summaries(profile, tariff) -> list[DailySummary]:
    """Compute daily consumption summaries grouped by local date."""
    if not profile:
        return []
    timestamps = getattr(profile, "timestamps", None)
    powers = getattr(profile, "power_kw", None)
    if timestamps is None or powers is None:
        try:
            pairs = list(profile)
            if not pairs:
                return []
            timestamps, powers = zip(*pairs)
        except Exception:
            return []
    if not timestamps:
        return []
    daily_data = {}
    for ts, power in zip(timestamps, powers):
        day = ts.date()
        if day not in daily_data:
            daily_data[day] = {"timestamps": [], "powers": []}
        daily_data[day]["timestamps"].append(ts)
        daily_data[day]["powers"].append(power)
    results = []
    resolution = getattr(profile, "resolution_minutes", 15)
    for day, data in sorted(daily_data.items()):
        sub_profile = SubProfile(data["timestamps"], data["powers"], resolution)
        summary = build_demo1_summary(sub_profile, tariff)
        expected_intervals_per_day = 24 * 60 // resolution
        coverage_pct = round((summary.interval_count / expected_intervals_per_day) * 100, 2)
        results.append(
            DailySummary(
                day=day,
                interval_count=summary.interval_count,
                energy_kwh=summary.energy_kwh,
                cost_uah=summary.cost_uah,
                coverage_pct=coverage_pct,
            )
        )
    return results

def weekly_summaries(daily_list: list[DailySummary]) -> list[WeeklySummary]:
    """Aggregate daily summaries into weekly summaries."""
    if not daily_list:
        return []
    total_days = len(daily_list)
    total_intervals = sum(d.interval_count for d in daily_list)
    total_energy = sum(d.energy_kwh for d in daily_list)
    total_cost = sum(d.cost_uah for d in daily_list)
    return [
        WeeklySummary(
            days_count=total_days,
            interval_count=total_intervals,
            energy_kwh=total_energy,
            cost_uah=total_cost,
        )
    ]