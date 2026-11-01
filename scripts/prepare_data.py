"""Prepare a real UCI year or an explicitly synthetic, reproducible backup."""

import argparse
import csv
import json
from collections import Counter
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from random import Random
from zoneinfo import ZoneInfo

from energyhome.dataio import (
    UCI_HOUSEHOLD, DataImporterError, QualityReport, clean_readings, read_chunks,
)
from energyhome.domain import (
    CyclicDevice, FixedDevice, MeterReading, ThermostaticDevice,
)


def synthetic_year(year: int, seed: int) -> Iterator[MeterReading]:
    """Yield a seeded 15-minute UTC year, including February 29 when present."""
    rng = Random(seed)
    base = FixedDevice('base', 0.25)
    boiler = ThermostaticDevice('boiler', 2.0, duty_cycle=0.25)
    washer = CyclicDevice('washer', 1.5, 60, start_minute=19 * 60)
    start = datetime(year, 1, 1, tzinfo=UTC)
    end = datetime(year + 1, 1, 1, tzinfo=UTC)
    minute = 0
    while (moment := start + timedelta(minutes=minute)) < end:
        power = sum(d.power_kw for d in (base, boiler)
                    if d.is_on(minute))
        power += washer.power_kw if washer.is_on(minute % 1440) else 0.0
        noise = rng.uniform(-0.02, 0.02)
        yield MeterReading(moment, round(max(power + noise, 0.0), 6))
        minute += 15


def uci_year(path: Path, year: int,
             report: QualityReport, chunk_size: int = 1000) -> Iterator[MeterReading]:
    """Clean only the chosen source-calendar year, preserving original physical line numbers."""
    for chunk in read_chunks(path, UCI_HOUSEHOLD, chunk_size, year=year):
        for line, item in chunk:
            report.line = line - 1
            yield from clean_readings((item,), 1, 'reject', report)


def write_csv(readings: Iterator[MeterReading], out: Path,
              summary: dict[str, object] | None = None) -> int:
    """Write one reading at a time and optionally collect observed bounds and an exact sum."""
    out.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    total, first, last = Decimal(0), None, None
    with out.open('w', encoding='utf-8', newline='') as file:
        writer = csv.writer(file)
        writer.writerow(['timestamp', 'power_kw'])
        for reading in readings:
            stamp = reading.timestamp.strftime('%Y-%m-%dT%H:%M:%SZ')
            writer.writerow([stamp, reading.power_kw])
            first, last = first or stamp, stamp
            total += Decimal(str(reading.power_kw))
            count += 1
    if summary is not None:
        summary.update(first_timestamp=first, last_timestamp=last, power_sum_kw=str(total))
    return count


def main() -> None:
    """Prepare CSV plus observed quality metadata without loading the year into memory."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('source', choices=['synthetic', 'uci'])
    parser.add_argument('--year', type=int, required=True)
    parser.add_argument('--seed', type=int, default=42)
    parser.add_argument('--input', type=Path)
    parser.add_argument('--out', type=Path)
    parser.add_argument('--chunk-size', type=int, default=1000)
    args = parser.parse_args()
    if not 1 <= args.year <= 9998 or args.chunk_size <= 0:
        parser.error('year must be 1..9998 and chunk-size must be positive')
    out = args.out or Path(f'data/generated/{args.source}_{args.year}.csv')
    targets = (out.resolve(), out.with_suffix('.quality.json').resolve())
    if args.input and args.input.resolve() in targets:
        parser.error('input must differ from output and quality report')
    report = QualityReport()
    summary = {}
    if args.source == 'synthetic':
        rows = write_csv(synthetic_year(args.year, args.seed), out, summary)
    else:
        if args.input is None:
            parser.error('uci потребує --input')
        rows = write_csv(uci_year(args.input, args.year, report, args.chunk_size), out, summary)
    if not rows:
        raise DataImporterError('No readings in the selected year')
    step, zone = (15, UTC) if args.source == 'synthetic' else (1, ZoneInfo(UCI_HOUSEHOLD.timezone))
    start, end = (datetime(y, 1, 1, tzinfo=zone).astimezone(UTC)
                  for y in (args.year, args.year + 1))
    expected = int((end - start).total_seconds() / (step * 60))
    reasons = dict(Counter(r.reason for r in report.rejects))
    summary.update(source=args.source, year=args.year, chunk_size=args.chunk_size, rows=rows,
                   timezone=str(zone), step_minutes=step,
                   seed=args.seed if args.source == 'synthetic' else None,
                   source_rows=rows + len(report.rejects), rejects=len(report.rejects),
                   gaps=len(report.gaps), reject_reasons=reasons,
                   period_start=start.isoformat(), period_end_exclusive=end.isoformat(),
                   expected_intervals=expected, missing_intervals=expected - rows,
                   energy_kwh=str(Decimal(summary.pop('power_sum_kw')) * step / 60))
    out.with_suffix('.quality.json').write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + '\n', encoding='utf-8')
    print(f'{out}: {rows} рядків, rejects={len(report.rejects)}, '
          f'gaps={len(report.gaps)}')


if __name__ == '__main__':
    try:
        main()
    except (DataImporterError, OSError) as error:
        raise SystemExit(str(error)) from None

