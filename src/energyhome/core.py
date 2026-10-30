from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP

@dataclass(frozen=True)
class CostBreakdown:
    energy_kwh: float
    total_cost: float
    average_price: float
    zone_energy: dict[str, float]
    zone_cost: dict[str, float]

def calculate_cost(profile, tariff) -> CostBreakdown:
    if len(profile) == 0:
        return CostBreakdown(
            energy_kwh=0.0,
            total_cost=0.0,
            average_price=0.0,
            zone_energy={"single": 0.0},
            zone_cost={"single": 0.0}
        )
    hours_step = profile.resolution_minutes / 60.0
    total_energy_raw = 0.0
    total_cost_raw = 0.0
    for timestamp, power_kw in profile:
        interval_energy = power_kw * hours_step
        total_energy_raw += interval_energy
        rate = tariff.rate_at(timestamp)
        total_cost_raw += interval_energy * rate
    total_cost_dec = Decimal(str(total_cost_raw)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    total_cost = float(total_cost_dec)
    total_energy = round(total_energy_raw, 4)
    avg_price = round(total_cost / total_energy, 4) if total_energy > 0 else 0.0

    return CostBreakdown(
        energy_kwh=total_energy,
        total_cost=total_cost,
        average_price=avg_price,
        zone_energy={"single": total_energy},
        zone_cost={"single": total_cost}
    )