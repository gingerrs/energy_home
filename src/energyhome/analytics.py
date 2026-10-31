from __future__ import annotations

import io
from datetime import date, datetime, timedelta
from decimal import Decimal
from dataclasses import FrozenInstanceError
from collections.abc import Iterable, Iterator, Mapping, Sequence
from pathlib import Path
from typing import Any
from energyhome.domain import LoadProfile, Tariff
from energyhome.core import calculate_cost

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.figure import Figure

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