"""Consumption summaries and plots over observed meter intervals."""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from itertools import groupby
from math import isclose, isfinite
from pathlib import Path
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

from .core import (
    CostBreakdown, calculate_cost, compare_tariffs, mean_price,
    merge_cost_breakdowns, profile_metrics,
)
from .dataio import clean_readings
from .domain import LoadProfile, MeterReading, Tariff

if TYPE_CHECKING:
    from matplotlib.figure import Figure


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
    """Known intervals with a common duration; gaps remain unfilled."""

    def __init__(self, timestamps: Sequence[datetime], powers: Sequence[float],
                 resolution: int) -> None:
        self.timestamps = tuple(timestamps)
        self.power_kw = tuple(powers)
        self.resolution_minutes = resolution

    def start_time(self) -> datetime | None:
        """Return the first observed interval start."""
        return self.timestamps[0] if self.timestamps else None

    def end_time(self) -> datetime | None:
        """Return the end of the final observed interval."""
        if not self.timestamps:
            return None
        return self.timestamps[-1] + timedelta(minutes=self.resolution_minutes)

    def __iter__(self) -> Iterator[tuple[datetime, float]]:
        return iter(zip(self.timestamps, self.power_kw))

    def __len__(self) -> int:
        return len(self.timestamps)


def _readings(items: Iterable[MeterReading | tuple[datetime, float]]) -> Iterator[MeterReading]:
    for item in items:
        yield item if isinstance(item, MeterReading) else MeterReading(*item)


def _iter_day_results(readings: Iterable[MeterReading | tuple[datetime, float]],
                      tariff: Tariff, step_minutes: int
                      ) -> Iterator[tuple[SubProfile, DailySummary, CostBreakdown]]:
    zone = ZoneInfo(tariff.timezone_name)
    points = clean_readings(_readings(readings), step_minutes, allow_gaps=True)
    total = calculate_cost(None, tariff)
    for day, group in groupby(points, lambda r: r.timestamp.astimezone(zone).date()):
        timestamps, powers = zip(*((r.timestamp, r.power_kw) for r in group))
        profile = SubProfile(timestamps, powers, step_minutes)
        previous = total
        total = merge_cost_breakdowns((total, calculate_cost(profile, tariff)))
        start = datetime.combine(day, time.min, zone).astimezone(timezone.utc)
        end = datetime.combine(day + timedelta(days=1), time.min, zone)
        minutes = (end.astimezone(timezone.utc) - start).total_seconds() / 60
        summary = DailySummary(
            day, len(profile), round(total.energy_kwh - previous.energy_kwh, 4),
            Decimal(str(total.total_cost)) - Decimal(str(previous.total_cost)),
            round(len(profile) * step_minutes / minutes * 100, 2),
        )
        yield profile, summary, total


def daily_summaries(profile: LoadProfile | SubProfile | None,
                    tariff: Tariff) -> list[DailySummary]:
    """Summarize local days with the same rounding and coverage as the streaming API."""
    if profile is None or len(profile) == 0:
        return []
    return list(iter_daily_summaries(profile, tariff, profile.resolution_minutes))


def iter_daily_summaries(readings: Iterable[MeterReading | tuple[datetime, float]],
                         tariff: Tariff, step_minutes: int = 15) -> Iterator[DailySummary]:
    """Keep one local day in memory and carry final rounding between days."""
    for _, summary, _ in _iter_day_results(readings, tariff, step_minutes):
        yield summary


def weekly_summaries(daily_list: Sequence[DailySummary]) -> list[WeeklySummary]:
    """Sum observed dates into Monday-based calendar weeks."""
    weeks: dict[date, list[DailySummary]] = {}
    seen = set()
    for summary in daily_list:
        if summary.day in seen:
            raise ValueError('Daily summaries must contain unique dates')
        seen.add(summary.day)
        monday = summary.day - timedelta(days=summary.day.weekday())
        weeks.setdefault(monday, []).append(summary)
    return [WeeklySummary(
        len(days), sum(d.interval_count for d in days),
        float(sum((Decimal(str(d.energy_kwh)) for d in days), Decimal(0))),
        sum((d.cost_uah for d in days), Decimal('0.00')),
    ) for _, days in sorted(weeks.items())]


def build_annual_report(readings: Iterable[MeterReading], single: Tariff,
                        two: Tariff | None = None, step_minutes: int = 15,
                        date_from: datetime | None = None,
                        date_to: datetime | None = None) -> str:
    """Report observed intervals in [from, to), without retaining a raw year."""
    if any(bound is not None and bound.utcoffset() is None for bound in (date_from, date_to)):
        raise ValueError('Invalid period: boundaries must be timezone-aware')
    begin = date_from.astimezone(timezone.utc) if date_from is not None else None
    end = date_to.astimezone(timezone.utc) if date_to is not None else None
    if begin is not None and end is not None and begin >= end:
        raise ValueError('Period start must precede its end')
    tariffs = (single,) if two is None else (single, two)
    totals = compare_tariffs(None, tariffs)
    selected = (r for r in clean_readings(readings, step_minutes, allow_gaps=True)
                if (begin is None or r.timestamp >= begin)
                and (end is None or r.timestamp < end))
    days = []
    start, peak, power_sum = None, 0.0, 0.0
    for profile, daily, cost in _iter_day_results(selected, single, step_minutes):
        start = start or profile.start_time()
        totals[single.tariff_id] = cost
        if two is not None:
            totals[two.tariff_id] = merge_cost_breakdowns(
                (totals[two.tariff_id], calculate_cost(profile, two)))
        days.append(daily)
        metrics = profile_metrics(profile, single)
        peak = max(peak, metrics.peak_kw)
        power_sum += metrics.mean_power_kw * len(profile)
    if not days:
        raise ValueError('No data in the selected period')
    count = sum(day.interval_count for day in days)
    mean = power_sum / count
    summary = ReportSummary(start, profile.end_time(), count, cost.energy_kwh,
                            Decimal(str(cost.total_cost)))
    missing = (profile.end_time() - start) // timedelta(minutes=step_minutes) - count
    lines = [format_console_report(summary), f'Пік: {peak:g} kW',
             f'Нерівномірність: {peak / mean if mean else "не визначено"}',
             f'Середня ціна: {mean_price(cost) if mean else "не визначено"} UAH/kWh',
             f'Тарифи (UAH), базовий: {single.tariff_id}:']
    lines.extend(f'{key}: {part.total_cost:.2f}' for key, part in totals.items())
    lines.append(f'Пропущено інтервалів у спостереженому періоді: {missing}')
    lines.append('Добові суми: округлення переноситься між днями.')
    lines.append('Добові підсумки: дата | інтервали | kWh | UAH | покриття')
    lines.extend(f'{d.day} | {d.interval_count} | {d.energy_kwh:.4f} | {d.cost_uah:.2f} | '
                 f'{d.coverage_pct:.2f}%' for d in days)
    lines.append('Тижневі підсумки: дні | інтервали | kWh | UAH')
    lines.extend(f'{w.days_count} | {w.interval_count} | {w.energy_kwh:.4f} | {w.cost_uah:.2f}'
                 for w in weekly_summaries(days))
    return '\n'.join(lines)


