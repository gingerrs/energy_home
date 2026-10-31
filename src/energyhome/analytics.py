from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

from energyhome.domain import LoadProfile, Tariff


@dataclass(frozen=True, slots=True)
class ReportSummary:
    """Immutable summary of an energy consumption report."""

    period_start: datetime | None
    period_end_exclusive: datetime | None
    interval_count: int
    energy_kwh: float
    cost_uah: Decimal


@dataclass(frozen=True, slots=True)
class DailySummary:
    """Consumption and observed coverage of a local calendar day."""

    day: date
    interval_count: int
    energy_kwh: float
    cost_uah: Decimal
    coverage_pct: float = 100.0


@dataclass(frozen=True, slots=True)
class WeeklySummary:
    """Consumption of the observed days within one Monday-to-Sunday week."""

    days_count: int
    interval_count: int
    energy_kwh: float
    cost_uah: Decimal


def build_demo1_summary(profile: LoadProfile | None, tariff: Tariff) -> ReportSummary:
    """Take energy and cost from core and period boundaries from the profile."""
    if profile is None or len(profile) == 0:
        return ReportSummary(None, None, 0, 0.0, Decimal("0.00"))

    from energyhome.core import calculate_cost

    cost_result = calculate_cost(profile, tariff)
    return ReportSummary(
        period_start=profile.start_time(),
        period_end_exclusive=profile.end_time(),
        interval_count=len(profile),
        energy_kwh=float(cost_result.energy_kwh),
        cost_uah=Decimal(str(cost_result.total_cost)),
    )


def format_console_report(summary: ReportSummary) -> str:
    """Format a report with an exclusive end boundary and two money decimals."""
    if summary.interval_count > 0 and (
        summary.period_start is None or summary.period_end_exclusive is None
    ):
        raise ValueError("Invalid period boundaries")
    if (summary.period_start is None) != (summary.period_end_exclusive is None):
        raise ValueError("Invalid period boundaries")
    if summary.period_start is None:
        period_str = "немає даних"
    else:
        period_str = (
            f"[{summary.period_start.isoformat()}, "
            f"{summary.period_end_exclusive.isoformat()})"
        )
    return (
        f"Період: {period_str}\n"
        f"Інтервалів: {summary.interval_count}\n"
        f"Енергія: {summary.energy_kwh} kWh\n"
        f"Вартість: {summary.cost_uah:.2f} UAH"
    )


class SubProfile:
    """Observed intervals of a day, without filling gaps with invented readings."""

    def __init__(self, timestamps, powers, resolution):
        self.timestamps = timestamps
        self.power_kw = powers
        self.resolution_minutes = resolution

    def start_time(self):
        return self.timestamps[0] if self.timestamps else None

    def end_time(self):
        if not self.timestamps:
            return None
        return self.timestamps[-1] + timedelta(minutes=self.resolution_minutes)

    def __iter__(self):
        return iter(zip(self.timestamps, self.power_kw))

    def __len__(self):
        return len(self.timestamps)


def daily_summaries(profile: LoadProfile | None, tariff: Tariff) -> list[DailySummary]:
    """Group interval starts by the tariff's local date, accounting for DST."""
    if profile is None or len(profile) == 0:
        return []
    zone = ZoneInfo(tariff.timezone_name)
    resolution = profile.resolution_minutes
    daily_data = {}
    for timestamp, power in profile:
        day = timestamp.astimezone(zone).date()
        daily_data.setdefault(day, []).append((timestamp, power))

    results = []
    for day, readings in sorted(daily_data.items()):
        timestamps, powers = zip(*readings)
        summary = build_demo1_summary(SubProfile(timestamps, powers, resolution), tariff)
        start = datetime.combine(day, time.min, zone).astimezone(timezone.utc)
        end = datetime.combine(day + timedelta(days=1), time.min, zone)
        day_minutes = (end.astimezone(timezone.utc) - start).total_seconds() / 60
        coverage_pct = round(summary.interval_count * resolution / day_minutes * 100, 2)
        results.append(DailySummary(
            day=day,
            interval_count=summary.interval_count,
            energy_kwh=summary.energy_kwh,
            cost_uah=summary.cost_uah,
            coverage_pct=coverage_pct,
        ))
    return results


def weekly_summaries(daily_list: list[DailySummary]) -> list[WeeklySummary]:
    """Sum daily reports within calendar weeks without recalculating core."""
    weeks = {}
    for summary in daily_list:
        monday = summary.day - timedelta(days=summary.day.weekday())
        weeks.setdefault(monday, []).append(summary)
    return [
        WeeklySummary(
            days_count=len(days),
            interval_count=sum(day.interval_count for day in days),
            energy_kwh=sum(day.energy_kwh for day in days),
            cost_uah=sum((day.cost_uah for day in days), Decimal("0.00")),
        )
        for _, days in sorted(weeks.items())
    ]
