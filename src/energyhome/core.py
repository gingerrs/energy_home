from collections.abc import Iterable
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime
from fractions import Fraction
from math import floor, isfinite

from energyhome.domain import LoadProfile, Tariff


@dataclass(frozen=True)
class CostBreakdown:
    energy_kwh: float
    total_cost: float
    average_price: float
    zone_energy: dict[str, float]
    zone_cost: dict[str, float]
    _raw_energy: dict[str, Fraction] | None = field(default=None, repr=False, compare=False)
    _raw_cost: dict[str, Fraction] | None = field(default=None, repr=False, compare=False)
    _tariff_config: dict[str, object] | None = field(default=None, repr=False, compare=False)


def _money(value: Fraction) -> float:
    """Round nonnegative UAH half up to cents, without intermediate float rounding."""
    return floor(value * 100 + Fraction(1, 2)) / 100


def _breakdown(energy: dict[str, Fraction], costs: dict[str, Fraction],
               tariff_config: dict[str, object] | None) -> CostBreakdown:
    """Build a display result while retaining exact values for later reducers."""
    total_energy, total_cost = round(float(sum(energy.values())), 4), _money(sum(costs.values()))
    return CostBreakdown(
        total_energy, total_cost,
        round(total_cost / total_energy, 4) if total_energy else 0.0,
        {zone: round(float(value), 4) for zone, value in energy.items()} or {'single': 0.0},
        {zone: _money(value) for zone, value in costs.items()} or {'single': 0.0},
        _raw_energy=dict(energy), _raw_cost=dict(costs), _tariff_config=tariff_config,
    )


def calculate_cost(profile: LoadProfile | None, tariff: Tariff) -> CostBreakdown:
    """Calculate observed interval costs through the tariff API; never round chunks early."""
    energy, costs = {}, {}
    if profile is not None and len(profile):
        step = Fraction(profile.resolution_minutes, 60)
        for timestamp, power_kw in profile:
            zone = tariff.zone_at(timestamp) if hasattr(tariff, 'zone_at') else 'single'
            value = Fraction(str(power_kw)) * step
            energy[zone] = energy.get(zone, Fraction(0)) + value
            cost = value * Fraction(str(tariff.rate_at(timestamp)))
            costs[zone] = costs.get(zone, Fraction(0)) + cost
    return _breakdown(energy, costs, deepcopy(vars(tariff)) | {'__type__': type(tariff)})


@dataclass(frozen=True)
class ProfileMetrics:
    """Unevenness uses the provisional, not lecturer-approved, peak/mean formula."""

    peak_kw: float
    mean_power_kw: float
    unevenness: float | None
    mean_price: float | None


def peak_kw(profile: LoadProfile | None) -> float:
    """Return the observed peak in kW, or zero for an empty profile."""
    return max((power for _, power in profile), default=0.0) if profile is not None else 0.0


def mean_price(cost: CostBreakdown) -> float | None:
    """Return energy-weighted UAH/kWh before money rounding; zero energy gives None."""
    energy = sum(cost._raw_energy.values()) if cost._raw_energy is not None else cost.energy_kwh
    value = sum(cost._raw_cost.values()) if cost._raw_cost is not None else cost.total_cost
    return float(value / energy) if energy else None


def profile_metrics(profile: LoadProfile | None, tariff: Tariff) -> ProfileMetrics:
    """Return peak, mean power, provisional peak/mean, and weighted mean price."""
    count = len(profile) if profile is not None else 0
    mean = sum(power for _, power in profile) / count if count else 0.0
    peak = peak_kw(profile)
    return ProfileMetrics(peak, mean, peak / mean if mean else None,
                          mean_price(calculate_cost(profile, tariff)))


def compare_tariffs(profile: LoadProfile | None,
                    tariffs: Iterable[Tariff]) -> dict[str, CostBreakdown]:
    """Map unique tariff IDs to DTOs calculated by the same cost engine."""
    results = {}
    for tariff in tariffs:
        if tariff.tariff_id in results:
            raise ValueError('Tariff IDs must be unique')
        results[tariff.tariff_id] = calculate_cost(profile, tariff)
    return results


def merge_cost_breakdowns(parts: Iterable[CostBreakdown]) -> CostBreakdown:
    """Reduce exact totals of matching tariff snapshots in one pass, ignoring empty parts."""
    energy, costs, tariff_config = {}, {}, None
    for part in parts:
        if part._raw_energy is None or part._raw_cost is None:
            raise ValueError('Cannot merge rounded-only CostBreakdown values')
        if not part._raw_energy:
            continue
        if tariff_config is not None and part._tariff_config != tariff_config:
            raise ValueError('Cannot merge different tariff configurations')
        tariff_config = part._tariff_config
        for zone, value in part._raw_energy.items():
            energy[zone] = energy.get(zone, Fraction(0)) + value
        for zone, value in part._raw_cost.items():
            costs[zone] = costs.get(zone, Fraction(0)) + value
    return _breakdown(energy, costs, tariff_config)


@dataclass(frozen=True)
class Event:
    timestamp: datetime
    device_id: str
    event_type: str

def detect_events(
    profile,
    on_threshold: float,
    off_threshold: float,
    label: str = "device",
) -> tuple[Event, ...]:
    if any(isinstance(value, bool) or not isinstance(value, (int, float))
           or not isfinite(value) for value in (on_threshold, off_threshold)):
        raise ValueError("Thresholds must be finite numbers")
    if on_threshold <= off_threshold:
        raise ValueError(
            f"Некоректні пороги: on_threshold ({on_threshold}) "
            f"має бути більшим за off_threshold ({off_threshold})"
        )

    events: list[Event] = []
    is_on = False

    for timestamp, power_kw in profile:
        if not is_on and power_kw >= on_threshold:
            is_on = True
            events.append(Event(timestamp=timestamp, device_id=label, event_type="on"))
        elif is_on and power_kw <= off_threshold:
            is_on = False
            events.append(Event(timestamp=timestamp, device_id=label, event_type="off"))

    return tuple(events)
