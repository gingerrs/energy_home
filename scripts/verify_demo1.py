"""Verify normalized UCI 2008 against independent sums and all three CLI tariffs."""

import argparse
import csv
import hashlib
import json
import os
import subprocess
import sys
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]


def independent_totals(path: Path, registry: dict) -> tuple[int, Decimal, dict[str, Decimal]]:
    """Sum raw decimal powers and JSON-defined rates without the production cost engine."""
    count, energy = 0, Decimal(0)
    costs = dict.fromkeys(registry, Decimal(0))
    zones = {key: ZoneInfo(entry['timezone_name']) for key, entry in registry.items()}
    with path.open(encoding='utf-8', newline='') as source:
        for row in csv.DictReader(source):
            timestamp = datetime.fromisoformat(row['timestamp'].replace('Z', '+00:00'))
            if timestamp.utcoffset() is None:
                raise ValueError('Verification input must contain explicit UTC offsets')
            interval_energy = Decimal(row['power_kw']) / 60
            energy += interval_energy
            count += 1
            for key, entry in registry.items():
                if entry['type'] == 'single':
                    rate = entry['rate']
                else:
                    local = timestamp.astimezone(zones[key])
                    minute = local.hour * 60 + local.minute
                    matches = []
                    for zone in entry['zones']:
                        start, end = zone['start_minute'], zone['end_minute']
                        if ((start < end and start <= minute < end)
                                or (start > end and (minute >= start or minute < end))):
                            matches.append(zone['rate'])
                    if len(matches) != 1:
                        raise ValueError(f'Invalid reference tariff coverage: {key}')
                    rate = matches[0]
                costs[key] += interval_energy * Decimal(str(rate))
    return count, energy, costs


def verify(path: Path) -> dict:
    """Check source integrity, CLI totals and sums of displayed daily/weekly rows."""
    metadata = json.loads(path.with_suffix('.quality.json').read_text(encoding='utf-8'))
    if metadata.get('step_minutes') != 1:
        raise ValueError('Expected normalized one-minute UCI data with quality metadata')
    registry = json.loads((ROOT / 'config/tariffs.json').read_text())['tariffs']
    count, energy, costs = independent_totals(path, registry)
    if count != 526845 or metadata['rejects'] != 195 or metadata['gaps'] != 16:
        raise ValueError('UCI 2008 counts differ from the saved source control')
    source_control = json.loads((ROOT / 'data/quality/w4_2008.json').read_text())
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    if digest != source_control['real_runs'][0]['csv_sha256']:
        raise ValueError('Prepared input differs from the verified UCI 2008 CSV')
    results = {}
    expected_energy = energy.quantize(Decimal('.0001'))
    env = dict(os.environ, PYTHONUTF8='1', PYTHONPATH=str(ROOT / 'src'))
    for key in registry:
        command = [sys.executable, '-m', 'energyhome', 'report', '--input', str(path),
                   '--tariffs', str(ROOT / 'config/tariffs.json'), '--tariff', key]
        result = subprocess.run(command, cwd=ROOT, env=env, text=True, encoding='utf-8',
                                capture_output=True, timeout=180)
        if result.returncode or result.stderr:
            raise ValueError(f'CLI {key} failed: {result.stderr}')
        lines = result.stdout.splitlines()
        daily_index = lines.index('Добові підсумки: дата | інтервали | kWh | UAH | покриття')
        weekly_index = lines.index('Тижневі підсумки: дні | інтервали | kWh | UAH')
        days = [line.split(' | ') for line in lines[daily_index+1:weekly_index]]
        weeks = [line.split(' | ') for line in lines[weekly_index+1:]]
        expected_cost = costs[key].quantize(Decimal('.01'), rounding=ROUND_HALF_UP)
        if f'Вартість: {expected_cost:.2f} UAH' not in lines:
            raise ValueError(f'CLI {key} differs from the independent decimal cost')
        if f'Енергія: {expected_energy} kWh' not in lines:
            raise ValueError(f'CLI {key} differs from the independent energy')
        if 'Пропущено інтервалів у спостереженому періоді: 195' not in lines:
            raise ValueError(f'CLI {key} lost missing-interval information')
        for rows in (days, weeks):
            if (sum(int(row[1]) for row in rows) != count
                    or sum(Decimal(row[2]) for row in rows) != expected_energy
                    or sum(Decimal(row[3]) for row in rows) != expected_cost):
                raise ValueError(f'Daily/weekly totals do not match {key}')
        if (len(days), len(weeks)) != (367, 53):
            raise ValueError('Local day/week coverage differs from the saved year')
        if key == 'single' and result.stdout != (ROOT / 'docs/demo1/annual_2008.txt').read_text():
            raise ValueError('The original annual report changed')
        results[key] = dict(cost_uah=str(expected_cost), daily_rows=len(days),
                            weekly_rows=len(weeks), daily_weekly_totals_match=True)
    return dict(input_sha256=digest, intervals=count, energy_kwh=str(expected_energy),
                rejects=metadata['rejects'], gap_events=metadata['gaps'],
                missing_intervals=195, tariffs=results, independent_decimal_check=True,
                original_single_report_unchanged=True)


def main() -> None:
    """Write a machine-readable verification record."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    args.out.write_text(json.dumps(verify(args.input), ensure_ascii=False, indent=2) + '\n',
                        encoding='utf-8')
    print(args.out)


if __name__ == '__main__':
    main()