def _plot_axes(timestamps: Sequence[datetime], zone: object = timezone.utc) -> tuple:
    from matplotlib.dates import AutoDateLocator, DateFormatter
    from matplotlib.figure import Figure

    fig = Figure(figsize=(10, 4))
    ax = fig.subplots()
    if any(not isinstance(t, datetime) or t.utcoffset() is None for t in timestamps):
        raise ValueError('Plot timestamps must be timezone-aware')
    instants = [t.astimezone(timezone.utc) for t in timestamps]
    if any(a >= b for a, b in zip(instants, instants[1:])):
        raise ValueError('Plot timestamps must be strictly increasing')
    ax.xaxis.set_major_locator(AutoDateLocator(minticks=3, maxticks=8))
    ax.xaxis.set_major_formatter(DateFormatter('%H:%M %z', tz=zone))
    ax.set_xlabel(f'Час ({zone})')
    ax.set_ylabel('Потужність (kW)')
    ax.grid(True, axis='y', linestyle='--', alpha=0.5)
    return fig, ax, instants


def _check_series(values: Sequence[float], count: int) -> list[float]:
    series = list(values)
    if len(series) != count:
        raise ValueError('Plot series and timestamps must have equal lengths')
    if any(isinstance(v, bool) or not isinstance(v, (int, float))
           or not isfinite(v) or v < 0 for v in series):
        raise ValueError('Plot power must be finite and nonnegative')
    return series


def plot_daily_profile(timestamps: Sequence[datetime], powers_kw: Sequence[float],
                        tariff: Tariff | None = None, output_path: Path | str | None = None,
                        *, step_minutes: int = 15) -> Figure:
    """Plot power and interval-start tariff rates on an absolute datetime axis."""
    zone = ZoneInfo(tariff.timezone_name) if tariff is not None else timezone.utc
    fig, ax, instants = _plot_axes(timestamps, zone)
    powers = _check_series(powers_kw, len(instants))
    if type(step_minutes) is not int or step_minutes <= 0:
        raise ValueError('Plot step must be a positive integer')
    ax.set_title('Добовий профіль навантаження')
    if instants:
        ax.plot(instants, powers, label='Потужність (kW)', color='tab:blue', lw=1.5)
        if tariff is not None:
            right = instants[-1] + timedelta(minutes=step_minutes)
            rates = [tariff.rate_at(t) for t in instants]
            rate_axis = ax.twinx()
            rate_axis.step([*instants, right], [*rates, rates[-1]], where='post',
                           color='tab:orange', alpha=0.7)
            rate_axis.set_ylabel('Тариф (UAH/kWh)')
    if output_path is not None:
        fig.savefig(output_path, bbox_inches='tight')
    return fig


def plot_device_stack(timestamps: Sequence[datetime],
                       device_series: Mapping[str, Sequence[float]] | None = None,
                       total_power_kw: Sequence[float] | None = None,
                       output_path: Path | str | None = None,
                       *, step_minutes: int = 15) -> Figure:
    """Stack supplied device profiles; show only total when no breakdown is known."""
    fig, ax, instants = _plot_axes(timestamps)
    ax.set_title('Навантаження приладів')
    if type(step_minutes) is not int or step_minutes <= 0:
        raise ValueError('Plot step must be a positive integer')
    if instants:
        if not device_series and total_power_kw is None:
            raise ValueError('Device profiles or measured total are required')
        series = {label: _check_series(values, len(instants))
                  for label, values in (device_series or {}).items()}
        total = (_check_series(total_power_kw, len(instants))
                 if total_power_kw is not None else None)
        if series and total is not None:
            if any(not isclose(sum(values[i] for values in series.values()), power,
                               rel_tol=1e-9, abs_tol=1e-9) for i, power in enumerate(total)):
                raise ValueError('Device profiles must sum to the measured total')
        if not series:
            series = {'Загальне навантаження (прилади не визначені)': total}
        bottom = [0.0] * len(instants)
        for label, values in series.items():
            ax.bar(instants, values, bottom=bottom, label=label,
                   width=timedelta(minutes=step_minutes), align='edge')
            bottom = [b + v for b, v in zip(bottom, values)]
        ax.legend(loc='upper right')
    if output_path is not None:
        fig.savefig(output_path, bbox_inches='tight')
    return fig
