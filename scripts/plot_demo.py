"""Save Demo 1 plots from the shared CSV and explicit device models."""

import json
from pathlib import Path

from energyhome.analytics import plot_daily_profile, plot_device_stack
from energyhome.dataio import load_demo_profile
from energyhome.domain import CyclicDevice, tariff_from_config


def main() -> None:
    """Reproduce both figures without inventing a breakdown of measured data."""
    root = Path(__file__).resolve().parents[1]
    profile = load_demo_profile(root / 'data/demo/meter_15min.csv')
    if profile is None:
        raise ValueError('The shared demo CSV is empty')
    registry = json.loads((root / 'config/tariffs.json').read_text())['tariffs']
    tariff = tariff_from_config('two_zone', registry)
    period = profile.start_time(), profile.end_time()
    devices = [CyclicDevice('cycle A', 1, 30), CyclicDevice('cycle B', 1, 15, 15),
               CyclicDevice('cycle C', 1, 15, 45)]
    series = {f'Synthetic: {d.device_id}': d.profile_for(period, 15).power_kw for d in devices}
    output = root / 'docs/demo1'
    plot_daily_profile(profile.timestamps, profile.power_kw, tariff, output / 'daily_profile.png')
    plot_device_stack(profile.timestamps, series, profile.power_kw, output / 'device_stack.png')


if __name__ == '__main__':
    main()
