import argparse
import csv
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from random import Random

from energyhome.dataio import (
    UCI_HOUSEHOLD, QualityReport, iter_clean_readings,
)
from energyhome.domain import (
    CyclicDevice, FixedDevice, MeterReading, ThermostaticDevice,
)

def synthetic_year(year: int, seed: int) -> Iterator[MeterReading]:
    rng = Random(seed)
    base = FixedDevice('base', 0.25)
    boiler = ThermostaticDevice('boiler', 2.0, duty_cycle=0.25)
    washer = CyclicDevice('washer', 1.5, 60, start_minute=19 * 60)
    start = datetime(year, 1, 1, tzinfo=UTC)
    minute = 0
    while (moment := start + timedelta(minutes=minute)).year == year:
        power = sum(d.power_kw for d in (base, boiler)
                    if d.is_on(minute))
        power += washer.power_kw if washer.is_on(minute % 1440) else 0.0
        noise = rng.uniform(-0.02, 0.02)
        yield MeterReading(moment, round(max(power + noise, 0.0), 6))
        minute += 15

def uci_year(path: Path, year: int,
             report: QualityReport) -> Iterator[MeterReading]:
    for reading in iter_clean_readings(path, report, UCI_HOUSEHOLD):
        if reading.timestamp.year > year:
            return
        if reading.timestamp.year == year:
            yield reading

def write_csv(readings: Iterator[MeterReading], out: Path) -> int:
    out.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with out.open('w', encoding='utf-8', newline='') as file:
        writer = csv.writer(file)
        writer.writerow(['timestamp', 'power_kw'])
        for reading in readings:
            stamp = reading.timestamp.strftime('%Y-%m-%dT%H:%M:%SZ')
            writer.writerow([stamp, reading.power_kw])
            count += 1
    return count

def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('source', choices=['synthetic', 'uci'])
    parser.add_argument('--year', type=int, required=True)
    parser.add_argument('--seed', type=int, default=42)
    parser.add_argument('--input', type=Path)
    parser.add_argument('--out', type=Path)
    args = parser.parse_args()
    out = args.out or Path(f'data/generated/{args.source}_{args.year}.csv')
    report = QualityReport()
    if args.source == 'synthetic':
        rows = write_csv(synthetic_year(args.year, args.seed), out)
    else:
        if args.input is None:
            parser.error('uci потребує --input')
        rows = write_csv(uci_year(args.input, args.year, report), out)
    print(f'{out}: {rows} рядків, rejects={len(report.rejects)}, '
          f'gaps={len(report.gaps)}')
    
if __name__ == '__main__':
    main()
