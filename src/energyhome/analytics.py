from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .domain import LoadProfile, Tariff


@dataclass(frozen=True)
class ReportSummary:
    """Consumption and cost for the half-open period [start, end)."""

    period_start: datetime | None
    period_end_exclusive: datetime | None
    interval_count: int
    energy_kwh: float
    cost_uah: Decimal


def build_demo1_summary(profile: LoadProfile | None, tariff: Tariff) -> ReportSummary:
    """Build a summary from domain boundaries and the cost returned by core."""
    interval_count = len(profile) if profile is not None else 0
    if interval_count == 0:
        return ReportSummary(None, None, 0, 0.0, Decimal("0.00"))

    from .core import calculate_cost

    breakdown = calculate_cost(profile, tariff)
    return ReportSummary(
        period_start=profile.start_time(),
        period_end_exclusive=profile.end_time(),
        interval_count=interval_count,
        energy_kwh=breakdown.total_energy_kwh,
        cost_uah=breakdown.total_cost_uah,
    )


def format_console_report(summary: ReportSummary) -> str:
    """Return a console report, including an explicit message for empty data."""
    if summary.interval_count == 0:
        period = "немає даних"
    else:
        if summary.period_start is None or summary.period_end_exclusive is None:
            raise ValueError("A non-empty report must have both period boundaries")
        start = summary.period_start.isoformat()
        end = summary.period_end_exclusive.isoformat()
        period = f"[{start}, {end})"

    return "\n".join(
        (
            f"Період: {period}",
            f"Інтервалів: {summary.interval_count}",
            f"Енергія: {summary.energy_kwh} kWh",
            f"Вартість: {summary.cost_uah:.2f} UAH",
        )
    )
