from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo
from pathlib import Path
from typing import Any
from collections.abc import Iterable, Iterator, Mapping, Sequence

from energyhome.domain import LoadProfile, Tariff
from energyhome.core import calculate_cost

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
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
    ]

def _flush_day_summary(
    day: date,
    ts_list: list[datetime],
    p_list: list[float],
    tariff: Tariff,
    step_minutes: int,
) -> DailySummary:
    """Aggregates accumulated points of the day through core.calculate_cost."""
    sub = SubProfile(ts_list, p_list, step_minutes)
    res = calculate_cost(sub, tariff)

    energy = getattr(res, "energy_kwh", None) or getattr(res, "total_energy_kwh", 0.0)
    cost = getattr(res, "total_cost", None) or getattr(res, "total_cost_uah", 0.0)

    expected_count = (24 * 60) // step_minutes
    coverage = round((len(ts_list) / expected_count) * 100, 2)
    return DailySummary(
        day=day,
        interval_count=len(ts_list),
        energy_kwh=float(energy),
        cost_uah=Decimal(str(cost)),
        coverage_pct=coverage,
    )


def iter_daily_summaries(
    readings: Iterable[Any],
    tariff: Tariff,
    step_minutes: int = 15,
) -> Iterator[DailySummary]:
    """
    Stream generator of summaries of the day without accumulating the entire data set in RAM.
    Keeps only the current day in memory, correctly completes the tail with coverage_pct.
    """
    current_day: date | None = None
    cur_ts: list[datetime] = []
    cur_p: list[float] = []

    for item in readings:
        ts = getattr(item, "timestamp", None)
        power = getattr(item, "power_kw", None)
        if ts is None or power is None:
            ts, power = item[0], item[1]

        item_day = ts.date()
        if current_day is not None and item_day != current_day:
            yield _flush_day_summary(current_day, cur_ts, cur_p, tariff, step_minutes)
            cur_ts = []
            cur_p = []

        current_day = item_day
        cur_ts.append(ts)
        cur_p.append(float(power))

    if current_day is not None and cur_ts:
        yield _flush_day_summary(current_day, cur_ts, cur_p, tariff, step_minutes)


def plot_daily_profile(
    timestamps: Sequence[datetime],
    powers_kw: Sequence[float],
    tariff: Tariff | None = None,
    output_path: Path | str | None = None,
) -> Figure:
    """Linear schedule of daily load with tariff zones rate_at."""
    fig, ax = plt.subplots(figsize=(10, 4))
    if not timestamps or not powers_kw:
        ax.set_title("Daily Profile (Empty)")
        ax.set_xlabel("Час (HH:MM)")
        ax.set_ylabel("Потужність (kW)")
        if output_path:
            fig.savefig(output_path, bbox_inches="tight")
        return fig

    x_labels = [dt.strftime("%H:%M") for dt in timestamps]
    ax.plot(x_labels, powers_kw, label="Потужність (kW)", color="tab:blue", lw=1.5)

    if tariff is not None and hasattr(tariff, "rate_at"):
        rates = [tariff.rate_at(dt) for dt in timestamps]
        ax2 = ax.twinx()
        ax2.step(
            x_labels, rates, where="mid", color="tab:orange", alpha=0.6, label="Тариф (грн/кВт·год)"
        )
        ax2.set_ylabel("Тариф (грн/кВт·год)")

    ax.set_title("Добовий профіль навантаження та тарифні зони")
    ax.set_xlabel("Час (HH:MM)")
    ax.set_ylabel("Потужність (kW)")
    ax.grid(True, linestyle="--", alpha=0.5)

    step = max(1, len(x_labels) // 8)
    ax.set_xticks(range(0, len(x_labels), step))
    ax.set_xticklabels([x_labels[i] for i in range(0, len(x_labels), step)])

    if output_path:
        fig.savefig(output_path, bbox_inches="tight")
    return fig


def plot_device_stack(
    timestamps: Sequence[datetime],
    device_series: Mapping[str, Sequence[float]] | None = None,
    total_power_kw: Sequence[float] | None = None,
    output_path: Path | str | None = None,
) -> Figure:
    """Stacked bar instrument graph (or explicitly marked synthetic script)."""
    fig, ax = plt.subplots(figsize=(10, 4))
    if not timestamps:
        ax.set_title("Device Stack (Empty)")
        if output_path:
            fig.savefig(output_path, bbox_inches="tight")
        return fig

    x_labels = [dt.strftime("%H:%M") for dt in timestamps]
    n = len(timestamps)

    if not device_series:
        totals = list(total_power_kw) if total_power_kw is not None else [1.0] * n
        device_series = {
            "Synthetic: Base Load": [p * 0.3 for p in totals],
            "Synthetic: Active Devices": [p * 0.7 for p in totals],
        }

    bottom = [0.0] * n
    for label, series in device_series.items():
        vals = list(series)
        ax.bar(x_labels, vals, bottom=bottom, label=label, width=0.8)
        bottom = [b + v for b, v in zip(bottom, vals)]

    ax.set_title("Розподіл навантаження за приладами (Stacked Bar)")
    ax.set_xlabel("Час (HH:MM)")
    ax.set_ylabel("Потужність (kW)")
    ax.legend(loc="upper right")
    ax.grid(True, axis="y", linestyle="--", alpha=0.5)

    step = max(1, len(x_labels) // 8)
    ax.set_xticks(range(0, len(x_labels), step))
    ax.set_xticklabels([x_labels[i] for i in range(0, len(x_labels), step)])

    if output_path:
        fig.savefig(output_path, bbox_inches="tight")
    return fig
